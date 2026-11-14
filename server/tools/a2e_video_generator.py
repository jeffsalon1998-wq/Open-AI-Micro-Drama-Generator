"""a2e.ai image-to-video client.

API surface implemented (all async, polled to terminal state):

    POST   /v1/userImage2Video/start          -> create task, returns _id
    GET    /v1/userImage2Video/{_id}          -> task detail (source of truth)
    GET    /v1/userImage2Video/allRecords     -> list tasks
    DELETE /v1/userImage2Video/{_id}          -> delete task
    POST   /v1/generation/quote               -> exact coin cost, free, no task
    POST   /v1/r2/upload-presigned-url        -> presigned PUT + public CDN URL

Why this provider matters for micro-drama:

* ``a2e-v2`` renders a single continuous 24 fps clip up to 20s (no 5s chunking),
  so a whole 15-second retention beat can be one generation instead of three cuts.
* ``auto_add_audio`` on v2 gives native audio (no ThinkSound surcharge), which is
  what vertical drama needs - dialogue, slaps, and stings are the hook.
* ``reference-to-video`` takes character reference images instead of a first frame,
  which holds identity across shots better than re-rolling an I2V frame.
* ``FLF2V`` (first-last-frame) lets us drive an exact opening and closing frame -
  ideal for cliffhanger cuts where the final frame must land precisely.

Contract details taken from the published OpenAPI spec (not guessed):

* ``current_status`` is a closed vocabulary: initialized | sent | pending |
  processing | completed | failed. Only ``completed`` and ``failed`` are terminal.
* A ``429`` on the detail route means the *query budget* is exhausted and carries
  ``Retry-After``; it is not a task failure, so we back off instead of aborting.
* ``extend_prompt`` defaults to **true**, which lets the backend rewrite the
  prompt. Our reference prompts are hand-authored beat sheets with costume and
  identity locks, so ``A2E_EXTEND_PROMPT`` defaults to false here.
* ``/v1/generation/quote`` is free and creates nothing; use it to price a render
  before spending. ``coins`` is the authoritative cost, not a coins/second guess.
* ``/v1/r2/upload-presigned-url`` means a2e *does* accept our own files: ask for
  a presigned PUT, upload the raw bytes unauthenticated, then use ``cdnUrl``.
* Failures expose ``hasRefundCoin`` / ``refund_pending``. A failed render does not
  prove the coins came back, so both are surfaced on the exception.

Docs caveat: the vendor example sends ``generation_mode: reference-to-video`` with
``aspect_ratio: 16:9`` while the prompt asks for a 3:4 portrait. We treat
``generation_params.aspect_ratio`` as authoritative and never inherit that mismatch.
"""
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

DEFAULT_BASE_URL = "https://api.a2e.ai"

# OpenAPI: GenerationVideoTaskLegacy.current_status is a closed enum and only
# "completed" / "failed" are terminal. The extra spellings are defensive only;
# anything unknown is polled through rather than treated as a failure.
_NON_TERMINAL_STATUSES = {"initialized", "sent", "pending", "processing", "queued", "running"}
_FAILURE_STATUSES = {"failed", "failure", "error", "cancelled", "canceled", "expired", "rejected"}
_SUCCESS_STATUSES = {"completed", "complete", "success", "succeeded", "done", "finished"}

_REFERENCE_DURATIONS = (3, 4, 5, 10, 15)
_TEXT_DURATIONS = (5, 10, 15)
_MIN_DURATION, _MAX_DURATION = 3, 20
_MAX_PROMPT_V2 = 2000

# reference_assets: at most 9 images (12 assets total, including video/audio).
_MAX_REFERENCE_IMAGES = 9

QUOTE_ENDPOINT = "/v1/userImage2Video/start"
_MAX_RETRY_AFTER = 120.0


class A2EError(RuntimeError):
    """Raised for non-retryable a2e failures (bad payload, rejected task)."""


class A2ETaskFailed(A2EError):
    """Raised when a submitted task reaches a terminal failure state.

    ``has_refund`` mirrors the platform's ``hasRefundCoin`` flag: True means the
    refund was recorded, False means it explicitly was not, None means unknown.
    ``refund_pending`` takes precedence — coins are still in limbo.
    """

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        code: Any = None,
        reason: Any = None,
        has_refund: Optional[bool] = None,
        refund_pending: Optional[bool] = None,
        coins: Any = None,
    ):
        super().__init__(message)
        self.task_id = task_id
        self.code = code
        self.reason = reason
        self.has_refund = has_refund
        self.refund_pending = refund_pending
        self.coins = coins

    @property
    def refund_state(self) -> str:
        if self.refund_pending:
            return "pending"
        if self.has_refund is True:
            return "refunded"
        if self.has_refund is False:
            return "not refunded"
        return "unknown"


@dataclass
class VideoResult:
    """Provider-neutral result so pipelines don't care which engine ran."""

    url: str
    provider: str
    task_id: str = ""
    duration_seconds: Optional[float] = None
    has_audio: bool = False
    model_version: str = ""
    cover_url: str = ""
    coins: Optional[float] = None  # coins actually charged, from the task record
    raw: Dict[str, Any] = field(default_factory=dict)


class A2EVideoGenerator:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        poll_interval: float = 3.0,
        timeout: int = 900,
        max_submit_retries: int = 3,
        model_version: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("A2E_API_KEY", "")
        self.base_url = (base_url or os.environ.get("A2E_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.poll_interval = float(os.environ.get("A2E_POLL_INTERVAL", poll_interval))
        self.timeout = int(os.environ.get("A2E_TIMEOUT", timeout))
        self.max_submit_retries = max_submit_retries
        self.model_version = model_version or os.environ.get("A2E_MODEL_VERSION", "a2e-v2")
        self.default_duration = int(os.environ.get("A2E_DEFAULT_DURATION", "15"))
        # The API defaults extend_prompt to true, which lets the backend rewrite
        # the prompt. Retention prompts are hand-authored beat sheets carrying the
        # identity and costume locks, so we opt out by default and only allow it
        # when a caller explicitly asks for a looser prompt.
        self.extend_prompt = _env_bool("A2E_EXTEND_PROMPT", False)

    # ------------------------------------------------------------------ util
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _require_key(self) -> None:
        if not self.api_key:
            raise A2EError(
                "A2E_API_KEY is not set. Add it to server/.env, or set VIDEO_PROVIDER=muapi."
            )

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _task_name(prefix: str = "microdrama") -> str:
        """GUI-style timestamped task name, e.g. 11-14-2026 10:49:09."""
        from datetime import datetime
        return f"{prefix} {datetime.now().strftime('%m-%d-%Y %H:%M:%S')}"

    @staticmethod
    def _task_id_of(payload: Dict[str, Any]) -> str:
        data = payload.get("data") or {}
        task_id = data.get("_id") or data.get("id") or payload.get("_id")
        if not task_id:
            raise A2EError(f"a2e returned no task id: {payload}")
        return str(task_id)

    @staticmethod
    def _clamp_time(seconds: int, allowed: Optional[tuple] = None) -> int:
        seconds = int(seconds)
        if allowed:
            if seconds not in allowed:
                # Snap to the nearest allowed value rather than hard-failing a render.
                seconds = min(allowed, key=lambda v: abs(v - seconds))
            return seconds
        return max(_MIN_DURATION, min(_MAX_DURATION, seconds))

    @staticmethod
    def _safe_prompt(prompt: str, limit: int = _MAX_PROMPT_V2) -> str:
        prompt = (prompt or "").strip()
        if len(prompt) <= limit:
            return prompt
        # Trim on a sentence/line boundary so the tail (often the exit frame note) survives.
        return prompt[:limit].rsplit("\n", 1)[0][:limit] if "\n" in prompt[:limit] else prompt[:limit]

    # ------------------------------------------------------------- submit/poll
    async def _submit(self, payload: Dict[str, Any]) -> str:
        self._require_key()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_submit_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    resp = await client.post(
                        f"{self.base_url}/v1/userImage2Video/start",
                        headers=self._headers,
                        json=payload,
                    )
            except httpx.HTTPError as exc:  # network blips are retryable
                last_error = exc
                if attempt < self.max_submit_retries:
                    await asyncio.sleep(2 * attempt)
                    continue
                raise A2EError(f"a2e submit failed after {attempt} attempts: {exc}") from exc

            if resp.status_code == 401:
                raise A2EError("a2e rejected the token (401). Check A2E_API_KEY.")
            if resp.status_code == 403:
                body = _safe_json(resp)
                code = body.get("code") if isinstance(body, dict) else None
                hint = (
                    " API-token requests cannot use unlimited=true (UNLIMITED_WEB_ONLY)."
                    if code == "UNLIMITED_WEB_ONLY" or "UNLIMITED_WEB_ONLY" in resp.text
                    else ""
                )
                raise A2EError(f"a2e forbade the request (403).{hint} {resp.text[:300]}")
            if resp.status_code == 400:
                raise A2EError(f"a2e rejected the payload (400): {resp.text[:400]}")
            if resp.status_code >= 500 and attempt < self.max_submit_retries:
                last_error = A2EError(f"a2e server error {resp.status_code}")
                await asyncio.sleep(2 * attempt)
                continue

            body = _safe_json(resp)
            if resp.status_code != 200:
                raise A2EError(f"a2e submit failed ({resp.status_code}): {resp.text[:300]}")
            if isinstance(body, dict) and body.get("code") not in (0, None):
                raise A2EError(f"a2e submit rejected: code={body.get('code')} msg={body.get('msg')}")
            return self._task_id_of(body if isinstance(body, dict) else {})

        raise A2EError(f"a2e submit failed: {last_error}")

    async def _fetch(self, task_id: str) -> Dict[str, Any]:
        """One detail read.

        Returns ``{"__retry_after": n}`` instead of raising when the API says the
        poll budget is exhausted (429): a rate-limited *read* says nothing about
        the render, and treating it as failure would throw away a paid task.
        """
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.base_url}/v1/userImage2Video/{task_id}",
                headers=self._headers,
            )
        if resp.status_code == 401:
            raise A2EError("a2e rejected the token (401) while polling.")
        if resp.status_code == 404:
            raise A2EError(f"a2e task {task_id} not found (404).")
        if resp.status_code == 429:
            return {"__retry_after": _retry_after(resp, self.poll_interval)}
        if resp.status_code == 503:
            # Documented transient; same treatment as 429 without the header.
            return {"__retry_after": _retry_after(resp, self.poll_interval * 2)}
        if resp.status_code != 200:
            raise A2EError(f"a2e poll failed ({resp.status_code}): {resp.text[:300]}")
        body = _safe_json(resp)
        if not isinstance(body, dict):
            raise A2EError(f"a2e poll returned non-JSON: {resp.text[:200]}")
        if body.get("code") not in (0, None):
            raise A2EError(f"a2e poll rejected: code={body.get('code')} msg={body.get('msg')}")
        return body.get("data") or {}

    async def _poll_until_done(self, task_id: str) -> Dict[str, Any]:
        """Poll task detail until terminal. Detail API is the source of truth."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.timeout
        last_status = ""

        while loop.time() < deadline:
            await asyncio.sleep(self.poll_interval)
            data = await self._fetch(task_id)

            if "__retry_after" in data:
                # Rate-limited read, not a task state. Back off and keep the task.
                wait = float(data["__retry_after"])
                print(f"[a2e] task {task_id}: poll throttled, retrying in {wait:g}s")
                await asyncio.sleep(wait)
                continue

            status = str(data.get("current_status") or data.get("status") or "").lower()
            if status != last_status:
                print(f"[a2e] task {task_id}: {status or 'unknown'}")
                last_status = status

            if status in _FAILURE_STATUSES:
                raise A2ETaskFailed(
                    f"a2e task {task_id} failed: "
                    f"{data.get('failed_message') or data.get('failed_reason') or 'unknown error'}"
                    f" (refund: {_refund_note(data)})",
                    task_id=task_id,
                    code=data.get("failed_code"),
                    reason=data.get("failed_reason"),
                    has_refund=data.get("hasRefundCoin"),
                    refund_pending=data.get("refund_pending"),
                    coins=data.get("coins"),
                )

            media = _extract_media(data)
            if media or status in _SUCCESS_STATUSES:
                if not media:
                    raise A2EError(f"a2e task {task_id} completed without a media URL: {data}")
                return data

            if status and status not in _NON_TERMINAL_STATUSES:
                # Unknown state: keep polling (forward compatible), but say so once.
                if status != last_status:
                    print(f"[a2e] task {task_id}: unrecognised status {status!r}, continuing to poll")

        raise TimeoutError(
            f"a2e task {task_id} did not finish within {self.timeout}s. "
            f"The task may still complete - check {self.base_url}/v1/userImage2Video/{task_id} "
            f"before re-submitting, or you will pay twice."
        )

    # ------------------------------------------------------------- public API
    async def generate_image_to_video(
        self,
        prompt: str,
        image_url: str,
        video_time: int = 5,
        aspect_ratio: str = "9:16",
        negative_prompt: Optional[str] = None,
        auto_add_audio: Optional[bool] = None,
        name: str = "",
        model_version: Optional[str] = None,
    ) -> VideoResult:
        """Animate a single still. ``video_time`` 3-20s on a2e-v2."""
        if not image_url:
            raise A2EError("image_url is required for image-to-video")
        model = model_version or self.model_version
        duration = self._clamp_time(video_time or self.default_duration)
        payload: Dict[str, Any] = {
            "name": name or self._task_name(),
            "model_version": model,
            "generation_mode": "image-to-video",
            "image_url": image_url,
            "prompt": self._safe_prompt(prompt),
            "video_time": duration,
            "number_of_images": 1,
            "extend_prompt": self.extend_prompt,
            "generation_params": {"aspect_ratio": aspect_ratio},
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if auto_add_audio is not None:
            payload["auto_add_audio"] = auto_add_audio
        return await self._run(payload, has_audio=bool(payload.get("auto_add_audio")))

    async def generate_reference_to_video(
        self,
        prompt: str,
        reference_urls: List[str],
        video_time: Optional[int] = None,
        aspect_ratio: str = "9:16",
        negative_prompt: Optional[str] = None,
        auto_add_audio: Optional[bool] = None,
        name: str = "",
        model_version: Optional[str] = None,
    ) -> VideoResult:
        """Condition on character reference images (identity lock) rather than a first frame."""
        urls = [u for u in reference_urls if u]
        if not urls:
            raise A2EError("reference-to-video requires at least one reference asset")
        if len(urls) > _MAX_REFERENCE_IMAGES:
            # Spec: at most 9 images (12 assets counting video/audio). Truncating
            # silently would quietly change the identity lock, so say so.
            print(f"[a2e] {len(urls)} reference images supplied; using the first "
                  f"{_MAX_REFERENCE_IMAGES} (API maximum).")
            urls = urls[:_MAX_REFERENCE_IMAGES]
        model = model_version or self.model_version
        if model not in ("a2e-v2", "a2e-v2-flash"):
            raise A2EError("reference-to-video requires model_version a2e-v2 or a2e-v2-flash")
        if not (prompt or "").strip():
            raise A2EError("reference-to-video requires a non-empty prompt")

        duration = self._clamp_time(video_time or self.default_duration, _REFERENCE_DURATIONS)
        payload: Dict[str, Any] = {
            "name": name or self._task_name(),
            "model_version": model,
            "generation_mode": "reference-to-video",
            "prompt": self._safe_prompt(prompt),
            "video_time": duration,
            "number_of_images": 1,
            "extend_prompt": self.extend_prompt,
            "generation_params": {
                "aspect_ratio": aspect_ratio,
                "reference_assets": [
                    {"asset_id": f"image_{i + 1}", "type": "image", "url": u}
                    for i, u in enumerate(urls)
                ],
            },
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if auto_add_audio is not None:
            payload["auto_add_audio"] = auto_add_audio
        return await self._run(payload, has_audio=bool(payload.get("auto_add_audio")))

    async def generate_first_last_frame(
        self,
        prompt: str,
        image_url: str,
        end_image_url: str,
        video_time: Optional[int] = None,
        aspect_ratio: str = "9:16",
        negative_prompt: Optional[str] = None,
        auto_add_audio: Optional[bool] = None,
        name: str = "",
        model_version: Optional[str] = None,
    ) -> VideoResult:
        """FLF2V: drive an exact opening *and* closing frame — use for cliffhanger cuts."""
        if not image_url or not end_image_url:
            raise A2EError("FLF2V requires both image_url and end_image_url")
        duration = self._clamp_time(video_time or self.default_duration)
        payload: Dict[str, Any] = {
            "name": name or self._task_name(),
            "model_version": model_version or self.model_version,
            "model_type": "FLF2V",
            "generation_mode": "image-to-video",
            "image_url": image_url,
            "end_image_url": end_image_url,
            "prompt": self._safe_prompt(prompt),
            "video_time": duration,
            "number_of_images": 1,
            "extend_prompt": self.extend_prompt,
            "generation_params": {"aspect_ratio": aspect_ratio},
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if auto_add_audio is not None:
            payload["auto_add_audio"] = auto_add_audio
        return await self._run(payload, has_audio=bool(payload.get("auto_add_audio")))

    async def extend_video(
        self,
        prompt: str,
        starting_video_url: str,
        video_time: Optional[int] = None,
        aspect_ratio: str = "9:16",
        auto_add_audio: Optional[bool] = None,
        name: str = "",
        model_version: Optional[str] = None,
    ) -> VideoResult:
        """Continue an existing clip (5-20s). Useful for extending a hook beat."""
        if not starting_video_url:
            raise A2EError("starting_video_url is required for video extension")
        duration = max(5, self._clamp_time(video_time or self.default_duration))
        payload: Dict[str, Any] = {
            "name": name or self._task_name(),
            "model_version": model_version or self.model_version,
            "starting_video_url": starting_video_url,
            "prompt": self._safe_prompt(prompt),
            "video_time": duration,
            "generation_params": {"aspect_ratio": aspect_ratio},
        }
        if auto_add_audio is not None:
            payload["auto_add_audio"] = auto_add_audio
        return await self._run(payload, has_audio=bool(payload.get("auto_add_audio")))

    async def generate_text_to_video(
        self,
        prompt: str,
        video_time: Optional[int] = None,
        aspect_ratio: str = "9:16",
        negative_prompt: Optional[str] = None,
        auto_add_audio: Optional[bool] = None,
        name: str = "",
        model_version: Optional[str] = None,
    ) -> VideoResult:
        """Text-to-video (a2e-v2 only, 5/10/15s, no reference assets)."""
        if not (prompt or "").strip():
            raise A2EError("text-to-video requires a non-empty prompt")
        model = model_version or self.model_version
        if model not in ("a2e-v2", "a2e-v2-flash"):
            raise A2EError("text-to-video requires model_version a2e-v2 or a2e-v2-flash")
        duration = self._clamp_time(video_time or self.default_duration, _TEXT_DURATIONS)
        payload: Dict[str, Any] = {
            "name": name or self._task_name(),
            "model_version": model,
            "generation_mode": "text-to-video",
            "prompt": self._safe_prompt(prompt),
            "video_time": duration,
            "number_of_images": 1,
            "extend_prompt": self.extend_prompt,
            "generation_params": {"aspect_ratio": aspect_ratio},
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if auto_add_audio is not None:
            payload["auto_add_audio"] = auto_add_audio
        return await self._run(payload, has_audio=bool(payload.get("auto_add_audio")))

    # -------------------------------------------------------------- internals
    async def _run(self, payload: Dict[str, Any], *, has_audio: bool) -> VideoResult:
        task_id = await self._submit(payload)
        data = await self._poll_until_done(task_id)
        url = _extract_media(data)
        if not url:
            raise A2EError(f"a2e task {task_id} returned no video URL")
        return VideoResult(
            url=url,
            provider="a2e",
            task_id=task_id,
            duration_seconds=data.get("duration_seconds") or data.get("video_time")
            or payload.get("video_time"),
            has_audio=has_audio,
            model_version=str(payload.get("model_version") or ""),
            cover_url=str(data.get("cover_url") or ""),
            coins=data.get("coins"),
            raw=data,
        )

    # -------------------------------------------------------------- pricing
    async def quote(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Price a generation request *without* creating a task or spending.

        ``POST /v1/generation/quote`` takes the endpoint plus the exact body that
        would be submitted. The returned ``coins`` is the authoritative cost, so
        the local coins/second estimate is only ever a fallback.

        A quote does not validate the request or reserve credits: ``available``
        can be true while the submission is still rejected.
        """
        self._require_key()
        body = {"endpoint": QUOTE_ENDPOINT, "requestBody": payload}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/v1/generation/quote",
                headers=self._headers,
                json=body,
            )
        if resp.status_code == 401:
            raise A2EError("a2e rejected the token (401) while quoting.")
        if resp.status_code == 400:
            detail = _safe_json(resp)
            code = detail.get("code") if isinstance(detail, dict) else None
            if code == "UNSUPPORTED_GENERATION_QUOTE_ENDPOINT":
                return {"available": False, "unavailable_reason": "no quote for this endpoint"}
            raise A2EError(f"a2e rejected the quote (400): {resp.text[:400]}")
        if resp.status_code >= 400:
            raise A2EError(f"a2e quote failed ({resp.status_code}): {resp.text[:300]}")
        data = (_safe_json(resp) or {}).get("data") or {}
        return {
            "available": bool(data.get("available")),
            "coins": data.get("coins"),
            "per_output_coins": data.get("perOutputCoins"),
            "count": data.get("count"),
            "exact": data.get("exact"),
            "unavailable_reason": data.get("unavailableReason"),
            "missing_pricing_fields": data.get("missingPricingFields") or [],
            "free_quota_coins": data.get("freeQuotaCoins"),
            "raw": data,
        }

    def quote_payload(
        self,
        *,
        prompt: str,
        video_time: int = 15,
        aspect_ratio: str = "9:16",
        reference_urls: Optional[List[str]] = None,
        auto_add_audio: Optional[bool] = None,
        model_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build the exact body a reference-to-video render would submit.

        ``quote()`` only interprets pricing fields, so the body must match the
        real submission for the price to mean anything.
        """
        urls = [u for u in (reference_urls or []) if u][:_MAX_REFERENCE_IMAGES]
        model = model_version or self.model_version
        duration = self._clamp_time(video_time, _REFERENCE_DURATIONS if urls else _TEXT_DURATIONS)
        payload: Dict[str, Any] = {
            "name": self._task_name(),
            "model_version": model,
            "generation_mode": "reference-to-video" if urls else "text-to-video",
            "prompt": self._safe_prompt(prompt),
            "video_time": duration,
            "number_of_images": 1,
            "extend_prompt": self.extend_prompt,
            "generation_params": {"aspect_ratio": aspect_ratio},
        }
        if urls:
            payload["generation_params"]["reference_assets"] = [
                {"asset_id": f"image_{i + 1}", "type": "image", "url": u}
                for i, u in enumerate(urls)
            ]
        if auto_add_audio is not None:
            payload["auto_add_audio"] = auto_add_audio
        return payload

    # --------------------------------------------------------------- uploads
    async def upload_file(
        self,
        path: str,
        *,
        content_type: str = "",
        expires_in: int = 300,
        purpose: str = "STAGING",
    ) -> Dict[str, Any]:
        """Host a local file on a2e's own storage and return its public CDN URL.

        Two steps, exactly as documented:

        1. ``POST /v1/r2/upload-presigned-url`` with key/purpose/contentType/size
        2. ``PUT`` the raw bytes to ``data.uploadUrl`` **without** an Authorization
           header (the presigned query already authenticates the PUT). Sending our
           bearer token there breaks the signature.

        This removes the third-party-host dependency: a Gemini still can go
        straight to a2e without Drive, a tunnel, or a public bucket.
        """
        self._require_key()
        from pathlib import Path as _Path

        file = _Path(path)
        if not file.exists():
            raise A2EError(f"upload_file: not found: {path}")
        size = file.stat().st_size
        if not content_type:
            content_type = _guess_content_type(file.suffix)
        key = f"microdrama/{uuid.uuid4().hex}{file.suffix.lower()}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/v1/r2/upload-presigned-url",
                headers=self._headers,
                json={
                    "key": key,
                    "purpose": purpose,
                    "contentType": content_type,
                    "fileSize": size,
                    "expiresIn": expires_in,
                },
            )
        if resp.status_code == 401:
            raise A2EError("a2e rejected the token (401) while requesting an upload URL.")
        if resp.status_code >= 400:
            raise A2EError(f"a2e presign failed ({resp.status_code}): {resp.text[:300]}")
        body = _safe_json(resp) or {}
        data = body.get("data") or {}
        upload_url, cdn_url = data.get("uploadUrl"), data.get("cdnUrl")
        if not upload_url or not cdn_url:
            raise A2EError(f"a2e presign returned no URL: {resp.text[:300]}")

        # Separate, unauthenticated client: no Authorization, no x-amz-* headers.
        async with httpx.AsyncClient(timeout=300) as client:
            put = await client.put(
                upload_url,
                content=file.read_bytes(),
                headers={"Content-Type": content_type},
            )
        if put.status_code >= 400:
            raise A2EError(
                f"a2e upload PUT failed ({put.status_code}): {put.text[:300]}. "
                "The presigned query carries the auth - do not attach the bearer token."
            )

        check = await _verify_hosted(cdn_url)
        return {
            "url": cdn_url,
            "key": data.get("key") or key,
            "bucket": data.get("bucket"),
            "bytes": size,
            "verified": check,
        }

    # ------------------------------------------------------------- management
    async def list_tasks(self, page_num: int = 1, page_size: int = 20) -> Dict[str, Any]:
        self._require_key()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.base_url}/v1/userImage2Video/allRecords",
                headers=self._headers,
                params={"pageNum": page_num, "pageSize": page_size},
            )
        resp.raise_for_status()
        body = _safe_json(resp)
        if isinstance(body, dict) and body.get("code") not in (0, None):
            raise A2EError(f"a2e list failed: code={body.get('code')} msg={body.get('msg')}")
        return (body or {}).get("data") or {}

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        return await self._fetch(task_id)

    async def delete_task(self, task_id: str) -> bool:
        self._require_key()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.delete(
                f"{self.base_url}/v1/userImage2Video/{task_id}",
                headers=self._headers,
            )
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        body = _safe_json(resp)
        return bool(isinstance(body, dict) and body.get("code") in (0, None))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _retry_after(resp: httpx.Response, fallback: float) -> float:
    """Parse ``Retry-After`` (seconds or HTTP-date) from a 429/503.

    The poll route documents ``Retry-After`` explicitly; ignoring it either hammers
    the query budget or gives up on a task that is still rendering.
    """
    raw = (resp.headers.get("retry-after") or "").strip()
    wait = fallback
    if raw:
        try:
            wait = float(raw)
        except ValueError:
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone

            try:
                when = parsedate_to_datetime(raw)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                wait = (when - datetime.now(timezone.utc)).total_seconds()
            except Exception:
                wait = fallback
    return max(1.0, min(float(wait), _MAX_RETRY_AFTER))


def _guess_content_type(suffix: str) -> str:
    suffix = (suffix or "").lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
    }.get(suffix, "application/octet-stream")


async def _verify_hosted(url: str) -> Dict[str, Any]:
    """Confirm the CDN URL a2e handed back actually serves bytes before use."""
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            resp = await client.get(url)
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        return {
            "ok": resp.status_code == 200 and len(resp.content) > 1024,
            "status": resp.status_code,
            "content_type": ctype,
            "bytes": len(resp.content),
        }
    except Exception as exc:
        return {"ok": False, "status": None, "content_type": None, "bytes": 0,
                "error": f"{type(exc).__name__}: {exc}"}


def _refund_note(data: Dict[str, Any]) -> str:
    if data.get("refund_pending"):
        return "pending"
    if data.get("hasRefundCoin") is True:
        return "refunded"
    if data.get("hasRefundCoin") is False:
        return "not refunded"
    return "unknown"


# ---------------------------------------------------------------- helpers
def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {}


def _extract_media(data: Dict[str, Any]) -> str:
    """First usable video URL from a task detail payload.

    Tolerates extra properties, and prefers HD/result fields over the plain ones.
    ``cover_url`` / ``result_cover`` are deliberately *not* consulted: they are
    images, and a poster frame handed to ``download_video`` would be saved as a
    corrupt .mp4 long after the render looked successful.
    """
    if not isinstance(data, dict):
        return ""
    for key in (
        "hd_video_url",
        "result_video_url",
        "video_url",
        "result_url",
        "url",
    ):
        value = data.get(key)
        if isinstance(value, str) and _looks_like_media(value):
            return value
    # The start() response nests a per-attempt record list.
    for record in data.get("all_records") or []:
        if not isinstance(record, dict):
            continue
        for key in ("hd_video_url", "result_video_url", "video_url", "result_url"):
            value = record.get(key)
            if isinstance(value, str) and _looks_like_media(value):
                return value
    return ""


_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp")
_VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".m4v", ".m3u8")


def _looks_like_media(url: str) -> bool:
    """True for anything that plausibly serves video rather than a still.

    Signed CDN URLs routinely omit an extension, so that case is accepted; an
    explicit image suffix never is.
    """
    if not url.startswith(("http://", "https://")):
        return False
    path = urlparse(url).path.lower()
    if path.endswith(_VIDEO_SUFFIXES):
        return True
    return not path.endswith(_IMAGE_SUFFIXES)

"""a2e.ai image-to-video client.

API surface implemented (all async, polled to terminal state):

    POST   /v1/userImage2Video/start          -> create task, returns _id
    GET    /v1/userImage2Video/{_id}          -> task detail (source of truth)
    GET    /v1/userImage2Video/allRecords     -> list tasks
    DELETE /v1/userImage2Video/{_id}          -> delete task

Why this provider matters for micro-drama:

* ``a2e-v2`` renders a single continuous 24 fps clip up to 20s (no 5s chunking),
  so a whole 15-second retention beat can be one generation instead of three cuts.
* ``auto_add_audio`` on v2 gives native audio (no ThinkSound surcharge), which is
  what vertical drama needs - dialogue, slaps, and stings are the hook.
* ``reference-to-video`` takes character reference images instead of a first frame,
  which holds identity across shots better than re-rolling an I2V frame.
* ``FLF2V`` (first-last-frame) lets us drive an exact opening and closing frame -
  ideal for cliffhanger cuts where the final frame must land precisely.

Docs caveat: the vendor example sends ``generation_mode: reference-to-video`` with
``aspect_ratio: 16:9`` while the prompt asks for a 3:4 portrait. We treat
``generation_params.aspect_ratio`` as authoritative and never inherit that mismatch.

The docs do not publish a fixed status vocabulary, so terminal detection here is
defensive: explicit failure markers first, then any success marker, then the
appearance of a media URL.
"""
import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

DEFAULT_BASE_URL = "https://api.a2e.ai"

# Statuses seen in the wild / implied by the payloads. Unknown values are polled
# through rather than treated as failures.
_FAILURE_STATUSES = {"failed", "failure", "error", "cancelled", "canceled", "expired", "rejected"}
_SUCCESS_STATUSES = {"completed", "complete", "success", "succeeded", "done", "finished"}

_REFERENCE_DURATIONS = (3, 4, 5, 10, 15)
_TEXT_DURATIONS = (5, 10, 15)
_MIN_DURATION, _MAX_DURATION = 3, 20
_MAX_PROMPT_V2 = 2000


class A2EError(RuntimeError):
    """Raised for non-retryable a2e failures (bad payload, rejected task)."""


class A2ETaskFailed(A2EError):
    """Raised when a submitted task reaches a terminal failure state."""

    def __init__(self, message: str, *, task_id: str = "", code: Any = None, reason: Any = None):
        super().__init__(message)
        self.task_id = task_id
        self.code = code
        self.reason = reason


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
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.base_url}/v1/userImage2Video/{task_id}",
                headers=self._headers,
            )
        if resp.status_code == 401:
            raise A2EError("a2e rejected the token (401) while polling.")
        if resp.status_code == 404:
            raise A2EError(f"a2e task {task_id} not found (404).")
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
            status = str(data.get("current_status") or data.get("status") or "").lower()
            if status != last_status:
                print(f"[a2e] task {task_id}: {status or 'unknown'}")
                last_status = status

            if status in _FAILURE_STATUSES:
                raise A2ETaskFailed(
                    f"a2e task {task_id} failed: "
                    f"{data.get('failed_message') or data.get('failed_reason') or 'unknown error'}",
                    task_id=task_id,
                    code=data.get("failed_code"),
                    reason=data.get("failed_reason"),
                )

            media = _extract_media(data)
            if media or status in _SUCCESS_STATUSES:
                if not media:
                    raise A2EError(f"a2e task {task_id} completed without a media URL: {data}")
                return data

        raise TimeoutError(f"a2e task {task_id} did not finish within {self.timeout}s")

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
            raw=data,
        )

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


# ---------------------------------------------------------------- helpers
def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {}


def _extract_media(data: Dict[str, Any]) -> str:
    """First usable video URL from a task detail payload.

    Tolerates extra properties, and prefers HD/result fields over the plain ones.
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


def _looks_like_media(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    path = urlparse(url).path.lower()
    if path.endswith((".mp4", ".mov", ".webm", ".m3u8")):
        return True
    # Signed CDN URLs often omit an extension; accept anything that isn't an image.
    return not path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))

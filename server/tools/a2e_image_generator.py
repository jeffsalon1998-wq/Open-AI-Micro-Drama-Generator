"""a2e.ai text-to-image client (the image generator on the a2e site).

    POST   /v1/userText2Image/start        -> create task, returns _id
    GET    /v1/userText2Image/{_id}        -> task detail (source of truth)
    GET    /v1/userText2Image/allRecords   -> list tasks
    POST   /v1/userText2Image/batchDetail  -> batch details
    DELETE /v1/userText2Image/{_id}        -> delete task

Documented models (``model_type``):

    a2e      automatic model selection (default; accepts up to 2 input images)
    zimage   Z-Image
    seedream Seedream, needs ``model_version`` 4.5 | 5.0 | 5.0-pro

``aspect_ratio`` supports 1:1, 4:3, 3:4, 16:9, 9:16, 2:3, 3:2, 21:9.
The backend defaults Seedream to 9:16 when the ratio is omitted; we always send it
explicitly so the request cannot inherit a surprise default.

Note on "Muse": the a2e image API documents ``a2e``, ``zimage`` and ``seedream``
only — there is no ``muse`` model_type in the published contract. Sending an
undocumented enum is a 400, so ``a2e`` (auto selection) is the default here; set
``A2E_IMAGE_MODEL_TYPE`` if the site exposes Muse under one of those names.
"""
import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from tools.a2e_video_generator import (
    A2EError,
    A2ETaskFailed,
    _FAILURE_STATUSES,
    _SUCCESS_STATUSES,
    _safe_json,
)

DEFAULT_BASE_URL = "https://api.a2e.ai"
ASPECT_RATIOS = ("1:1", "4:3", "3:4", "16:9", "9:16", "2:3", "3:2", "21:9")
RESOLUTIONS = ("1K", "1080P", "2K", "3K", "4K")
MODEL_TYPES = ("a2e", "zimage", "seedream")
SEEDREAM_VERSIONS = ("4.5", "5.0", "5.0-pro")


@dataclass
class ImageResult:
    url: str
    provider: str = "a2e"
    task_id: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    model_type: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class A2EImageGenerator:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        poll_interval: float = 3.0,
        timeout: int = 600,
        max_submit_retries: int = 3,
    ):
        self.api_key = api_key or os.environ.get("A2E_API_KEY", "")
        self.base_url = (base_url or os.environ.get("A2E_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.poll_interval = float(os.environ.get("A2E_POLL_INTERVAL", poll_interval))
        self.timeout = int(os.environ.get("A2E_TIMEOUT", timeout))
        self.max_submit_retries = max_submit_retries
        self.model_type = os.environ.get("A2E_IMAGE_MODEL_TYPE", "a2e")
        self.model_version = os.environ.get("A2E_IMAGE_MODEL_VERSION", "")
        self.resolution = os.environ.get("A2E_IMAGE_RESOLUTION", "1080P")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _require_key(self) -> None:
        if not self.api_key:
            raise A2EError("A2E_API_KEY is not set (needed for a2e image generation).")

    # ------------------------------------------------------------------ submit
    def _payload(
        self,
        prompt: str,
        aspect_ratio: str,
        width: Optional[int],
        height: Optional[int],
        model_type: str,
        model_version: Optional[str],
        resolution: Optional[str],
        input_images: Optional[List[str]],
        name: str,
        max_images: int,
        skip_face_enhance: bool,
    ) -> Dict[str, Any]:
        if aspect_ratio not in ASPECT_RATIOS:
            raise A2EError(
                f"aspect_ratio {aspect_ratio!r} is not one of {', '.join(ASPECT_RATIOS)}"
            )
        if model_type not in MODEL_TYPES:
            raise A2EError(
                f"model_type {model_type!r} is not one of {', '.join(MODEL_TYPES)} "
                "(the API has no 'muse' value)"
            )
        payload: Dict[str, Any] = {
            "name": name,
            "prompt": prompt,
            "model_type": model_type,
            "aspect_ratio": aspect_ratio,
            "max_images": max_images,
            "skip_face_enhance": skip_face_enhance,
        }
        if model_type == "seedream":
            version = model_version or self.model_version or "5.0"
            if version not in SEEDREAM_VERSIONS:
                raise A2EError(
                    f"seedream model_version {version!r} is not one of {', '.join(SEEDREAM_VERSIONS)}"
                )
            payload["model_version"] = version
        if width and height:
            payload["width"] = int(width)
            payload["height"] = int(height)
        if resolution:
            payload["resolution"] = resolution
        if input_images:
            payload["input_images"] = list(input_images)
        return payload

    @staticmethod
    def _aspect_size(aspect_ratio: str, short_side: int = 1080) -> tuple:
        a, b = (int(x) for x in aspect_ratio.split(":"))
        if a <= b:
            return short_side, int(round(short_side * b / a))
        return int(round(short_side * a / b)), short_side

    async def _submit(self, payload: Dict[str, Any]) -> str:
        self._require_key()
        for attempt in range(1, self.max_submit_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    resp = await client.post(
                        f"{self.base_url}/v1/userText2Image/start",
                        headers=self._headers,
                        json=payload,
                    )
            except httpx.HTTPError as exc:
                if attempt < self.max_submit_retries:
                    await asyncio.sleep(2 * attempt)
                    continue
                raise A2EError(f"a2e image submit failed after {attempt} attempts: {exc}") from exc

            if resp.status_code == 401:
                raise A2EError("a2e rejected the token (401). Check A2E_API_KEY.")
            if resp.status_code == 400:
                raise A2EError(f"a2e rejected the image payload (400): {resp.text[:400]}")
            if resp.status_code >= 500 and attempt < self.max_submit_retries:
                await asyncio.sleep(2 * attempt)
                continue
            if resp.status_code != 200:
                raise A2EError(f"a2e image submit failed ({resp.status_code}): {resp.text[:300]}")

            body = _safe_json(resp)
            if isinstance(body, dict) and body.get("code") not in (0, None):
                raise A2EError(f"a2e image submit rejected: code={body.get('code')} msg={body.get('msg')}")
            data = (body or {}).get("data") or {}
            task_id = data.get("_id") or data.get("id")
            if not task_id:
                raise A2EError(f"a2e image submit returned no task id: {body}")
            return str(task_id)

        raise A2EError("a2e image submit failed")

    async def _fetch(self, task_id: str) -> Dict[str, Any]:
        self._require_key()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.base_url}/v1/userText2Image/{task_id}", headers=self._headers
            )
        if resp.status_code == 401:
            raise A2EError("a2e rejected the token (401) while polling the image task.")
        if resp.status_code == 404:
            raise A2EError(f"a2e image task {task_id} not found (404).")
        if resp.status_code != 200:
            raise A2EError(f"a2e image poll failed ({resp.status_code}): {resp.text[:300]}")
        body = _safe_json(resp)
        if isinstance(body, dict) and body.get("code") not in (0, None):
            raise A2EError(f"a2e image poll rejected: code={body.get('code')} msg={body.get('msg')}")
        return (body or {}).get("data") or {}

    async def _poll_until_done(self, task_id: str) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.timeout
        last = ""
        while loop.time() < deadline:
            await asyncio.sleep(self.poll_interval)
            data = await self._fetch(task_id)
            status = str(data.get("current_status") or data.get("status") or "").lower()
            if status and status != last:
                print(f"[a2e image] task {task_id}: {status}")
                last = status
            if status in _FAILURE_STATUSES:
                raise A2ETaskFailed(
                    f"a2e image task {task_id} failed: "
                    f"{data.get('failed_message') or data.get('failed_reason') or 'unknown error'}",
                    task_id=task_id,
                    code=data.get("failed_code"),
                )
            url = _extract_image_url(data)
            if url or status in _SUCCESS_STATUSES:
                if not url:
                    raise A2EError(f"a2e image task {task_id} completed without a URL: {data}")
                return data
        raise TimeoutError(f"a2e image task {task_id} did not finish within {self.timeout}s")

    # -------------------------------------------------------------------- api
    async def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "9:16",
        width: Optional[int] = None,
        height: Optional[int] = None,
        model_type: Optional[str] = None,
        model_version: Optional[str] = None,
        resolution: Optional[str] = None,
        input_images: Optional[List[str]] = None,
        name: str = "",
        max_images: int = 1,
        skip_face_enhance: bool = False,
    ) -> ImageResult:
        """Text-to-image, or image-edit when ``input_images`` are supplied."""
        model = (model_type or self.model_type).lower()
        if not name:
            from datetime import datetime
            name = f"microdrama {datetime.now().strftime('%m-%d-%Y %H:%M:%S')}"
        payload = self._payload(
            prompt=prompt.strip(),
            aspect_ratio=aspect_ratio,
            width=width,
            height=height,
            model_type=model,
            model_version=model_version,
            resolution=resolution if resolution is not None else self.resolution,
            input_images=input_images,
            name=name,
            max_images=max_images,
            skip_face_enhance=skip_face_enhance,
        )
        task_id = await self._submit(payload)
        data = await self._poll_until_done(task_id)
        url = _extract_image_url(data)
        if not url:
            raise A2EError(f"a2e image task {task_id} returned no image URL")
        return ImageResult(
            url=url,
            task_id=task_id,
            width=data.get("width"),
            height=data.get("height"),
            model_type=model,
            raw=data,
        )

    async def list_tasks(self, page_num: int = 1, page_size: int = 20) -> Dict[str, Any]:
        self._require_key()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.base_url}/v1/userText2Image/allRecords",
                headers=self._headers,
                params={"pageNum": page_num, "pageSize": page_size},
            )
        resp.raise_for_status()
        body = _safe_json(resp)
        return (body or {}).get("data") or {}

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        return await self._fetch(task_id)

    async def batch_detail(self, task_ids: List[str]) -> Dict[str, Any]:
        self._require_key()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/v1/userText2Image/batchDetail",
                headers=self._headers,
                json={"_ids": list(task_ids)},
            )
        resp.raise_for_status()
        body = _safe_json(resp)
        return (body or {}).get("data") or {}

    async def delete_task(self, task_id: str) -> bool:
        self._require_key()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.delete(
                f"{self.base_url}/v1/userText2Image/{task_id}", headers=self._headers
            )
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        body = _safe_json(resp)
        return bool(isinstance(body, dict) and body.get("code") in (0, None))


def _extract_image_url(data: Dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("result_url", "image_url", "hd_image_url", "url", "cover_url"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    for key in ("image_urls", "result_urls", "all_images"):
        values = data.get(key) or []
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.startswith("http"):
                    return value
    for record in data.get("all_records") or []:
        if isinstance(record, dict):
            url = _extract_image_url(record)
            if url:
                return url
    return ""


__all__ = ["A2EImageGenerator", "ImageResult", "ASPECT_RATIOS", "RESOLUTIONS", "MODEL_TYPES"]

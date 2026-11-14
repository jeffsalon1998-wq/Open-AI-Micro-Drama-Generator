"""Gemini image generation (image generation only — Gemini is not used for video).

    POST {GEMINI_API_URL}/models/{model}:generateContent

Documented model: ``gemini-2.5-flash-image`` ("Nano Banana"), which returns the
image as inline base64 data rather than a hosted URL. Supports text-to-image and
image editing (pass input images), plus ``imageConfig.aspectRatio``.

Because Gemini hands back bytes, callers get a **local file path**; use
``tools.image_host.publish_image`` to turn it into a URL a video provider can fetch.
"""
import asyncio
import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_API_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash-image"

# Aspect ratios accepted by gemini-2.5-flash-image's imageConfig.
ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")

_RETRY_STATUSES = {429, 500, 502, 503, 504}


class GeminiImageError(RuntimeError):
    """Raised for Gemini image failures (bad key, blocked prompt, no image returned)."""


@dataclass
class GeneratedImage:
    path: str
    mime_type: str = "image/png"
    model: str = ""
    text: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def size_bytes(self) -> int:
        return Path(self.path).stat().st_size if Path(self.path).exists() else 0


class GeminiImageGenerator:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        api_url: Optional[str] = None,
        timeout: int = 180,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_MODEL)
        self.api_url = (api_url or os.environ.get("GEMINI_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.timeout = int(os.environ.get("GEMINI_TIMEOUT", timeout))
        self.max_retries = max_retries

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def _headers(self) -> Dict[str, str]:
        # Header auth keeps the key out of URLs and logs.
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def _build_payload(
        self,
        prompt: str,
        aspect_ratio: str,
        input_images: Optional[List[str]],
    ) -> Dict[str, Any]:
        parts: List[Dict[str, Any]] = [{"text": prompt}]
        for img in input_images or []:
            if not img:
                continue
            mime, data = self._load_image(img)
            # REST wire format is camelCase.
            parts.append({"inlineData": {"mimeType": mime, "data": data}})

        generation_config: Dict[str, Any] = {"responseModalities": ["IMAGE"]}
        if aspect_ratio:
            if aspect_ratio not in ASPECT_RATIOS:
                raise GeminiImageError(
                    f"aspect_ratio {aspect_ratio!r} is not supported by {self.model}; "
                    f"use one of {', '.join(ASPECT_RATIOS)}"
                )
            generation_config["imageConfig"] = {"aspectRatio": aspect_ratio}

        return {"contents": [{"parts": parts}], "generationConfig": generation_config}

    @staticmethod
    def _load_image(src: str) -> tuple:
        """Accept a local path, an http(s) URL, or a bare data URL/base64 blob."""
        if src.startswith("data:"):
            header, _, data = src.partition(",")
            mime = header.split(";")[0].replace("data:", "") or "image/png"
            return mime, data
        if src.startswith(("http://", "https://")):
            resp = httpx.get(src, timeout=60, follow_redirects=True)
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "image/png").split(";")[0].strip()
            return mime, base64.b64encode(resp.content).decode()
        path = Path(src)
        if not path.exists():
            raise GeminiImageError(f"input image not found: {src} (and it is not a URL)")
        suffix = path.suffix.lower().lstrip(".") or "png"
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(suffix, f"image/{suffix}")
        return mime, base64.b64encode(path.read_bytes()).decode()

    async def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "9:16",
        input_images: Optional[List[str]] = None,
        out_path: str = "",
        name: str = "",
    ) -> GeneratedImage:
        """Generate (or edit with ``input_images``) and write the image to disk."""
        if not self.api_key:
            raise GeminiImageError("GEMINI_API_KEY is not set (needed for image generation).")
        if not (prompt or "").strip():
            raise GeminiImageError("prompt is required for image generation")

        payload = self._build_payload(prompt, aspect_ratio, input_images)
        url = f"{self.api_url}/models/{self.model}:generateContent"

        last_error: Optional[str] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, headers=self._headers, json=payload)
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
                if attempt < self.max_retries:
                    await asyncio.sleep(2 * attempt)
                    continue
                raise GeminiImageError(f"Gemini image request failed: {last_error}") from exc

            if resp.status_code in _RETRY_STATUSES and attempt < self.max_retries:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                await asyncio.sleep(2 * attempt)
                continue
            if resp.status_code == 400:
                raise GeminiImageError(f"Gemini rejected the request (400): {resp.text[:500]}")
            if resp.status_code in (401, 403):
                raise GeminiImageError(
                    f"Gemini API key rejected ({resp.status_code}). Check GEMINI_API_KEY."
                )
            if resp.status_code != 200:
                raise GeminiImageError(
                    f"Gemini image failed ({resp.status_code}): {resp.text[:300]}"
                )

            body = resp.json()
            image_part = _find_image_part(body)
            if not image_part:
                # Safety blocks and refusals come back as text with no image.
                text = _collect_text(body)
                hint = ""
                if body.get("promptFeedback", {}).get("blockReason"):
                    hint = f" (blockReason: {body['promptFeedback']['blockReason']})"
                raise GeminiImageError(
                    f"Gemini returned no image{hint}. Text: {text[:300] or 'none'}"
                )

            data = image_part.get("data") or ""
            mime = image_part.get("mimeType") or image_part.get("mime_type") or "image/png"
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")

            if not out_path:
                from datetime import datetime
                safe = name or datetime.now().strftime("gemini-%Y%m%d-%H%M%S")
                out_path = str(Path(os.environ.get("IMAGE_CACHE_DIR", "outputs/images")) / f"{safe}{ext}")
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(base64.b64decode(data))
            print(f"[gemini] image written: {out_path} ({mime})")

            return GeneratedImage(
                path=out_path,
                mime_type=mime,
                model=self.model,
                text=_collect_text(body),
                raw={"candidates": len(body.get("candidates") or [])},
            )

        raise GeminiImageError(f"Gemini image request failed after {self.max_retries} attempts: {last_error}")


def _find_image_part(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for candidate in body.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return inline
    return None


def _collect_text(body: Dict[str, Any]) -> str:
    chunks = []
    for candidate in body.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if part.get("text"):
                chunks.append(part["text"])
    return "\n".join(chunks).strip()

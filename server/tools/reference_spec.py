"""a2e's reference-file rules, in one place.

The a2e uploader is a validator, not a suggestion box. Its accepted-input panel
states, verbatim::

    JPG, PNG, WEBP, GIF · up to 30 MiB · 2400-6000 px · MP4, MOV · up to 50 MiB
                                              2-15 s
    Up to 36,000,000 pixels · WAV, MP3 · up to 10 MiB · 2-15 s

The "2400-6000 px" strip belongs to the *video* inputs. Reference stills use the
shared constraint line: **600 px minimum, 2400 px maximum, 36 MP ceiling**.

This module exists because getting it wrong is expensive in a specific way: the
validator sits in the web UI, but the API takes `reference_assets[].url` and
fails the task *after* submission. Coins are committed before anyone learns the
portrait was 364 px wide.

Everything here is dependency-free (a 14-byte PNG/JPG header reader) so the API
and the pre-flight can check dimensions without pulling in Pillow.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

MIN_SIDE = 600
MAX_SIDE = 2400
MAX_PIXELS = 36_000_000
MAX_BYTES = 30 * 1024 * 1024          # 30 MiB for images
ALLOWED_MIME = ("image/jpeg", "image/png", "image/webp", "image/gif")
ALLOWED_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# The vertical dramas are 9:16; reference stills do not have to match, but a
# portrait far outside it can get letterboxed into the frame.
TARGET_ASPECT = 9 / 16


@dataclass(frozen=True)
class ReferenceCheck:
    ok: bool
    problems: tuple
    warnings: tuple
    width: Optional[int] = None
    height: Optional[int] = None
    pixels: Optional[int] = None
    bytes: Optional[int] = None
    mime: str = ""

    @property
    def aspect(self) -> Optional[float]:
        if not (self.width and self.height):
            return None
        return self.width / self.height

    def summary(self) -> str:
        if self.width:
            return f"{self.width}x{self.height} ({self.pixels:,} px)"
        return "dimensions unknown"

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "width": self.width,
            "height": self.height,
            "pixels": self.pixels,
            "bytes": self.bytes,
            "mime": self.mime,
            "aspect": round(self.aspect, 3) if self.aspect else None,
            "rules": {
                "min_side": MIN_SIDE,
                "max_side": MAX_SIDE,
                "max_pixels": MAX_PIXELS,
                "allowed": list(ALLOWED_MIME),
            },
        }


def image_size(path: str) -> Tuple[Optional[int], Optional[int]]:
    """Read (width, height) from a PNG/JPG/GIF/WEBP header without Pillow.

    Returns (None, None) when the format is not recognised — callers treat that
    as "cannot verify", never as "fine".
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                # IHDR width/height are big-endian uint32 at offsets 16 and 20.
                return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))

            if head[:6] in (b"GIF87a", b"GIF89a"):
                return (int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little"))

            if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
                chunk = head[12:16]
                if chunk == b"VP8 ":
                    return (int.from_bytes(head[26:28], "little") & 0x3FFF,
                            int.from_bytes(head[28:30], "little") & 0x3FFF)
                if chunk == b"VP8L":
                    bits = int.from_bytes(head[21:25], "little")
                    return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
                if chunk == b"VP8X":
                    return (int.from_bytes(head[24:27], "little") + 1,
                            int.from_bytes(head[27:30], "little") + 1)
                return (None, None)

            if head.startswith(b"\xff\xd8"):
                # Walk the JPEG marker chain to the first SOFn segment.
                with open(path, "rb") as f:
                    f.seek(2)
                    while True:
                        marker = f.read(2)
                        if len(marker) < 2 or marker[0] != 0xFF:
                            return (None, None)
                        code = marker[1]
                        if code in (0xD8, 0xD9):
                            continue
                        length_bytes = f.read(2)
                        if len(length_bytes) < 2:
                            return (None, None)
                        length = int.from_bytes(length_bytes, "big")
                        if code in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                            data = f.read(5)
                            if len(data) < 5:
                                return (None, None)
                            return (int.from_bytes(data[3:5], "big"),
                                    int.from_bytes(data[1:3], "big"))
                        f.seek(max(0, length - 2), 1)
    except OSError:
        return (None, None)
    return (None, None)


def mime_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")


def check_file(path: str) -> ReferenceCheck:
    """Validate a local image against a2e's reference-input rules."""
    p = Path(path)
    if not p.exists():
        return ReferenceCheck(False, (f"file not found: {path}",), ())
    size = p.stat().st_size
    mime = mime_for(str(p))
    width, height = image_size(str(p))

    problems, warnings = [], []
    if p.suffix.lower() not in ALLOWED_SUFFIXES:
        problems.append(
            f"{p.suffix or 'no extension'} is not accepted — a2e takes "
            f"{', '.join(s.lstrip('.').upper() for s in ALLOWED_SUFFIXES)}"
        )
    if size > MAX_BYTES:
        problems.append(f"{size / 1e6:.1f} MB exceeds the 30 MB image limit")

    pixels = None
    if not (width and height):
        warnings.append("could not read image dimensions from the header")
    else:
        pixels = width * height
        short, long_side = min(width, height), max(width, height)
        if short < MIN_SIDE:
            problems.append(
                f"short side {short}px is below a2e's {MIN_SIDE}px minimum"
            )
        if long_side > MAX_SIDE:
            problems.append(f"long side {long_side}px exceeds a2e's {MAX_SIDE}px maximum")
        if pixels > MAX_PIXELS:
            problems.append(f"{pixels:,} px exceeds the {MAX_PIXELS:,} px ceiling")
        aspect = width / height
        if aspect > 1.05:
            warnings.append(
                f"landscape ({width}x{height}); a 9:16 render may crop or letterbox it"
            )
        elif aspect < 0.35:
            warnings.append(
                f"very narrow ({aspect:.2f}); the model may pad the frame"
            )

    return ReferenceCheck(
        ok=not problems, problems=tuple(problems), warnings=tuple(warnings),
        width=width, height=height, pixels=pixels, bytes=size, mime=mime,
    )


def check_url(url: str, timeout: int = 45) -> dict:
    """Validate a *remote* reference by fetching it and reading the header.

    This is the check that matters for the Drive path: a 364-wide portrait is
    rejected by a2e no matter how healthy the URL is, and ``verify_image_url``
    alone would call that file fine.
    """
    import asyncio
    import os
    import tempfile

    import httpx

    result = {"url": url, "ok": False, "status": None, "content_type": None,
              "bytes": 0, "error": None}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
        result["status"] = resp.status_code
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        result["content_type"] = ctype
        result["bytes"] = len(resp.content)
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result
        if not ctype.startswith("image/"):
            result["error"] = f"served {ctype or 'unknown type'}, not an image"
            return result
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                  "image/gif": ".gif"}.get(ctype, ".bin")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name
        try:
            check = check_file(tmp_path)
        finally:
            os.unlink(tmp_path)
        result.update(check.as_dict())
        result["ok"] = check.ok
        if not check.ok:
            result["error"] = "; ".join(check.problems)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


__all__ = [
    "ALLOWED_MIME",
    "MAX_PIXELS",
    "MAX_SIDE",
    "MIN_SIDE",
    "ReferenceCheck",
    "TARGET_ASPECT",
    "check_file",
    "check_url",
    "image_size",
    "mime_for",
]

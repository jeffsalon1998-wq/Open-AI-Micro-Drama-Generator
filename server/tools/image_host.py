"""Publish a locally generated image so video providers can fetch it.

Gemini returns image bytes, but video providers need a URL they can download.
a2e has no upload endpoint in its API, so a public URL is mandatory.

Strategy:

1. ``IMAGE_HOST_MODE=serve`` (default) — assume this server is reachable and
   return ``{PUBLIC_BASE_URL}/images/{job}/{file}``. Requires a tunnel or a
   deployed host; the API reports whether that is likely to work.
2. ``IMAGE_HOST_MODE=imgur`` — upload to Imgur with ``IMGUR_CLIENT_ID``.
3. ``IMAGE_HOST_PUBLIC_DIR`` — write into a directory already served publicly
   (e.g. an S3/nginx mount), returning ``PUBLIC_BASE_URL`` + relative path.

``publish_image`` always returns a URL and says how it was produced, so a run
never silently hands a localhost path to a paid video API.
"""
import os
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

IMGUR_ENDPOINT = "https://api.imgur.com/3/image"


class ImageHostError(RuntimeError):
    pass


def _base_url() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")


def is_public_url(url: str) -> bool:
    """Reject URLs no remote service could reach."""
    if not url.startswith(("http://", "https://")):
        return False
    host = url.split("://", 1)[1].split("/", 1)[0].split(":")[0].lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"):
        return False
    if host.startswith("192.168.") or host.startswith("10.") or host.endswith(".local"):
        return False
    return "." in host


def describe() -> dict:
    """Surface the publish strategy so the UI/API can warn before a paid run."""
    mode = (os.environ.get("IMAGE_HOST_MODE") or "serve").lower()
    base = _base_url()
    return {
        "mode": mode,
        "public_base_url": base or None,
        "public_base_url_reachable": is_public_url(base) if base else False,
        "imgur_configured": bool(os.environ.get("IMGUR_CLIENT_ID")),
        "public_dir": os.environ.get("IMAGE_HOST_PUBLIC_DIR") or None,
        "warning": (
            None
            if (mode == "imgur" and os.environ.get("IMGUR_CLIENT_ID"))
            or (base and is_public_url(base))
            or os.environ.get("IMAGE_HOST_PUBLIC_DIR")
            else "Gemini images are local files; the video provider needs a public URL. "
                 "Set PUBLIC_BASE_URL (tunnel/deployed host), IMAGE_HOST_MODE=imgur with "
                 "IMGUR_CLIENT_ID, or IMAGE_HOST_PUBLIC_DIR."
        ),
    }


async def publish_image(
    local_path: str,
    job_id: str = "shared",
    public_dir: Optional[str] = None,
) -> str:
    """Return a public URL for ``local_path``, or raise ImageHostError."""
    path = Path(local_path)
    if not path.exists():
        raise ImageHostError(f"image not found: {local_path}")

    mode = (os.environ.get("IMAGE_HOST_MODE") or "serve").lower()

    if mode == "imgur":
        client_id = os.environ.get("IMGUR_CLIENT_ID", "")
        if not client_id:
            raise ImageHostError("IMAGE_HOST_MODE=imgur but IMGUR_CLIENT_ID is empty.")
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                IMGUR_ENDPOINT,
                headers={"Authorization": f"Client-ID {client_id}"},
                files={"image": (path.name, path.read_bytes())},
                data={"type": "file"},
            )
        if resp.status_code >= 400:
            raise ImageHostError(f"Imgur upload failed ({resp.status_code}): {resp.text[:200]}")
        link = ((resp.json() or {}).get("data") or {}).get("link")
        if not link:
            raise ImageHostError(f"Imgur returned no link: {resp.text[:200]}")
        return link

    base = _base_url()

    # Static-hosting mode: copy into a directory that is already served publicly.
    target_dir = public_dir or os.environ.get("IMAGE_HOST_PUBLIC_DIR")
    if target_dir:
        dest = Path(target_dir) / job_id
        dest.mkdir(parents=True, exist_ok=True)
        dest_file = dest / path.name
        shutil.copy2(path, dest_file)
        if not base:
            raise ImageHostError(
                "IMAGE_HOST_PUBLIC_DIR is set but PUBLIC_BASE_URL is missing, so no URL can be built."
            )
        return f"{base}/{quote(job_id)}/{quote(path.name)}"

    # Default: this server also mounts /images (see api.py).
    if not base:
        raise ImageHostError(
            "Gemini produced a local image, but no PUBLIC_BASE_URL is configured, so the video "
            "provider cannot fetch it. Set PUBLIC_BASE_URL to a reachable https host (tunnel or "
            "deployment), or IMAGE_HOST_MODE=imgur with IMGUR_CLIENT_ID."
        )
    if not is_public_url(base):
        raise ImageHostError(
            f"PUBLIC_BASE_URL={base!r} is not reachable from the internet, so the video provider "
            "cannot fetch the image. Use a tunnel (e.g. cloudflared/ngrok) or IMAGE_HOST_MODE=imgur."
        )

    served = Path(os.environ.get("IMAGE_SERVE_DIR", "outputs/images"))
    dest = served / job_id / path.name
    if path.resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    return f"{base}/images/{quote(job_id)}/{quote(path.name)}"


async def verify_image_url(url: str, timeout: int = 45) -> dict:
    """Confirm a remote URL actually serves an image before we spend coins.

    Google Drive share links are a common trap: ``/file/d/<id>/view`` returns an
    HTML page, and only ``uc?id=<id>&export=download`` (or ``thumbnail?id=<id>``)
    returns bytes. Video providers reject the HTML page silently or with a
    generic failure, long after the task is queued.
    """
    result = {"url": url, "ok": False, "status": None, "content_type": None,
              "bytes": 0, "final_url": url, "error": None}
    if not is_public_url(url):
        result["error"] = "not a public http(s) URL (localhost/private hosts are unreachable)"
        return result
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
        result["status"] = resp.status_code
        result["final_url"] = str(resp.url)
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        result["content_type"] = ctype
        result["bytes"] = len(resp.content)
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
        elif not ctype.startswith("image/"):
            result["error"] = (
                f"served {ctype or 'unknown type'}, not an image"
                + (" (looks like a Drive/Docs HTML viewer page)" if "html" in ctype else "")
            )
        elif result["bytes"] < 1024:
            result["error"] = f"suspiciously small ({result['bytes']} bytes)"
        else:
            result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def drive_direct_url(file_id: str) -> str:
    """Direct-download form of a Drive file (public file required)."""
    return f"https://drive.google.com/uc?id={file_id}&export=download"

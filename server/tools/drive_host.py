"""Google Drive as the image host for reference-to-video.

Pipeline: Gemini image bytes -> Drive upload -> public "anyone with link" ->
direct-download URL -> a2e reference_assets.

Drive is attractive because it is free and persistent, but two details decide
whether a2e can read the file:

1. Share it as ``anyone`` / ``reader``. A private file returns an HTML sign-in page.
2. Use the **direct download** form. ``/file/d/<id>/view`` returns HTML;
   ``uc?id=<id>&export=download`` returns bytes.

``verify_image_url`` checks both before any coins are spent.

This module only builds URLs and orchestrates; the actual Drive reads/writes go
through the connected Drive app (agent tool), not through this process.
"""
from typing import Optional

from tools.image_host import drive_direct_url, verify_image_url

DOWNLOAD_URL_TEMPLATE = "https://drive.google.com/uc?id={file_id}&export=download"


def build_reference_url(file_id: str) -> str:
    return DOWNLOAD_URL_TEMPLATE.format(file_id=file_id)


async def prepare_reference(file_id: str) -> dict:
    """Validate a Drive file is usable as an a2e reference asset."""
    url = build_reference_url(file_id)
    check = await verify_image_url(url)
    hints = []
    if not check["ok"]:
        if check["status"] in (401, 403):
            hints.append("file is not shared publicly: set link sharing to 'anyone with the link'")
        elif check["content_type"] and "html" in check["content_type"]:
            hints.append("this URL returned an HTML page; use uc?id=<file_id>&export=download")
        elif check["status"] == 404:
            hints.append("file id not found or not accessible to anonymous users")
    check["file_id"] = file_id
    check["hints"] = hints
    return check


__all__ = ["build_reference_url", "prepare_reference", "DOWNLOAD_URL_TEMPLATE"]

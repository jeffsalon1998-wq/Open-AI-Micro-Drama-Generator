import asyncio
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import httpx


async def download_video(url: str, dest_path: str) -> str:
    """Download a video from a URL to a local path. Returns the path."""
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(resp.content)
    return dest_path


async def concatenate_videos(
    video_paths: List[str],
    output_path: str,
    target_size: Optional[Tuple[int, int]] = None,
) -> str:
    """
    Concatenate multiple video files into one using moviepy.

    Clips from different providers can differ in resolution, frame rate and
    audio presence (a2e returns native audio, MuAPI usually does not), so every
    clip is normalised to one size/fps and given a silent track when missing.
    Without that, a single mismatched clip produces black bars or a broken mix.

    video_paths: list of local file paths or URLs
    output_path: local path for the output video
    target_size: (width, height); defaults to the first clip's size, or
                 TARGET_RESOLUTION (default 720x1280 for vertical drama)
    Returns the output_path.
    """
    local_paths = []
    temp_files = []

    for vp in video_paths:
        if vp.startswith("http://") or vp.startswith("https://"):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            await download_video(vp, tmp.name)
            local_paths.append(tmp.name)
            temp_files.append(tmp.name)
        else:
            local_paths.append(vp)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _concat_sync, local_paths, output_path, target_size)

    for tf in temp_files:
        Path(tf).unlink(missing_ok=True)

    return output_path


def _env_resolution() -> Optional[Tuple[int, int]]:
    raw = os.environ.get("TARGET_RESOLUTION", "").strip().lower()
    if "x" not in raw:
        return None
    try:
        w, h = raw.split("x", 1)
        return int(w), int(h)
    except ValueError:
        return None


def _import_moviepy():
    try:
        from moviepy.editor import (  # type: ignore
            AudioClip,
            VideoFileClip,
            concatenate_videoclips,
        )
        return VideoFileClip, concatenate_videoclips, AudioClip, 1
    except ImportError:
        from moviepy import (  # type: ignore
            AudioClip,
            VideoFileClip,
            concatenate_videoclips,
        )
        return VideoFileClip, concatenate_videoclips, AudioClip, 2


def _with_audio(clip, audio, version: int):
    if hasattr(clip, "with_audio"):
        return clip.with_audio(audio)
    return clip.set_audio(audio)


def _resized(clip, size: Tuple[int, int], version: int):
    if hasattr(clip, "resized"):
        return clip.resized(new_size=size)
    return clip.resize(newsize=size)


def _cover(clip, size: Tuple[int, int], version: int):
    """Scale to fill ``size`` then centre-crop the overflow.

    Stretching a landscape render into 9:16 distorts faces, which is fatal for
    drama; cropping keeps proportions and loses only the edges.
    """
    target_w, target_h = size
    src_w, src_h = clip.size
    if (src_w, src_h) == (target_w, target_h):
        return clip
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(target_w, int(round(src_w * scale))), max(target_h, int(round(src_h * scale)))
    work = _resized(clip, (new_w, new_h), version)
    if (new_w, new_h) == (target_w, target_h):
        return work
    return _cropped(work, new_w / 2, new_h / 2, target_w, target_h, version)


def _cropped(clip, x_center: float, y_center: float, width: int, height: int, version: int):
    kwargs = dict(x_center=x_center, y_center=y_center, width=width, height=height)
    if hasattr(clip, "cropped"):  # moviepy 2.x
        return clip.cropped(**kwargs)
    return clip.crop(**kwargs)


def _with_fps(clip, fps: int, version: int):
    if hasattr(clip, "with_fps"):
        return clip.with_fps(fps)
    return clip.set_fps(fps)


def _concat_sync(
    video_paths: List[str],
    output_path: str,
    target_size: Optional[Tuple[int, int]] = None,
) -> None:
    """Synchronous moviepy concatenation (runs in thread executor)."""
    VideoFileClip, concatenate_videoclips, AudioClip, version = _import_moviepy()

    clips = []
    prepared = []
    try:
        for path in video_paths:
            clip = VideoFileClip(path)
            clips.append(clip)

        if not clips:
            raise ValueError("No video clips to concatenate")

        size = target_size or _env_resolution() or clips[0].size
        fps = int(os.environ.get("TARGET_FPS", "24"))

        for clip in clips:
            work = clip
            if tuple(work.size) != tuple(size):
                work = _cover(work, size, version)
            if int(round(work.fps or 0)) != fps:
                work = _with_fps(work, fps, version)
            if work.audio is None:
                # Silent track keeps every segment aligned through the final mux.
                work = _with_audio(
                    work, AudioClip(lambda t: [0, 0], duration=work.duration, fps=44100), version
                )
            prepared.append(work)

        method = "chain" if len({tuple(c.size) for c in prepared}) == 1 else "compose"
        final = (
            prepared[0]
            if len(prepared) == 1
            else concatenate_videoclips(prepared, method=method)
        )

        write_kwargs = dict(codec="libx264", audio_codec="aac", logger=None)
        try:
            final.write_videofile(output_path, fps=fps, **write_kwargs)
        except TypeError:
            final.write_videofile(output_path, **write_kwargs)

        if final is not prepared[0]:
            final.close()
    finally:
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass

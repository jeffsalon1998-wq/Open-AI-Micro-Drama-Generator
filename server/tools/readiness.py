"""Pre-flight checks: will a paid run actually work, and what will it cost?

Everything here is cheap (no generation). It answers the questions that
otherwise surface *after* coins are spent:

- are the API keys present?
- is the image host reachable, or is the reference URL a Drive HTML page?
- does every character have a *portrait* reference (not just a 3-figure sheet)?
- which costume will each character wear this episode?
- will the output framing survive the concat step?
"""
import os
from typing import List, Optional

from tools.character_refs import CHARACTERS, get
from tools.image_host import describe as host_description, is_public_url
from tools.video_provider import ImageProviderRouter, VideoProviderRouter

LEVEL_ORDER = {"ok": 0, "warn": 1, "error": 2}


def _check(name: str, status: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _aspect(ratio: str) -> float:
    try:
        a, b = (int(x) for x in ratio.split(":"))
        return a / b if b else 0.0
    except Exception:
        return 0.0


def run_checks(
    characters: Optional[List[str]] = None,
    episode: int = 1,
    aspect_ratio: str = "",
    duration: int = 15,
    verify_urls: bool = False,
    urls_to_check: Optional[List[str]] = None,
) -> dict:
    """Return {ready, worst, checks[], plan{}}."""
    aspect_ratio = aspect_ratio or os.environ.get("VIDEO_ASPECT_RATIO", "9:16")
    checks = []

    # ---- keys
    video = VideoProviderRouter()
    image = ImageProviderRouter()
    checks.append(
        _check(
            "video provider",
            "ok" if video.ready else "error",
            f"effective: {image.primary if not video.ready else video.primary.name}",
            "" if video.ready else "set A2E_API_KEY (or MUAPI_KEY)",
        )
    )
    checks.append(
        _check(
            "image provider",
            "ok" if image.ready else "error",
            f"effective: {image.primary}",
            "" if image.ready else "set GEMINI_API_KEY",
        )
    )

    # ---- hosting
    # Drive references bypass local hosting entirely: every still that reaches
    # the video provider is a Drive URL, not a published local file.
    host = host_description()
    uses_drive = any(w.reference_id for c in CHARACTERS.values() for w in c.wardrobe)
    if host["warning"] and uses_drive:
        checks.append(
            _check(
                "image hosting",
                "ok",
                "not needed — references are public Drive URLs",
                "",
            )
        )
    elif host["warning"]:
        checks.append(
            _check(
                "image hosting",
                "error",
                host["warning"],
                "set PUBLIC_BASE_URL, or configure a Drive reference for each character",
            )
        )
    else:
        checks.append(_check("image hosting", "ok", f"mode={host['mode']}", ""))

    # ---- characters
    wanted = [c.strip().lower() for c in (characters or [])]
    if not wanted:
        wanted = [k for k, v in CHARACTERS.items() if any(w.reference_id for w in v.wardrobe)]
    for key in wanted:
        char = CHARACTERS.get(key)
        if not char:
            checks.append(
                _check(f"character:{key}", "error", "unknown character",
                       f"known: {', '.join(CHARACTERS)}")
            )
            continue
        resolved = get(key, episode=episode)
        if not resolved or not resolved.file_id:
            checks.append(
                _check(f"character:{key}", "error",
                       f"no reference configured for episode {episode}",
                       f"set DRIVE_{key.upper()}_..._PORTRAIT_FILE_ID")
            )
            continue
        has_portrait = any(
            w.portrait_file_id and w.covers(episode) for w in char.wardrobe
        )
        checks.append(
            _check(
                f"character:{key}",
                "ok" if has_portrait else "warn",
                f"{resolved.file_id} ({'portrait' if has_portrait else 'sheet — may duplicate the figure'})",
                "" if has_portrait else "run tools/make_portraits.py and set the portrait id",
            )
        )

    # ---- reference URLs must be fetchable if asked
    if verify_urls:
        import asyncio
        from tools.image_host import verify_image_url

        targets = list(urls_to_check or [])
        if not targets:
            for key in wanted:
                resolved = get(key, episode=episode)
                if resolved and resolved.url:
                    targets.append(resolved.url)
        for url in targets:
            result = asyncio.get_event_loop().run_until_complete(verify_image_url(url)) \
                if not asyncio.get_event_loop().is_running() else {"ok": None, "error": "skip"}
            checks.append(
                _check(
                    "reference url",
                    "ok" if result.get("ok") else "error",
                    f"{url[:60]}… {result.get('content_type') or result.get('error')}",
                    "" if result.get("ok") else "use uc?id=<id>&export=download and share publicly",
                )
            )

    # ---- framing
    if abs(_aspect(aspect_ratio) - _aspect("9:16")) < 0.02:
        checks.append(_check("framing", "ok", f"{aspect_ratio} vertical, matches TARGET_RESOLUTION",
                             ""))
    else:
        target = os.environ.get("TARGET_RESOLUTION", "720x1280")
        tw, th = (int(x) for x in target.lower().split("x"))
        checks.append(
            _check(
                "framing",
                "warn" if abs((tw / th) - _aspect(aspect_ratio)) > 0.05 else "ok",
                f"clips {aspect_ratio} vs concat target {target}",
                "different ratios get cover-cropped — faces may be trimmed at the edges",
            )
        )

    # ---- cost
    per_second = float(os.environ.get("A2E_COINS_PER_SECOND", "8"))
    coins = int(round(duration * per_second))
    checks.append(
        _check("cost", "ok",
               f"~{coins} a2e coins for {duration}s (images are Gemini free tier)", "")
    )

    worst = "ok"
    for c in checks:
        if LEVEL_ORDER[c["status"]] > LEVEL_ORDER[worst]:
            worst = c["status"]

    return {
        "ready": worst != "error",
        "worst": worst,
        "checks": checks,
        "plan": {
            "episode": episode,
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "characters": wanted,
            "video_provider": video.primary.name if video.ready else None,
            "image_provider": image.primary,
            "estimated_coins": coins,
        },
    }

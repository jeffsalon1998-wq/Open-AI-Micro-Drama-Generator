"""Video provider router: MuAPI (Seedance/Kling) or a2e.ai, chosen by env.

The pipeline only needs one call shape, so providers are normalised here:

    provider.generate_from_image(prompt=..., image_url=..., duration=5, aspect_ratio="9:16")
        -> VideoResult(url=..., provider=..., duration_seconds=..., has_audio=...)

Environment:

    VIDEO_PROVIDER = auto | a2e | muapi      (default: auto)
        auto  -> a2e when A2E_API_KEY is set, otherwise muapi
    VIDEO_FALLBACK = true|false              (default: true)
        When true and both keys exist, a failing primary provider falls through
        to the other one instead of killing the scene.

a2e extras (ignored by muapi):

    A2E_MODEL_VERSION       default a2e-v2   (a2e | a2e-v2 | a2e-v2-flash)
    A2E_REFERENCE_MODE      default false    use reference-to-video (character lock)
    A2E_AUTO_AUDIO          default true     native audio on v2
    A2E_BASE_URL            default https://api.a2e.ai
    A2E_TIMEOUT             default 900s
    A2E_POLL_INTERVAL       default 3s
"""
import os
from typing import List, Optional

from tools.a2e_image_generator import A2EImageGenerator
from tools.a2e_video_generator import A2EVideoGenerator, A2EError, VideoResult
from tools.muapi_video_generator import MuAPIVideoGenerator


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


class MuAPIAdapter:
    """Wraps the existing MuAPI client in the common interface."""

    name = "muapi"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("MUAPI_KEY", "")
        self._gen = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @property
    def generator(self) -> MuAPIVideoGenerator:
        # Built lazily so an a2e-only deployment needs no MUAPI_KEY at all.
        if self._gen is None:
            if not self._api_key:
                raise RuntimeError("MUAPI_KEY is not set; MuAPI provider unavailable.")
            self._gen = MuAPIVideoGenerator(api_key=self._api_key)
        return self._gen

    async def generate_from_image(
        self,
        prompt: str,
        image_url: str,
        duration: int = 5,
        aspect_ratio: str = "9:16",
        negative_prompt: Optional[str] = None,
        reference_urls: Optional[List[str]] = None,
        auto_audio: bool = False,
    ) -> VideoResult:
        url = await self.generator.generate_video_from_image(
            prompt, image_url, duration=duration, aspect_ratio=aspect_ratio
        )
        return VideoResult(
            url=url,
            provider=self.name,
            duration_seconds=duration or 5,
            has_audio=False if "placeholder" in url or url.endswith("ForBiggerBlazes.mp4") else auto_audio,
        )


class A2EAdapter:
    """a2e.ai adapter — single continuous clips up to 20s with native audio."""

    name = "a2e"

    def __init__(self, api_key: Optional[str] = None):
        self._gen = A2EVideoGenerator(api_key=api_key or os.environ.get("A2E_API_KEY", ""))
        self.reference_mode = _env_bool("A2E_REFERENCE_MODE", False)
        self.auto_audio = _env_bool("A2E_AUTO_AUDIO", True)

    @property
    def available(self) -> bool:
        return self._gen.configured

    async def generate_from_image(
        self,
        prompt: str,
        image_url: str,
        duration: Optional[int] = None,
        aspect_ratio: str = "9:16",
        negative_prompt: Optional[str] = None,
        reference_urls: Optional[List[str]] = None,
        auto_audio: bool = True,
    ) -> VideoResult:
        """Render one clip. Reference mode conditions on character portraits
        (identity lock); otherwise the shot's first frame drives the motion."""
        refs = [u for u in (reference_urls or []) if u]
        if self.reference_mode and refs and self._gen.model_version.startswith("a2e-v2"):
            return await self._gen.generate_reference_to_video(
                prompt=prompt,
                reference_urls=refs,
                video_time=duration,
                aspect_ratio=aspect_ratio,
                negative_prompt=negative_prompt,
                auto_add_audio=auto_audio and self.auto_audio,
            )
        return await self._gen.generate_image_to_video(
            prompt=prompt,
            image_url=image_url,
            video_time=duration,
            aspect_ratio=aspect_ratio,
            negative_prompt=negative_prompt,
            auto_add_audio=auto_audio and self.auto_audio,
        )


class VideoProviderRouter:
    """Picks a provider and transparently falls back when one is unavailable."""

    def __init__(self, api_key: Optional[str] = None):
        self.muapi = MuAPIAdapter(api_key=api_key)
        self.a2e = A2EAdapter(api_key=os.environ.get("A2E_API_KEY", ""))
        self.requested = (os.environ.get("VIDEO_PROVIDER") or "auto").strip().lower()
        self.allow_fallback = _env_bool("VIDEO_FALLBACK", True)

    @property
    def primary(self):
        if self.requested == "a2e":
            return self.a2e
        if self.requested == "muapi":
            return self.muapi
        # auto: prefer a2e when it is actually configured
        return self.a2e if self.a2e.available else self.muapi

    @property
    def ready(self) -> bool:
        """True when at least one provider has credentials."""
        return self.a2e.available or self.muapi.available

    @property
    def secondary(self):
        return self.muapi if self.primary is self.a2e else self.a2e

    @property
    def name(self) -> str:
        return self.primary.name

    async def generate_from_image(self, **kwargs) -> VideoResult:
        primary = self.primary
        try:
            return await primary.generate_from_image(**kwargs)
        except Exception as exc:
            can_fall_back = (
                self.allow_fallback
                and self.secondary is not None
                and getattr(self.secondary, "available", False)
                and not (self.requested == "a2e" and not self.a2e.available)
            )
            if not can_fall_back:
                raise
            print(f"[video] {primary.name} failed ({exc}); falling back to {self.secondary.name}")
            return await self.secondary.generate_from_image(**kwargs)


def get_video_provider(api_key: Optional[str] = None) -> VideoProviderRouter:
    return VideoProviderRouter(api_key=api_key)


class ImageProviderRouter:
    """Same idea for stills: a2e's own image generator, else MuAPI.

    IMAGE_PROVIDER = auto | a2e | muapi   (auto -> a2e when A2E_API_KEY is set)
    The a2e site's image models (a2e auto / zimage / seedream) are preferred so the
    whole pipeline can run on a single site and a single paid balance.
    """

    name = "image-router"

    def __init__(self, api_key: Optional[str] = None):
        self.a2e = A2EImageGenerator(api_key=os.environ.get("A2E_API_KEY", ""))
        self._muapi_key = api_key or os.environ.get("MUAPI_KEY", "")
        self._muapi = None
        self.requested = (os.environ.get("IMAGE_PROVIDER") or "auto").strip().lower()

    @property
    def muapi(self):
        if self._muapi is None:
            from tools.muapi_image_generator import MuAPIImageGenerator
            if not self._muapi_key:
                raise RuntimeError("MUAPI_KEY is not set; MuAPI image provider unavailable.")
            self._muapi = MuAPIImageGenerator(api_key=self._muapi_key)
        return self._muapi

    @property
    def primary(self):
        if self.requested == "a2e":
            return "a2e"
        if self.requested == "muapi":
            return "muapi"
        return "a2e" if self.a2e.configured else "muapi"

    @property
    def ready(self) -> bool:
        return self.a2e.configured or bool(self._muapi_key)

    async def generate_image(self, prompt: str, aspect_ratio: str = "9:16", reference_url: Optional[str] = None) -> str:
        """Return an image URL. Character references are honoured where supported."""
        if self.primary == "a2e":
            if reference_url:
                result = await self.a2e.generate_image(
                    prompt, aspect_ratio=aspect_ratio, input_images=[reference_url]
                )
            else:
                result = await self.a2e.generate_image(prompt, aspect_ratio=aspect_ratio)
            print(f"[image] a2e {result.task_id} ({aspect_ratio})")
            return result.url
        if reference_url:
            return await self.muapi.generate_image_with_reference(
                prompt, reference_url, aspect_ratio=aspect_ratio
            )
        return await self.muapi.generate_image(prompt, aspect_ratio=aspect_ratio)


def get_image_provider(api_key: Optional[str] = None) -> ImageProviderRouter:
    return ImageProviderRouter(api_key=api_key)


__all__ = [
    "A2EAdapter",
    "ImageProviderRouter",
    "get_image_provider",
    "A2EError",
    "MuAPIAdapter",
    "VideoProviderRouter",
    "VideoResult",
    "get_video_provider",
]

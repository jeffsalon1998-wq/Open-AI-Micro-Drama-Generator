import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipelines.idea2video import Idea2VideoPipeline
from pipelines.script2video import Script2VideoPipeline
from agents.character_extractor import CharacterExtractor


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="MicroDrama AI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure outputs directory exists on startup
OUTPUTS_DIR = Path("outputs")
OUTPUTS_DIR.mkdir(exist_ok=True)

app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

# Gemini returns bytes; IMAGE_HOST_MODE=serve exposes them under PUBLIC_BASE_URL.
IMAGE_SERVE_DIR = Path(os.environ.get("IMAGE_SERVE_DIR", "outputs/images"))
IMAGE_SERVE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(IMAGE_SERVE_DIR)), name="images")


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------
# job structure:
# {
#   "status": "running" | "completed" | "failed",
#   "events": [...],           # list of JSON-serialisable dicts
#   "video_url": str | None,
#   "error": str | None,
#   "queue": asyncio.Queue,    # fed by pipeline, consumed by SSE
# }
jobs: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    idea: str
    user_requirement: str = ""
    style: str = "Cinematic"
    mode: str = "idea2video"  # "idea2video" or "script2video"
    script: str = ""          # used when mode == "script2video"
    # Retention / hooked-series options
    retention_mode: bool = False
    genre: str = "billionaire_revenge"
    episode_number: int = 1
    series_bible: str = ""    # paste previous bible JSON/text to keep continuity
    # Cost guards — every shot is one billed video generation.
    max_scenes: int = 0       # 0 = let the screenwriter decide
    max_shots: int = 0        # 0 = SHOTS_PER_SCENE env or LLM default


class GenerateResponse(BaseModel):
    job_id: str


class SampleRequest(BaseModel):
    """One-clip smoke test: image (site's own generator) -> a2e video.

    Defaults are the cheap, safe path: 9:16, one 15s clip, sound on.
    Set ``dry_run=True`` to see the exact plan and coin estimate without spending.
    """

    prompt: str = ""                    # video prompt; defaults to the Ep 1 hook
    image_prompt: str = ""              # still prompt; defaults to the hook frame
    aspect_ratio: str = "9:16"
    duration: int = 15
    auto_audio: bool = True
    model_mode: str = "image"           # image | reference
    reference_image_urls: list[str] = []
    skip_image: bool = False            # reuse an existing still instead
    image_url: str = ""
    negative_prompt: str = ""
    dry_run: bool = False
    verify_image_url: bool = True       # fetch the still first; fail before spending
    characters: list[str] = []          # e.g. ["lena"] -> use canon sheets as references
    use_identity_prompt: bool = True    # append the fixed identity block to the prompt


DEFAULT_IMAGE_PROMPT = """Cinematic vertical 9:16 poster frame, photoreal. Interior of a luxury gala ballroom at night, gold chandeliers, blurred crowd in evening wear behind shallow depth of field. Center frame: LENA CRUZ, 26, long black hair pulled back, sharp dark eyes, fair olive skin, wearing a black-and-white maid uniform with apron. Her head is turned slightly from a slap; one cheek is flushed. She is NOT crying - jaw set, eyes cold and steady, staring just past camera. To her left, out of focus: VIVIAN VEGA, 48, blonde bob, red lipstick, diamond necklace, hand still raised mid-slap. Dramatic amber key light from the left, deep cobalt shadows. 50mm look, film grain. No text, no watermark, no extra fingers."""

DEFAULT_VIDEO_PROMPT = """Create an exactly 15-second cinematic 9:16 vertical clip, photoreal, single continuous shot.

0:00-0:03  HOOK. Vivian's open palm SLAPS Lena across the face. Hard impact, hair snaps sideways, a glass on a passing tray topples. Handheld jolt. The frame lands on Lena's stunned face. Do not cut yet.

0:03-0:07  Vivian jabs a finger at her, diamond necklace swinging. Lena's jaw tightens. She does NOT flinch, does NOT cry. Guests behind them turn, phones rise.

0:07-0:11  Lena slowly lifts her head. Her eyes lock on Vivian - cold, unblinking. A slow push-in on Lena's face.

0:11-0:15  Lena's phone buzzes in her apron pocket. Without breaking eye contact she pulls it out, glances down.

DIALOGUE: Vivian (shouting, first lines): "You're fired! Thief!" Lena (quiet, final line): "You just fired the owner."

AUDIO: Sharp slap, room gasps, distant string quartet, phone buzz, deep sub drop on the last line.

CAMERA: Handheld at first, then locked-off slow push-in. Vertical framing, faces in upper third.

No morphing faces, no changing hair or eye color, no extra characters, no text overlay, no watermark, no camera shake after 0:07."""


def _cost_note(duration: int) -> dict:
    """Spend preview. Gemini images are free-tier; only video costs coins."""
    per_second = float(os.environ.get("A2E_COINS_PER_SECOND", "8"))
    return {
        "video_coins": int(round(duration * per_second)),
        "video_coin_note": (
            f"a2e video only: {per_second:g} coins/second "
            "(from the UI's 120 coins for 15s; calibrate with A2E_COINS_PER_SECOND)"
        ),
        "image": "Gemini free tier (set GEMINI_IMAGE_MODEL; billed to Google, not your a2e coins)",
    }


async def run_sample(job_id: str, req: SampleRequest) -> None:
    """Image -> one video clip, end to end, for a first paid smoke test."""
    from tools.video_provider import get_image_provider, get_video_provider

    job = jobs[job_id]
    queue: asyncio.Queue = job["queue"]

    async def emit(stage: str, message: str, progress: int, **extra) -> None:
        event = {"type": "progress", "stage": stage, "message": message, "progress": progress}
        event.update(extra)
        job["events"].append(event)
        await queue.put(event)

    image_prompt = req.image_prompt or DEFAULT_IMAGE_PROMPT
    video_prompt = req.prompt or DEFAULT_VIDEO_PROMPT
    estimate = _cost_note(req.duration)

    # Canon character sheets take priority over ad-hoc reference URLs.
    from tools.character_refs import get as get_character, identity_block
    canon = [c for c in (get_character(n) for n in req.characters) if c and c.file_id]
    if canon:
        req.reference_image_urls = [c.url for c in canon]
        req.model_mode = "reference"
        if req.use_identity_prompt:
            block = identity_block([c.name.lower().split()[0] for c in canon])
            if block and block not in video_prompt:
                video_prompt = f"{block}\n\n{video_prompt}"

    try:
        if req.dry_run:
            plan = {
                "aspect_ratio": req.aspect_ratio,
                "duration": req.duration,
                "auto_audio": req.auto_audio,
                "model_mode": req.model_mode,
                "reference_image_urls": req.reference_image_urls,
                "characters": req.characters,
                "image": {
                    "provider": get_image_provider().primary,
                    "aspect_ratio": req.aspect_ratio,
                    "prompt_chars": len(image_prompt),
                    "skipped": req.skip_image,
                },
                "video": {
                    "provider": get_video_provider().primary.name,
                    "generation_mode": "reference-to-video"
                    if req.model_mode == "reference" and req.reference_image_urls
                    else "image-to-video",
                    "video_time": req.duration,
                    "auto_add_audio": req.auto_audio,
                    "prompt_chars": len(video_prompt),
                },
                "estimated_coins": estimate,
            }
            job["status"] = "completed"
            done = {"type": "complete", "dry_run": True, "plan": plan, "progress": 100}
            job["events"].append(done)
            await queue.put(done)
            return

        await emit(
            "portraits",
            f"Planned spend: ~{estimate['video_coins']} a2e coins for the clip, "
            f"image via Gemini ({estimate['image']})",
            5,
        )

        if req.skip_image and req.image_url:
            image_url = req.image_url
            await emit("frames", "Reusing supplied still", 25)
        elif req.model_mode == "reference" and req.reference_image_urls:
            image_url = req.reference_image_urls[0]
            await emit("frames", "Using supplied reference still (no image generation)", 25)
        else:
            await emit("frames", f"Generating the hook frame with Gemini in {req.aspect_ratio}...", 10)
            image_url = await get_image_provider(api_key=None).generate_image(
                image_prompt,
                aspect_ratio=req.aspect_ratio,
                job_id=job_id,
                out_path=str(OUTPUTS_DIR / job_id / "frame.png"),
            )
            await emit(
                "frames",
                f"Hook frame ready: {image_url[:90]}",
                35,
                image_url=image_url,
                cached_frame=f"/outputs/{job_id}/frame.png",
            )

        # Never hand a paid endpoint an unreachable frame (Drive /view links are HTML).
        if req.verify_image_url:
            from tools.image_host import verify_image_url
            check = await verify_image_url(image_url)
            if not check["ok"]:
                raise RuntimeError(
                    f"Reference image is not fetchable: {check['error']} "
                    f"(url={image_url}). For Drive use "
                    f"https://drive.google.com/uc?id=<FILE_ID>&export=download and share "
                    f"the file as 'anyone with the link'."
                )
            await emit(
                "frames",
                f"Reference image verified ({check['content_type']}, {check['bytes']} bytes)",
                38,
            )

        await emit("video", f"Rendering one {req.duration}s clip in {req.aspect_ratio}...", 40)
        negative = req.negative_prompt or (
            "blurry, distorted, morphing face, changing eye color, text, watermark, extra fingers"
        )
        gen = get_video_provider()
        if req.model_mode == "reference" and req.reference_image_urls:
            result = await gen.generate_reference_to_video(
                prompt=video_prompt,
                reference_urls=req.reference_image_urls,
                video_time=req.duration,
                aspect_ratio=req.aspect_ratio,
                negative_prompt=negative,
                auto_add_audio=req.auto_audio,
            )
        else:
            result = await gen.generate_from_image(
                prompt=video_prompt,
                image_url=image_url,
                duration=req.duration,
                aspect_ratio=req.aspect_ratio,
                negative_prompt=negative,
                auto_audio=req.auto_audio,
            )

        await emit("concat", f"Task {result.task_id} complete - caching locally", 85)

        out_dir = OUTPUTS_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        from utils.video import download_video
        await download_video(result.url, str(out_dir / "sample.mp4"))

        job["status"] = "completed"
        job["video_url"] = f"/outputs/{job_id}/sample.mp4"
        done = {
            "type": "complete",
            "video_url": job["video_url"],
            "source_url": result.url,
            "task_id": result.task_id,
            "provider": result.provider,
            "duration_seconds": result.duration_seconds,
            "has_audio": result.has_audio,
            "image_url": image_url,
            "estimated_coins": estimate,
            "progress": 100,
        }
        job["events"].append(done)
        await queue.put(done)

    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        event = {"type": "error", "message": str(exc), "progress": -1}
        job["events"].append(event)
        await queue.put(event)
    finally:
        await queue.put(None)


class ImageRequest(BaseModel):
    """Gemini image generation only — costs zero a2e coins."""

    prompt: str
    aspect_ratio: str = "9:16"
    reference_images: list[str] = []
    name: str = ""


async def run_image_only(job_id: str, req: ImageRequest) -> None:
    from tools.image_host import publish_image
    from tools.video_provider import get_image_provider

    job = jobs[job_id]
    queue: asyncio.Queue = job["queue"]

    try:
        event = {"type": "progress", "stage": "frames",
                 "message": f"Generating image with Gemini in {req.aspect_ratio}...",
                 "progress": 10}
        job["events"].append(event)
        await queue.put(event)

        out_path = str(OUTPUTS_DIR / job_id / "image.png")
        url = await get_image_provider(api_key=None).generate_image(
            req.prompt,
            aspect_ratio=req.aspect_ratio,
            reference_url=(req.reference_images or [None])[0],
            job_id=job_id,
            out_path=out_path,
        )

        job["status"] = "completed"
        job["video_url"] = None
        done = {
            "type": "complete",
            "image_url": url,
            "cached_image": f"/outputs/{job_id}/image.png",
            "local_path": out_path,
            "aspect_ratio": req.aspect_ratio,
            "provider": "gemini",
            "a2e_coins_spent": 0,
            "progress": 100,
        }
        job["events"].append(done)
        await queue.put(done)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        err = {"type": "error", "message": str(exc), "progress": -1}
        job["events"].append(err)
        await queue.put(err)
    finally:
        await queue.put(None)


@app.post("/api/image", response_model=GenerateResponse)
async def generate_image_only(req: ImageRequest, background_tasks: BackgroundTasks):
    """Gemini image only (free tier, no a2e spend). Use this to test style first."""
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "running", "events": [], "video_url": None,
                    "error": None, "queue": asyncio.Queue()}
    background_tasks.add_task(run_image_only, job_id, req)
    return GenerateResponse(job_id=job_id)


@app.post("/api/sample", response_model=GenerateResponse)
async def sample_clip(req: SampleRequest, background_tasks: BackgroundTasks):
    """Render ONE sample clip: a2e image -> a2e video, 9:16.

    Call with ``{"dry_run": true}`` first to inspect the plan and coin estimate
    without spending, then again with ``dry_run: false``.
    """
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "running",
        "events": [],
        "video_url": None,
        "error": None,
        "queue": asyncio.Queue(),
    }
    background_tasks.add_task(run_sample, job_id, req)
    return GenerateResponse(job_id=job_id)


class RenderRequest(BaseModel):
    """Render one clip straight from a finished prompt — no screenwriter involved.

    Built for hand-authored, timecoded a2e prompts (the 0:00-0:04 beat style).
    """

    prompt: str
    mode: str = "reference"        # reference | image | flf2v | text
    reference_image_urls: list[str] = []   # mode=reference
    image_url: str = ""                    # mode=image / flf2v
    end_image_url: str = ""                # mode=flf2v
    duration: int = 0                      # 0 = A2E_DEFAULT_DURATION
    aspect_ratio: str = ""                 # "" = VIDEO_ASPECT_RATIO
    auto_audio: bool | None = None
    negative_prompt: str = ""
    name: str = ""


class JobResult(BaseModel):
    job_id: str
    status: str
    video_url: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------
async def run_pipeline(job_id: str, req: GenerateRequest) -> None:
    job = jobs[job_id]
    queue: asyncio.Queue = job["queue"]

    async def progress_callback(stage: str, message: str, progress: int) -> None:
        event = {
            "type": "progress",
            "stage": stage,
            "message": message,
            "progress": progress,
        }
        job["events"].append(event)
        await queue.put(event)

    try:
        if req.mode == "script2video":
            # Script2Video: user provides a single scene script
            pipeline = Script2VideoPipeline()
            character_extractor = CharacterExtractor()

            await progress_callback("characters", "Extracting characters...", 10)
            characters = await character_extractor.extract_characters(req.script or req.idea)

            output_dir = str(OUTPUTS_DIR / job_id / "scene_00")
            video_path = await pipeline.run(
                script=req.script or req.idea,
                characters=characters,
                user_requirement=req.user_requirement,
                style=req.style,
                working_dir=output_dir,
                progress_callback=progress_callback,
                scene_idx=0,
                base_progress=15,
                progress_range=80,
                retention_mode=req.retention_mode,
                max_shots=req.max_shots,
            )
        else:
            # Idea2Video: full agentic pipeline
            pipeline = Idea2VideoPipeline()
            video_path = await pipeline.run(
                idea=req.idea,
                user_requirement=req.user_requirement,
                style=req.style,
                job_id=job_id,
                progress_callback=progress_callback,
                retention_mode=req.retention_mode,
                genre=req.genre,
                episode_number=req.episode_number,
                series_bible=req.series_bible or None,
                max_shots=req.max_shots,
                max_scenes=req.max_scenes,
            )

        # Convert local path to URL
        rel_path = Path(video_path).relative_to(Path("."))
        video_url = f"/{rel_path}"

        job["status"] = "completed"
        job["video_url"] = video_url

        complete_event = {
            "type": "complete",
            "video_url": video_url,
            "progress": 100,
        }
        job["events"].append(complete_event)
        await queue.put(complete_event)

    except Exception as exc:
        error_msg = str(exc)
        job["status"] = "failed"
        job["error"] = error_msg

        error_event = {
            "type": "error",
            "message": error_msg,
            "progress": -1,
        }
        job["events"].append(error_event)
        await queue.put(error_event)

    finally:
        # Signal SSE consumers that the stream is done
        await queue.put(None)


async def run_render(job_id: str, req: RenderRequest) -> None:
    """Render a single clip from a provided prompt and cache it locally."""
    from tools.a2e_video_generator import A2EError, A2EVideoGenerator
    from utils.video import download_video

    job = jobs[job_id]
    queue: asyncio.Queue = job["queue"]

    async def emit(stage: str, message: str, progress: int) -> None:
        event = {"type": "progress", "stage": stage, "message": message, "progress": progress}
        job["events"].append(event)
        await queue.put(event)

    try:
        gen = A2EVideoGenerator()
        await emit("video", f"Submitting to a2e ({req.mode}, {req.duration or 'default'}s)...", 10)

        kwargs = dict(
            prompt=req.prompt,
            video_time=req.duration or None,
            aspect_ratio=req.aspect_ratio or os.environ.get("VIDEO_ASPECT_RATIO", "9:16"),
            negative_prompt=req.negative_prompt or None,
            auto_add_audio=req.auto_audio,
            name=req.name or None,
        )

        if req.mode == "reference":
            result = await gen.generate_reference_to_video(
                reference_urls=req.reference_image_urls, **kwargs
            )
        elif req.mode == "flf2v":
            result = await gen.generate_first_last_frame(
                image_url=req.image_url, end_image_url=req.end_image_url, **kwargs
            )
        elif req.mode == "text":
            result = await gen.generate_text_to_video(**kwargs)
        else:
            result = await gen.generate_image_to_video(image_url=req.image_url, **kwargs)

        await emit("video", f"Task {result.task_id} completed — downloading", 80)

        # a2e only keeps results 3 days, so cache the file with the job.
        out_dir = OUTPUTS_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        local_path = str(out_dir / "clip.mp4")
        await download_video(result.url, local_path)

        job["status"] = "completed"
        job["video_url"] = f"/outputs/{job_id}/clip.mp4"
        complete = {
            "type": "complete",
            "video_url": job["video_url"],
            "source_url": result.url,
            "task_id": result.task_id,
            "provider": result.provider,
            "duration_seconds": result.duration_seconds,
            "has_audio": result.has_audio,
            "progress": 100,
        }
        job["events"].append(complete)
        await queue.put(complete)

    except A2EError as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        event = {"type": "error", "message": str(exc), "progress": -1}
        job["events"].append(event)
        await queue.put(event)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        event = {"type": "error", "message": str(exc), "progress": -1}
        job["events"].append(event)
        await queue.put(event)
    finally:
        await queue.put(None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def _image_provider_info() -> dict:
    from tools.image_host import describe
    from tools.video_provider import ImageProviderRouter
    r = ImageProviderRouter()
    return {
        "requested": r.requested,
        "effective": r.primary,
        "gemini": {"configured": r.gemini.configured, "model": r.gemini.model},
        "a2e": {"configured": r.a2e.configured, "model_type": r.a2e.model_type,
                "resolution": r.a2e.resolution},
        "muapi": {"configured": bool(r._muapi_key)},
        "hosting": describe(),
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "microdrama-api"}


@app.post("/api/render", response_model=GenerateResponse)
async def render_clip(req: RenderRequest, background_tasks: BackgroundTasks):
    """Render one clip from a hand-written prompt via a2e (no LLM needed).

    Poll ``GET /api/status/{job_id}`` for progress; the completed event carries
    both the cached local URL and the original a2e ``source_url``.
    """
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "running",
        "events": [],
        "video_url": None,
        "error": None,
        "queue": asyncio.Queue(),
    }
    background_tasks.add_task(run_render, job_id, req)
    return GenerateResponse(job_id=job_id)


@app.get("/api/characters")
async def list_characters():
    """Canon character reference sheets (Drive URLs used for identity locking)."""
    from tools.character_refs import describe
    return describe()


@app.get("/api/providers")
async def providers():
    """Which video engine will render: a2e.ai and/or MuAPI, plus output settings."""
    from tools.video_provider import VideoProviderRouter
    router = VideoProviderRouter()
    return {
        "video_provider": router.requested,
        # Without a2e credentials, a2e vs muapi would render identically — say so.
        "selected": router.primary.name if router.ready else "none",
        "effective": ("a2e" if router.a2e.available else "muapi") if router.ready else "none",
        "fallback_enabled": router.allow_fallback,
        "a2e": {
            "configured": router.a2e.available,
            "model_version": router.a2e._gen.model_version,
            "reference_mode": router.a2e.reference_mode,
            "auto_audio": router.a2e.auto_audio,
        },
        "muapi": {"configured": router.muapi.available},
        "image_provider": _image_provider_info(),
        "aspect_ratio": os.environ.get("VIDEO_ASPECT_RATIO", "9:16"),
        "clip_seconds": int(os.environ.get("CLIP_SECONDS", "5")),
        "retention_clip_seconds": int(os.environ.get("RETENTION_CLIP_SECONDS", "15")),
        "shots_per_scene": int(os.environ.get("SHOTS_PER_SCENE", "0")),
        "a2e_default_duration": int(os.environ.get("A2E_DEFAULT_DURATION", "15")),
        "target_resolution": os.environ.get("TARGET_RESOLUTION", "720x1280"),
    }


@app.get("/api/genres")
async def list_genres():
    """Retention genre presets for the hooked-series UI."""
    from agents.retention import GENRE_PRESETS
    return {
        key: {"label": v["label"], "hook_formula": v["hook_formula"],
              "loop_question": v["loop_question"], "example_hook": v["example_hook"]}
        for key, v in GENRE_PRESETS.items()
    }


@app.post("/api/series-bible")
async def series_bible(req: GenerateRequest):
    """Generate a 10-episode hooked series bible (no video)."""
    from agents.screenwriter import Screenwriter
    sw = Screenwriter()
    bible = await sw.develop_series_bible(req.idea, req.genre, req.user_requirement)
    return {"bible": bible, "genre": req.genre}


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "running",
        "events": [],
        "video_url": None,
        "error": None,
        "queue": asyncio.Queue(),
    }
    background_tasks.add_task(run_pipeline, job_id, req)
    return GenerateResponse(job_id=job_id)


@app.get("/api/status/{job_id}")
async def status_stream(job_id: str):
    """SSE endpoint — streams progress events until job completes or fails."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    async def event_generator():
        # Replay already-emitted events first (in case client reconnects)
        for event in job["events"]:
            yield f"data: {json.dumps(event)}\n\n"

        # If job is already done, we've replayed everything — finish
        if job["status"] in ("completed", "failed"):
            return

        # Otherwise stream live events from queue
        queue: asyncio.Queue = job["queue"]
        while True:
            event = await queue.get()
            if event is None:
                # Sentinel — pipeline finished
                break
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("complete", "error"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/result/{job_id}", response_model=JobResult)
async def get_result(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return JobResult(
        job_id=job_id,
        status=job["status"],
        video_url=job.get("video_url"),
        error=job.get("error"),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)

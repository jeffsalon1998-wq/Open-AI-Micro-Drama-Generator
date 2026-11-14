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

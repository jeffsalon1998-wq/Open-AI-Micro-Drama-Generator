import json
import os
from pathlib import Path
from typing import Callable, Awaitable, Optional

from agents.screenwriter import Screenwriter
from agents.character_extractor import CharacterExtractor
from pipelines.script2video import Script2VideoPipeline
from utils.video import concatenate_videos


ProgressCallback = Callable[[str, str, int], Awaitable[None]]


class Idea2VideoPipeline:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("MUAPI_KEY", "")
        self.screenwriter = Screenwriter()
        self.character_extractor = CharacterExtractor()
        self.script2video = Script2VideoPipeline(api_key=self.api_key)

    async def run(
        self,
        idea: str,
        user_requirement: str,
        style: str,
        job_id: str,
        progress_callback: ProgressCallback,
        retention_mode: bool = False,
        genre: str = "billionaire_revenge",
        episode_number: int = 1,
        series_bible: Optional[str] = None,
        max_shots: int = 0,
        max_scenes: int = 0,
    ) -> str:
        """
        Full pipeline: idea → story → scenes → videos → final concatenated video.
        When retention_mode=True, the story is built as a hooked episode
        (cold open, open loop, escalation, cliffhanger) and saved with a
        hook audit + series bible for the next episode.
        Returns the path to the final video.
        """
        outputs_dir = Path("outputs") / job_id
        outputs_dir.mkdir(parents=True, exist_ok=True)

        # Stage 1: Develop story (retention-aware when enabled)
        await progress_callback(
            "screenwriter",
            "Developing hooked episode story..." if retention_mode else "Developing story...",
            10,
        )
        story = await self.screenwriter.develop_story(
            idea, user_requirement,
            retention_mode=retention_mode, genre=genre,
            episode_number=episode_number, series_bible=series_bible,
        )
        (outputs_dir / "story.txt").write_text(story, encoding="utf-8")

        # Stage 1b: Hook audit (retention mode only) — saved for review
        if retention_mode:
            await progress_callback("screenwriter", "Auditing hook & retention...", 15)
            try:
                audit = await self.screenwriter.audit_hook(story)
                (outputs_dir / "hook_audit.json").write_text(
                    json.dumps(audit, indent=2), encoding="utf-8"
                )
                await progress_callback(
                    "screenwriter",
                    f"Hook audit: {audit.get('overall', '?')}/100 — {audit.get('verdict', '')}",
                    18,
                )
            except Exception as e:
                print(f"Warning: hook audit failed: {e}")

        # Stage 2: Extract characters from story
        await progress_callback(
            "characters", "Extracting characters...", 20
        )
        characters = await self.character_extractor.extract_characters(story)

        # Stage 3: Write scene scripts
        await progress_callback(
            "screenwriter", "Writing scene scripts...", 25
        )
        scene_scripts = await self.screenwriter.write_script_based_on_story(
            story, user_requirement,
            retention_mode=retention_mode, genre=genre,
            episode_number=episode_number,
        )

        if not scene_scripts:
            raise RuntimeError("Screenwriter produced no scene scripts")

        # Budget guard: scenes x shots = paid clips.
        if max_scenes and len(scene_scripts) > max_scenes:
            print(f"[pipeline] trimming {len(scene_scripts)} scenes to {max_scenes}")
            await progress_callback(
                "screenwriter",
                f"Trimming to {max_scenes} scenes (budget guard)...",
                28,
            )
            scene_scripts = scene_scripts[:max_scenes]

        # Save scripts for reuse in next episode / manual review
        (outputs_dir / "scene_scripts.json").write_text(
            json.dumps(scene_scripts, indent=2), encoding="utf-8"
        )

        # Stage 4: Generate video for each scene
        scene_video_paths = []
        num_scenes = len(scene_scripts)

        # Distribute progress range 30-88 across scenes
        progress_per_scene = int(58 / max(num_scenes, 1))

        for scene_idx, scene_script in enumerate(scene_scripts):
            scene_dir = str(outputs_dir / f"scene_{scene_idx:02d}")
            base_progress = 30 + scene_idx * progress_per_scene

            try:
                scene_video = await self.script2video.run(
                    script=scene_script,
                    characters=characters,
                    user_requirement=user_requirement,
                    style=style,
                    working_dir=scene_dir,
                    progress_callback=progress_callback,
                    scene_idx=scene_idx,
                    base_progress=base_progress,
                    progress_range=progress_per_scene,
                    retention_mode=retention_mode,
                    max_shots=max_shots,
                )
                scene_video_paths.append(scene_video)
            except Exception as e:
                # Log but continue with remaining scenes
                print(f"Warning: scene {scene_idx} failed: {e}")
                await progress_callback(
                    "video",
                    f"Scene {scene_idx + 1} failed, continuing...",
                    base_progress + progress_per_scene,
                )

        if not scene_video_paths:
            raise RuntimeError("All scenes failed to generate")

        # Stage 5: Concatenate all scene videos
        await progress_callback(
            "concat", "Combining all scenes into final video...", 90
        )
        final_video_path = str(outputs_dir / "final_video.mp4")
        await concatenate_videos(scene_video_paths, final_video_path)

        await progress_callback(
            "concat", "Video generation complete!", 100
        )
        return final_video_path

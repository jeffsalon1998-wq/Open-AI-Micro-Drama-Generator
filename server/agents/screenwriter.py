import json
import os
from typing import List, Optional

from agents.retention import (
    RETENTION_RULES,
    build_episode_script_prompt,
    build_episode_story_prompt,
    build_hook_audit_prompt,
    build_series_bible_prompt,
    heuristic_audit,
    parse_json_response,
)
from tools.muapi_llm import MuAPILLM

_FALLBACK_STORY = """
Title: The Last Signal

A lone engineer named Maya, stationed at a remote arctic research base, intercepts a mysterious transmission.
The signal pulses with an unknown pattern — not noise, but intent. As a blizzard closes in and communication
with the outside world fails, Maya decodes the message piece by piece, racing against time and the elements.
The climax reveals the signal is a beacon left by an earlier expedition — a warning, and a map.
She follows it into the ice and uncovers a buried chamber, its contents changing everything she thought she knew.
"""

_FALLBACK_SCENES = json.dumps({
    "scenes": [
        {
            "scene_number": 1,
            "title": "The Transmission",
            "script": (
                "INT. ARCTIC RESEARCH BASE - COMMUNICATIONS ROOM - NIGHT\n"
                "Maya sits at her console, surrounded by flickering monitors. Snow hammers the windows. "
                "A burst of static, then a rhythmic pulse fills the speakers. "
                "MAYA (leaning forward): That's not random noise. She grabs her headset and starts recording.\n"
                "The pulse repeats — three long, two short. Her fingers fly across the keyboard."
            )
        },
        {
            "scene_number": 2,
            "title": "Into the Ice",
            "script": (
                "EXT. ARCTIC TUNDRA - DAWN\n"
                "The blizzard has passed. Maya trudges through knee-deep snow, GPS in hand, following the decoded coordinates. "
                "The landscape is vast and white. A faint rectangular outline breaks the snow ahead — "
                "something buried, geometric. She kneels and brushes the surface. "
                "MAYA (whispering): It's a door."
            )
        }
    ]
})

# Hooked fallback — used when retention_mode=True and LLM is unavailable
_FALLBACK_HOOKED_STORY = """
HOOK (first 3 seconds): A slap echoes across a gala. Lena, the maid, holds her cheek as Vivian screams: "You're fired!" On-screen text: "She was fired for stealing. She wasn't the thief."
OPEN LOOP: Who really stole the necklace — and why does Lena's phone say "Ma'am, you now own 51% of Vega Group"?
BEATS: 1) Humiliation at gala. 2) Lena fired on camera. 3) Phone rings — acquisition complete. 4) CEO Marcus kneels. 5) Lena reveals she bought the company. 6) Vivian's face drops. 7) Lena whispers the real question.
TWIST: The thief was Vivian herself — she planted the necklace to frame Lena.
PAYOFF: Lena takes back the company. One loop closed.
CLIFFHANGER (final 3 seconds): Lena stares at Vivian: "Now... let's talk about who burned my house down." CUT TO BLACK.
"""

_FALLBACK_HOOKED_SCENES = json.dumps({
    "scenes": [
        {
            "scene_number": 1,
            "title": "You Fired the Wrong Maid",
            "script": (
                "INT. VEGA GROUP GALA - NIGHT\n"
                "COLD OPEN: A slap echoes. Lena, a maid in uniform, holds her cheek. "
                "VIVIAN (screaming): You're fired! Thief! "
                "On-screen text: She was fired for stealing. She wasn't the thief. "
                "Lena's phone rings. LENA (answering, calm): Yes? "
                "VOICE ON PHONE: Ma'am, acquisition complete. You now own 51% of Vega Group. "
                "Marcus, the CEO, drops to one knee. MARCUS: Ma'am Lena, welcome back. "
                "VIVIAN (shocked): What?! "
                "LENA (to camera, quiet): Now... let's talk about who burned my house down. CUT TO BLACK."
            )
        },
        {
            "scene_number": 2,
            "title": "The Real Thief",
            "script": (
                "INT. VEGA GROUP BOARDROOM - NIGHT\n"
                "Lena stands at the head of the boardroom table. Vivian is frozen. "
                "LENA: The necklace was in your bag, Vivian. Security footage proves it. "
                "VIVIAN (panicking): You — you were dead! "
                "LENA: I was presumed dead. There's a difference. "
                "Marcus slides a folder across the table. MARCUS: The fire report. Arson. "
                "Lena opens it. Her hands shake. "
                "LENA (whispering): The signature... it's yours. CUT TO BLACK."
            )
        }
    ]
})


class Screenwriter:
    def __init__(self):
        self.llm = MuAPILLM()

    async def develop_story(
        self,
        idea: str,
        user_requirement: str,
        retention_mode: bool = False,
        genre: str = "billionaire_revenge",
        episode_number: int = 1,
        series_bible: Optional[str] = None,
    ) -> str:
        if retention_mode:
            system_prompt = (
                "You are a ruthless retention screenwriter for vertical micro-dramas. "
                "Every episode hooks in 3 seconds, loops a question, escalates every 5 seconds, "
                "pays off one loop, and ends on a cliffhanger that forces the next tap. "
                + RETENTION_RULES
            )
            prompt = build_episode_story_prompt(
                idea, genre, episode_number, series_bible, user_requirement
            )
            return await self.llm.complete(
                prompt, system_prompt=system_prompt, timeout=120, fallback=_FALLBACK_HOOKED_STORY
            )

        system_prompt = (
            "You are a professional screenwriter and story developer. "
            "Your task is to expand a brief idea into a compelling story outline. "
            "Include premise, protagonist, conflict, rising action, climax, and resolution. "
            "Keep it suitable for a short video (1-3 minutes). "
            "Write in clear prose, focusing on visual storytelling."
        )
        prompt = f"""Develop a story based on this idea:

Idea: {idea}

Additional requirements: {user_requirement if user_requirement else "None"}

Write a detailed story outline that can be translated into a short video. Include:
- Setting and atmosphere
- Main character(s) and their goal
- The conflict or journey
- Emotional arc
- Visual climax moment
- Resolution

Write the story outline as flowing prose."""

        return await self.llm.complete(
            prompt, system_prompt=system_prompt, timeout=120, fallback=_FALLBACK_STORY
        )

    async def write_script_based_on_story(
        self,
        story: str,
        user_requirement: str,
        retention_mode: bool = False,
        genre: str = "billionaire_revenge",
        episode_number: int = 1,
    ) -> List[str]:
        if retention_mode:
            system_prompt = (
                "You are a ruthless retention screenwriter. "
                "Break the story into 2-4 scene scripts that hook in 3 seconds, escalate every beat, "
                "and end on a cliffhanger. Respond ONLY with valid JSON — no markdown, no explanation, just JSON."
            )
            prompt = build_episode_script_prompt(story, genre, episode_number, user_requirement)
            raw = await self.llm.complete(
                prompt, system_prompt=system_prompt, timeout=120, fallback=_FALLBACK_HOOKED_SCENES
            )
            data = parse_json_response(raw)
            return [scene["script"] for scene in data.get("scenes", [])]

        system_prompt = (
            "You are a professional screenwriter. "
            "Your task is to break a story outline into individual scene scripts. "
            "Each scene should be self-contained, visually rich, and suitable for video generation. "
            "Write 2-4 scenes maximum. "
            "Respond ONLY with valid JSON — no markdown, no explanation, just JSON."
        )
        prompt = f"""Based on this story outline, write individual scene scripts for a short video.

Story Outline:
{story}

Additional requirements: {user_requirement if user_requirement else "None"}

Return a JSON object with this exact structure:
{{
  "scenes": [
    {{
      "scene_number": 1,
      "title": "Scene title",
      "script": "Full scene script with action lines, dialogue, and scene description."
    }}
  ]
}}

Rules:
- Create 2-4 scenes that together tell the complete story
- Each scene script should be visually descriptive and filmable
- Include character actions, dialogue, and environmental details"""

        raw = await self.llm.complete(
            prompt, system_prompt=system_prompt, timeout=120, fallback=_FALLBACK_SCENES
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return [scene["script"] for scene in data.get("scenes", [])]

    async def develop_series_bible(
        self, idea: str, genre: str = "billionaire_revenge", user_requirement: str = ""
    ) -> str:
        """Generate a full 10-episode series bible (JSON string)."""
        system_prompt = (
            "You are a series bible writer for hooked vertical micro-dramas. "
            "Design a 10-episode arc where every episode hooks, loops, escalates, pays off one loop, "
            "and ends on a cliffhanger. Respond ONLY with valid JSON."
        )
        prompt = build_series_bible_prompt(idea, genre, user_requirement)
        fallback = json.dumps({
            "title": "THE MAID WHO BOUGHT THE EMPIRE",
            "logline": "A humiliated maid is fired on camera — then buys the company that fired her.",
            "genre": genre,
            "loop_question": "Who really owns the empire — and who burned the house down?",
            "protagonist": "Lena Cruz, secret heiress presumed dead",
            "antagonist": "Vivian Vega, stepmother who framed her",
            "secret": "Lena is the true Vega heir; the fire was arson by Vivian",
            "episodes": [
                {
                    "episode_number": 1,
                    "title": "You Fired the Wrong Maid",
                    "hook_line": "You're fired! Thief!",
                    "open_loop": "Why does her phone say she owns 51% of Vega Group?",
                    "beats": ["Slap at gala", "Fired on camera", "Phone: acquisition complete", "CEO kneels", "Lena takes the company"],
                    "twist": "Vivian planted the necklace herself",
                    "payoff": "Lena buys the company",
                    "cliffhanger": "Now... let's talk about who burned my house down.",
                }
            ],
        })
        return await self.llm.complete(
            prompt, system_prompt=system_prompt, timeout=180, fallback=fallback
        )

    async def audit_hook(self, script_text: str) -> dict:
        """Score hook/retention/cliffhanger. Falls back to heuristic audit offline."""
        system_prompt = "You are a ruthless retention editor. Respond ONLY with valid JSON."
        prompt = build_hook_audit_prompt(script_text)
        try:
            raw = await self.llm.complete(
                prompt, system_prompt=system_prompt, timeout=120, fallback=None
            )
            return parse_json_response(raw)
        except Exception:
            return heuristic_audit(script_text)

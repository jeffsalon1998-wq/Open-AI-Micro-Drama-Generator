"""
Retention Engine — makes every micro-drama hooked & high-retention.

Framework (proven in ReelShort / DramaBox / vertical short-form):
  1. COLD OPEN (0-3s): mid-action, shocking line/visual. No title card, no slow establishing shot.
  2. OPEN LOOP: pose a question, delay the answer. Viewer stays to get it.
  3. PATTERN INTERRUPT every ~5s: new info, twist, or escalation. No dead air.
  4. ESCALATION: each beat raises stakes. Humiliation -> shock -> revenge satisfaction.
  5. PAYOFF + NEW MYSTERY: close one loop (satisfaction), open a bigger one (next episode).
  6. CLIFFHANGER END: end on a reveal/question, never on resolution.
  7. RECURRING LOOP QUESTION: one question drives the whole series.

This module is pure logic (no API key needed at import) + prompt builders
used by Screenwriter / StoryboardArtist when retention_mode=True.
"""
import json
import re
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Genre presets — proven viral engines
# ---------------------------------------------------------------------------
GENRE_PRESETS: Dict[str, Dict[str, str]] = {
    "billionaire_revenge": {
        "label": "Billionaire Revenge",
        "hook_formula": "Public humiliation -> secret power reveal. Protagonist is slapped/fired in public, then the room kneels to her.",
        "loop_question": "Who really owns the empire — and who burned the house down?",
        "escalation_engine": "Each episode one betrayer falls, and a bigger secret about the protagonist's identity surfaces.",
        "example_hook": "\"You fired the wrong maid.\" — then the CEO kneels.",
        "tone": "Shameless, fast, revenge-satisfying. Rich people behaving badly, poor girl winning.",
    },
    "rejected_mate": {
        "label": "Rejected Mate (Werewolf)",
        "hook_formula": "Rejection in public -> power awakening. Fated mate rejects her, she transforms / returns as Luna Queen.",
        "loop_question": "Why did the Alpha reject his fated mate — and what is she hiding?",
        "escalation_engine": "Each episode her hidden power grows; each ex-lover who wronged her begs for forgiveness.",
        "example_hook": "\"I reject you as my mate.\" — her eyes turn gold.",
        "tone": "Feral, romantic, possessive. High emotion, high heat, high betrayal.",
    },
    "time_loop": {
        "label": "Time-Loop Murder",
        "hook_formula": "Death in first 10 seconds -> wake up, same day. Protagonist must prevent her own murder.",
        "loop_question": "Who kills her — and why does the day reset?",
        "escalation_engine": "Each loop she learns one clue and changes one outcome, but the killer adapts.",
        "example_hook": "She dies at 11:59pm. Wakes up at 8:00am. Same day. Again.",
        "tone": "Tense, paranoid, puzzle-box. Every episode ends with a new death or clue.",
    },
    "fake_heiress": {
        "label": "Fake Heiress",
        "hook_formula": "Fake identity exposed in public -> real identity is even bigger. Impostor revealed... as the true heir.",
        "loop_question": "Which heiress is real — and who forged the DNA test?",
        "escalation_engine": "Each episode a document, witness, or test flips who is lying.",
        "example_hook": "\"She's not my daughter.\" — then the real will is read aloud.",
        "tone": "Gossip, glamour, betrayal. Everyone is lying about something.",
    },
    "ai_husband": {
        "label": "AI Husband",
        "hook_formula": "Perfect husband revealed as AI -> AI reveals it knows a secret no human should know.",
        "loop_question": "Is he AI, human, or something else — and why did he marry her?",
        "escalation_engine": "Each episode he proves more human (or less), and a darker purpose emerges.",
        "example_hook": "\"I was programmed to love you. I was NOT programmed to know this.\"",
        "tone": "Uncanny, romantic, sci-fi. Intimate scenes, existential twists.",
    },
    "murder_mystery": {
        "label": "Murder Mystery",
        "hook_formula": "Body found in first 10 seconds -> everyone is a suspect, including the narrator.",
        "loop_question": "Who did it — and why does the detective hide a clue?",
        "escalation_engine": "Each episode one suspect is cleared and a closer one is exposed.",
        "example_hook": "The victim opens her eyes. \"It was him.\" Then she dies.",
        "tone": "Dark, twisty, confession-driven. Flashbacks reveal lies.",
    },
}

DEFAULT_GENRE = "billionaire_revenge"

# ---------------------------------------------------------------------------
# Retention rules injected into LLM prompts
# ---------------------------------------------------------------------------
RETENTION_RULES = """
RETENTION RULES (non-negotiable for hooked, high-retention micro-drama):
1. COLD OPEN (0-3s): Start MID-ACTION with a shocking line or visual. NO title card, NO slow establishing shot, NO "once upon a time". First frame must make a scroller stop.
2. OPEN LOOP: In the first 10 seconds, pose ONE clear question (Who is she? Who did it? What is he hiding?). Do NOT answer it this episode. The viewer must stay to find out.
3. PATTERN INTERRUPT every ~5 seconds: every beat must add new information, a twist, a betrayal, or an escalation. No filler, no walking, no small talk.
4. ESCALATION LADDER: stakes must rise beat by beat. Humiliation -> shock -> danger -> revenge satisfaction. Never go flat.
5. EMOTIONAL SPIKES: alternate humiliation/anger (makes them hate the villain) with shock/satisfaction (makes them love the hero). Villains must be hateable in 5 seconds.
6. PAYOFF + NEW MYSTERY: close ONE small loop for satisfaction, but open a BIGGER loop before the end. Never resolve the main secret.
7. CLIFFHANGER END (final 3s): end on a REVEAL, a QUESTION, or a DANGER — never on resolution, never on "the end". The last line must force the viewer to tap Next Episode.
8. DIALOGUE: short, punchy, quotable lines. Max ~12 words per line. Every line must either reveal, threaten, or shock. No exposition dumps.
9. VISUAL HOOKS: every shot must contain a visual surprise (a slap, a kneel, a phone ringing, eyes changing color, a door opening). If a shot could be a screensaver, rewrite it.
10. RECURRING LOOP: keep returning to the series' central question. Each episode answers a piece and raises a bigger piece.
"""

EPISODE_BEAT_STRUCTURE = """
EPISODE BEAT STRUCTURE (60-90 seconds, 5-7 beats):
Beat 1 (0-3s)   COLD OPEN HOOK: shocking line/visual, mid-action. On-screen text hook.
Beat 2 (3-15s)  SETUP + OPEN LOOP: who/what/who-did-it question posed. Protagonist humiliated or in danger.
Beat 3 (15-30s) ESCALATION 1: villain pushes harder. New information drops.
Beat 4 (30-45s) TWIST: secret revealed / betrayal exposed / power shown. Pattern interrupt.
Beat 5 (45-60s) ESCALATION 2 / REVENGE PAYOFF: protagonist strikes back. One loop closed (satisfaction).
Beat 6 (60-75s) BIGGER MYSTERY: new secret surfaces that reframes everything.
Beat 7 (75-90s) CLIFFHANGER: final reveal/question/danger. Cut to black on the shock line.
"""

STORYBOARD_RETENTION_RULES = """
RETENTION STORYBOARD RULES:
- Shot 1 is the HOOK shot: extreme close-up or shocking action, NOT a wide establishing shot. It must work as a thumbnail.
- Every shot = one pattern interrupt: new location, new information, new emotion, or new danger. No two consecutive shots can feel the same.
- Alternate shot scales: close-up (emotion) -> medium (action) -> close-up (shock). Avoid long static wides.
- Final shot is the CLIFFHANGER shot: freeze on the reveal/question. Slow push-in on the shocked face, then cut.
- Motion must be purposeful: push-in on realization, snap-zoom on shock, whip-pan on betrayal. No slow drift.
- Audio: dialogue lines are the hook. Put the most shocking line in Shot 1 and the cliffhanger line in the final shot.
"""


def get_genre_preset(genre: str) -> Dict[str, str]:
    return GENRE_PRESETS.get(genre or DEFAULT_GENRE, GENRE_PRESETS[DEFAULT_GENRE])


def build_series_bible_prompt(idea: str, genre: str, user_requirement: str) -> str:
    preset = get_genre_preset(genre)
    return f"""Design a HOOKED, HIGH-RETENTION micro-drama SERIES bible.

Idea: {idea}
Genre preset: {preset['label']}
Hook formula: {preset['hook_formula']}
Loop question: {preset['loop_question']}
Escalation engine: {preset['escalation_engine']}
Example hook: {preset['example_hook']}
Tone: {preset['tone']}
Additional requirements: {user_requirement or "None"}

{RETENTION_RULES}

Return ONLY valid JSON with this exact structure:
{{
  "title": "Series title (short, punchy, searchable)",
  "logline": "One sentence that makes someone click",
  "genre": "{preset['label']}",
  "loop_question": "The one question driving the whole series",
  "protagonist": "Name + secret + goal",
  "antagonist": "Name + why they are hateable",
  "secret": "Hidden truth revealed gradually across episodes",
  "episodes": [
    {{
      "episode_number": 1,
      "title": "Episode title",
      "hook_line": "First spoken line / on-screen text (first 3 seconds)",
      "open_loop": "Question posed but NOT answered this episode",
      "beats": ["Beat 1...", "Beat 2...", "Beat 3...", "Beat 4...", "Beat 5..."],
      "twist": "Mid-episode twist",
      "payoff": "One small loop closed (satisfaction)",
      "cliffhanger": "Final 3 seconds — must force Next Episode"
    }}
  ]
}}

Rules:
- Design 10 episodes. Each episode: hook -> loop -> escalation -> twist -> payoff -> BIGGER mystery -> cliffhanger.
- Episode 1 must hook in 3 seconds and end on the biggest cliffhanger.
- Every cliffhanger must be a reveal, question, or danger — never a resolution.
- Keep dialogue-style hook lines under 12 words.
"""


def build_episode_story_prompt(
    idea: str,
    genre: str,
    episode_number: int,
    series_bible: Optional[str],
    user_requirement: str,
) -> str:
    preset = get_genre_preset(genre)
    bible_block = f"\nSeries Bible (follow it exactly):\n{series_bible}\n" if series_bible else ""
    return f"""Write a HOOKED, HIGH-RETENTION episode story outline.

Idea: {idea}
Genre: {preset['label']} — {preset['hook_formula']}
Episode number: {episode_number}
{bible_block}
Additional requirements: {user_requirement or "None"}

{RETENTION_RULES}
{EPISODE_BEAT_STRUCTURE}

Write the episode as flowing prose BUT structured with these labeled sections:
- HOOK (first 3 seconds): exact first line + visual
- OPEN LOOP: the question posed
- BEATS: 5-7 escalating beats, each one line
- TWIST: mid-episode twist
- PAYOFF: one loop closed
- CLIFFHANGER (final 3 seconds): exact final line + visual, cut to black

Make it filmable: specific locations, actions, dialogue lines, visual surprises.
"""


def build_episode_script_prompt(
    story: str,
    genre: str,
    episode_number: int,
    user_requirement: str,
) -> str:
    preset = get_genre_preset(genre)
    return f"""Break this hooked episode story into individual scene scripts.

Story:
{story}

Genre: {preset['label']}
Episode number: {episode_number}
Additional requirements: {user_requirement or "None"}

{RETENTION_RULES}
{EPISODE_BEAT_STRUCTURE}

Return ONLY valid JSON:
{{
  "scenes": [
    {{
      "scene_number": 1,
      "title": "Scene title",
      "script": "Full scene script. MUST start with the COLD OPEN HOOK (mid-action, shocking line). Include action lines, short punchy dialogue (max 12 words/line), and end with the CLIFFHANGER line if this is the final scene. Every beat must escalate. No filler."
    }}
  ]
}}

Rules:
- Create 2-4 scenes that together tell the complete episode.
- Scene 1 MUST open with the hook — no establishing shot, no title card.
- Final scene MUST end on the cliffhanger — cut to black on the shock line.
- Dialogue: short, quotable, each line reveals/threatens/shocks.
"""


def build_hook_audit_prompt(script_text: str) -> str:
    return f"""You are a ruthless retention editor for vertical micro-dramas (ReelShort/DramaBox style).
Audit this episode script for hook strength and retention. Be harsh — if it would make a scroller swipe away, say so.

Script:
{script_text}

{RETENTION_RULES}

Return ONLY valid JSON:
{{
  "hook_score": 0-100,
  "retention_score": 0-100,
  "cliffhanger_score": 0-100,
  "overall": 0-100,
  "strengths": ["..."],
  "fixes": ["specific fix 1", "specific fix 2"],
  "verdict": "One sentence: would you tap Next Episode? Why?"
}}

Scoring guide:
- 90+: viral. Hooks in 3s, loops, escalates, cliffhanger forces next tap.
- 70-89: strong. Minor fixes needed.
- 50-69: weak. Would lose scrollers.
- Below 50: rewrite. No hook or no cliffhanger.
"""


# ---------------------------------------------------------------------------
# Heuristic fallback audit (no LLM needed) — used in tests / offline mode
# ---------------------------------------------------------------------------
def heuristic_audit(script_text: str) -> Dict:
    text = (script_text or "").strip()
    lower = text.lower()
    words = len(text.split())
    hook_score = 50
    retention_score = 50
    cliffhanger_score = 50
    strengths: List[str] = []
    fixes: List[str] = []

    # Hook: shocking opening words / dialogue early
    first_200 = lower[:200]
    if "?" in first_200 or "!" in first_200 or ":" in first_200:
        hook_score += 15
        strengths.append("Opens with dialogue/question — immediate hook")
    else:
        fixes.append("Open with a shocking spoken line in the first 3 seconds, not description")

    if any(w in first_200 for w in ["slap", "kneel", "dead", "kill", "reject", "fire", "billion", "heir", "murder", "die"]):
        hook_score += 15
        strengths.append("Opening contains a high-stakes visual/action hook")

    # Retention: escalation words
    escalation_hits = sum(1 for w in ["but", "then", "suddenly", "reveals", "secret", "truth", "betray", "revenge", "kneel", "shock"] if w in lower)
    retention_score += min(escalation_hits * 4, 25)
    if escalation_hits >= 3:
        strengths.append("Multiple escalation/twist beats present")
    else:
        fixes.append("Add pattern interrupts every ~5s: new info, twist, or escalation per beat")

    # Cliffhanger: ends on question / reveal / danger
    last_300 = lower[-300:]
    if "?" in last_300 or any(w in last_300 for w in ["who", "what", "why", "how", "wait", "no!", "truth", "secret", "die", "kill"]):
        cliffhanger_score += 25
        strengths.append("Ends on a question/reveal — cliffhanger present")
    else:
        fixes.append("End on a reveal/question/danger — never on resolution")

    # Length sanity
    if words < 40:
        fixes.append("Script is very short — add beats so retention has material to work with")
    elif words > 600:
        fixes.append("Script is long — tighten to 60-90s of screen time, cut filler")

    hook_score = max(0, min(100, hook_score))
    retention_score = max(0, min(100, retention_score))
    cliffhanger_score = max(0, min(100, cliffhanger_score))
    overall = round((hook_score + retention_score + cliffhanger_score) / 3)
    verdict = (
        "Would tap Next Episode — hook, loop, and cliffhanger are working."
        if overall >= 70
        else "Would swipe away — fix the hook and cliffhanger first."
    )
    return {
        "hook_score": hook_score,
        "retention_score": retention_score,
        "cliffhanger_score": cliffhanger_score,
        "overall": overall,
        "strengths": strengths,
        "fixes": fixes,
        "verdict": verdict,
    }


def parse_json_response(raw: str) -> Dict:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    # Find first { ... last }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    return json.loads(raw)

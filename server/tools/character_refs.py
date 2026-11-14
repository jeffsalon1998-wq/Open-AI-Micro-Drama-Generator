"""Canon character references for identity-locked generation.

Each main character has a public turnaround sheet (front / three-quarter /
profile). Reference-to-video conditions on these instead of re-rolling a first
frame, which is what keeps a face identical across shots and episodes.

Sheets live in Google Drive (free, persistent) and are referenced by their
direct-download URL. Add a new character by appending an entry here and setting
``<NAME>_SHEET_URL`` in the environment.
"""
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from tools.drive_host import build_reference_url


@dataclass(frozen=True)
class Costume:
    """One look for a character, valid over an episode range."""

    key: str
    file_id: str
    costume: str
    first_episode: int = 1
    last_episode: int = 10
    label: str = ""

    def covers(self, episode: int) -> bool:
        return self.first_episode <= episode <= self.last_episode

    @property
    def url(self) -> str:
        return build_reference_url(self.file_id) if self.file_id else ""


@dataclass(frozen=True)
class CharacterRef:
    name: str
    file_id: str
    costume: str
    appearance: str
    signature_line: str = ""
    episodes: str = "1-10"
    # Additional looks; the first entry is the default.
    wardrobe: tuple = ()

    @property
    def url(self) -> str:
        """Direct-download URL (works whether or not the file id is set)."""
        return build_reference_url(self.file_id) if self.file_id else ""

    @property
    def costume_lock(self) -> str:
        return f"{self.name} costume: {self.costume}"

    def look(self, episode: int = 1) -> "CharacterRef":
        """Resolve the sheet + costume that belongs to a given episode."""
        for item in self.wardrobe:
            if item.covers(episode):
                return CharacterRef(
                    name=self.name,
                    file_id=item.file_id or self.file_id,
                    costume=item.costume,
                    appearance=self.appearance,
                    signature_line=self.signature_line,
                    episodes=f"{item.first_episode}-{item.last_episode}",
                )
        return self


LENA = CharacterRef(
    name="LENA CRUZ",
    file_id=os.environ.get("DRIVE_LENA_SHEET_FILE_ID", ""),
    costume=(
        "black long-sleeved maid dress with white scalloped lace trim at the apron and cuffs, "
        "crisp white collar, white apron tied at the waist, black flat shoes, no jewelry"
    ),
    appearance=(
        "26 years old, long black hair in a neat low bun with loose strands at the temples, "
        "sharp dark brown eyes, strong eyebrows, fair olive skin, high cheekbones, slim build, 5'6\""
    ),
    signature_line="You fired the wrong maid.",
    episodes="1-10",
    wardrobe=(
        Costume(
            key="maid",
            file_id=os.environ.get("DRIVE_LENA_SHEET_FILE_ID", ""),
            costume=(
                "black long-sleeved maid dress with white scalloped lace trim at the apron and cuffs, "
                "crisp white collar, white apron tied at the waist, black flat shoes, no jewelry"
            ),
            first_episode=1,
            last_episode=1,
            label="Maid uniform (Ep 1 only — the hook)",
        ),
        Costume(
            key="suit",
            file_id=os.environ.get("DRIVE_LENA_SUIT_SHEET_FILE_ID", ""),
            costume=(
                "tailored black power suit, single-breasted blazer with structured shoulders, "
                "slim black trousers, crisp white silk blouse, black pointed-toe heels, "
                "small gold stud earrings"
            ),
            first_episode=2,
            last_episode=10,
            label="Black power suit (Ep 2-10, owner of the empire)",
        ),
    ),
)

VIVIAN = CharacterRef(
    name="VIVIAN VEGA",
    file_id=os.environ.get("DRIVE_VIVIAN_SHEET_FILE_ID", ""),
    costume=(
        "floor-length black designer gown, diamond necklace, diamond drop earrings, "
        "red lipstick, blonde bob, manicured hands"
    ),
    appearance=(
        "48 years old, blonde bob, sharp blue eyes, sculpted cheekbones, tall and lean, cold expression"
    ),
    signature_line="You're fired. Thief!",
    episodes="1-10",
)

MARCUS = CharacterRef(
    name="MARCUS CHEN",
    file_id=os.environ.get("DRIVE_MARCUS_SHEET_FILE_ID", ""),
    costume="navy three-piece suit, white shirt, no tie, silver watch",
    appearance="32 years old, Black hair in a short fade, warm brown eyes, athletic build, calm bearing",
    signature_line="Ma'am Lena, welcome back.",
    episodes="1-10",
)

CHARACTERS: Dict[str, CharacterRef] = {
    "lena": LENA,
    "vivian": VIVIAN,
    "marcus": MARCUS,
}


def get(name: str, episode: int = 0) -> Optional[CharacterRef]:
    """Look up a character, optionally resolving the costume for an episode."""
    base = CHARACTERS.get(name.strip().lower())
    if base and episode:
        return base.look(episode)
    return base


def wardrobe_map() -> dict:
    return {
        key: [
            {
                "key": c.key,
                "label": c.label,
                "file_id": c.file_id or None,
                "url": c.url or None,
                "episodes": f"{c.first_episode}-{c.last_episode}",
            }
            for c in char.wardrobe
        ]
        for key, char in CHARACTERS.items()
        if char.wardrobe
    }


def with_sheets() -> List[CharacterRef]:
    """Characters that actually have a reference image configured."""
    return [c for c in CHARACTERS.values() if c.file_id]


def identity_block(
    names: Optional[List[str]] = None,
    include_costume: bool = True,
    episode: int = 0,
) -> str:
    """Prompt fragment describing the fixed identity, for reference-to-video."""
    picked = [get(n, episode=episode) for n in (names or [])] if names else with_sheets()
    picked = [c for c in picked if c]
    if not picked:
        return ""
    lines = ["CHARACTER IDENTITY (must match the reference images exactly):"]
    for c in picked:
        lines.append(f"- {c.name}: {c.appearance}.")
        if include_costume and c.costume:
            lines.append(f"  Costume: {c.costume}.")
    lines.append(
        "Do not change facial structure, hair color, skin tone, eye color, or costume between shots."
    )
    return "\n".join(lines)


def all_sheet_urls() -> List[str]:
    return [c.url for c in with_sheets() if c.url]


def describe() -> dict:
    return {
        "characters": [
            {
                "name": c.name,
                "file_id": c.file_id or None,
                "url": c.url or None,
                "costume": c.costume,
                "configured": bool(c.file_id),
                "episodes": c.episodes,
            }
            for c in CHARACTERS.values()
        ]
    }


__all__ = [
    "CharacterRef",
    "Costume",
    "wardrobe_map",
    "CHARACTERS",
    "LENA",
    "MARCUS",
    "VIVIAN",
    "all_sheet_urls",
    "describe",
    "get",
    "identity_block",
    "with_sheets",
]

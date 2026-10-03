"""Read the agent's own onboarding files without following links or writing state."""

from __future__ import annotations

import re
from pathlib import Path

from tinyassets.universe_files import read_universe_text
from tinyassets.universe_self_model import _read_frontmatter_value

_MAX_ONBOARDING_BYTES = 64 * 1024
_NOTE = (
    "\n\n## Onboarding\n"
    "Ask my owner for a name and one responsibility: what I own, where I learn "
    "from, my quality bar, what needs approval, and how often I report. Use my "
    "tools to save the name in identity.md frontmatter (name:) and the "
    "responsibility under ## Responsibility in AGENTS.md. Propose each approval "
    "item as an ask_first rule through the existing rules path; only my owner "
    "can set rules. Propose the report cadence as an automation."
)


def _read(universe_dir: Path, filename: str) -> str:
    try:
        return read_universe_text(universe_dir, filename, max_bytes=_MAX_ONBOARDING_BYTES)
    except (OSError, UnicodeError):
        return ""


def agent_identity(universe_dir: Path) -> tuple[str, str]:
    """The declared name and Responsibility section; unreadable means absent."""
    name = _read_frontmatter_value(_read(universe_dir, "identity.md"), "name")
    instructions = _read(universe_dir, "AGENTS.md")
    section = re.search(r"(?m)^## Responsibility[ \t]*\r?$", instructions)
    responsibility = ""
    if section:
        responsibility = re.split(
            r"(?m)^#{1,2}[ \t]+", instructions[section.end():], maxsplit=1,
        )[0].strip()
    return name, responsibility


def onboarding_note(universe_dir: Path) -> str:
    """One turn's instruction, absent once both owner-provided fields exist."""
    name, responsibility = agent_identity(universe_dir)
    return "" if name and responsibility else _NOTE

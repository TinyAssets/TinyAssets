"""Editable starter content, shared by creation and on-demand skill copying.

No per-turn installation or upgrade policy lives here. The planned
starter-seed-lifecycle manifest can consume the same source file.
"""
from pathlib import Path

CONNECT_SKILL_PATH = "skills/connect/SKILL.md"
SHARE_SKILL_PATH = "skills/share-after-publish/SKILL.md"
CAPABILITIES_SKILL_PATH = "skills/capabilities/SKILL.md"

STARTER_SKILL_NAMES = ("memory", "access", "onboarding", "time", "workspace")


def starter_agent_files() -> dict[str, str]:
    """Published content for D10 to install; never writes into an owner root."""
    package = Path(__file__).parent
    sources = {
        "AGENTS.md": package / "starter" / "AGENTS.md",
        "starter/hooks.md": package / "starter" / "hooks.md",
    }
    for name in STARTER_SKILL_NAMES:
        path = f"skills/starter-{name}/SKILL.md"
        sources[path] = package / path
    return {path: source.read_text(encoding="utf-8") for path, source in sources.items()}


def capabilities_skill() -> str:
    return Path(__file__).with_name("skills").joinpath("capabilities", "SKILL.md").read_text(
        encoding="utf-8",
    )


def connect_skill() -> str:
    return Path(__file__).with_name("skills").joinpath("connect", "SKILL.md").read_text(
        encoding="utf-8",
    )


def share_skill() -> str:
    return Path(__file__).with_name("skills").joinpath("share-after-publish", "SKILL.md").read_text(
        encoding="utf-8",
    )

"""Editable starter content, shared by creation and on-demand skill copying.

No per-turn installation or upgrade policy lives here. The planned
starter-seed-lifecycle manifest can consume the same source file.
"""
from pathlib import Path

CONNECT_SKILL_PATH = "skills/connect/SKILL.md"
SHARE_SKILL_PATH = "skills/share-after-publish/SKILL.md"


def connect_skill() -> str:
    return Path(__file__).with_name("skills").joinpath("connect", "SKILL.md").read_text(
        encoding="utf-8",
    )


def share_skill() -> str:
    return Path(__file__).with_name("skills").joinpath("share-after-publish", "SKILL.md").read_text(
        encoding="utf-8",
    )

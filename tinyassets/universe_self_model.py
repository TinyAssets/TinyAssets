"""Persona-facing view over the root OKF universe soul bundle.

This module keeps the historical ``read_self_model`` function name because the
public persona payload still has a ``self_model`` key for client compatibility.
The active files are no longer stored in ``<universe>/self/``. The single active
identity/intention model is the linked OKF bundle rooted at ``soul.md`` in the
universe directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SeedQuestion:
    """A universal thing every blank universe is curious to learn."""

    slug: str
    question: str
    path: str


SEED_QUESTIONS: tuple[SeedQuestion, ...] = (
    SeedQuestion("identity", "Who am I - my name and what I am", "identity.md"),
    SeedQuestion("founder", "Who is my founder", "founder.md"),
    SeedQuestion("orgchart", "What is my organization under my founder", "orgchart.md"),
    SeedQuestion("body", "What is my body - live surfaces, voice, hands, and senses", "body.md"),
    SeedQuestion("origin", "Existing work to build from, or are we starting new", "origin.md"),
)


def ensure_self_model(universe_dir: Path) -> Path:
    """Compatibility no-op.

    Creation owns the baseline. Status/read paths must not create the old
    ``self/`` directory or lazily seed brain files.
    """
    return Path(universe_dir)


def read_self_model(universe_dir: Path, *, owner: str | None = None) -> dict[str, object]:
    """Return known/open identity questions from the root OKF soul bundle."""
    from tinyassets.universe_owner import owner_of
    from tinyassets.universe_soul import soul_present

    root = Path(universe_dir)
    owner = owner or owner_of(root.parent, root.name)
    if not owner or not soul_present(root, owner=owner):
        return {
            "bundle_exists": False,
            "okf_version": "",
            "name": "",
            "known": [],
            "open_questions": [],
        }

    known: list[dict[str, str]] = []
    open_questions: list[dict[str, str]] = []
    for question in SEED_QUESTIONS:
        if _is_learned(root, question.path):
            known.append(
                {
                    "slug": question.slug,
                    "question": question.question,
                    "path": question.path,
                }
            )
        else:
            open_questions.append({"slug": question.slug, "question": question.question})

    return {
        "bundle_exists": True,
        "okf_version": _read_okf_version(root),
        "name": _read_concept_name(root, "identity.md"),
        "known": known,
        "open_questions": open_questions,
    }


def _is_learned(root: Path, relname: str) -> bool:
    from tinyassets.universe_files import read_universe_text

    try:
        text = read_universe_text(root, relname)
    except (OSError, UnicodeDecodeError):
        return False
    status = _read_frontmatter_value(text, "status").lower()
    if status in {"not-learned", "not learned", "unknown", "unlearned"}:
        return False
    return True


def _read_concept_name(root: Path, relname: str) -> str:
    from tinyassets.universe_files import read_universe_text

    try:
        text = read_universe_text(root, relname)
    except (OSError, UnicodeDecodeError):
        return ""
    return _read_frontmatter_value(text, "name")


def _read_okf_version(root: Path) -> str:
    from tinyassets.universe_files import read_universe_text

    for relname in ("index.md", "soul.md"):
        try:
            text = read_universe_text(root, relname)
        except (OSError, UnicodeDecodeError):
            continue
        value = _read_frontmatter_value(text, "okf_version")
        if value:
            return value
    return ""


def _read_frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---\n"):
        return ""
    try:
        frontmatter, _body = text[4:].split("\n---\n", 1)
    except ValueError:
        return ""
    for line in frontmatter.splitlines():
        current_key, sep, value = line.partition(":")
        if sep and current_key.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""

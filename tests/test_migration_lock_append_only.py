"""The owner schema's migration lock only grows (slice P review M3).

`platform/owner/migrations.json` pins every migration a deploy shipped
(`deployed`, each one's digest) and when the first deploy was
(`first_deploy`). The platform's own guard holds the code to the lock in the
same tree, so a change that edits a deployed migration *and* its pin passes
it. This guard holds the lock itself against the base branch: once the base
marks a first deploy, that marker never changes, and the base's pins stay the
head's first pins, in order. A new migration is appended; nothing is edited
or taken away. The owner object's start check (`migrate`) is the backstop in
production; this is the one that stops it in review.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LOCK = "platform/owner/migrations.json"
BASE = os.environ.get("MIGRATION_LOCK_BASE", "origin/main")


def problems(base: dict, head: dict) -> list[str]:
    """Each way `head` is not `base` with pins appended."""
    found: list[str] = []
    if base.get("first_deploy") is not None and head.get("first_deploy") != base["first_deploy"]:
        found.append("the first-deploy marker changed")
    pinned, now = base.get("deployed", []), head.get("deployed", [])
    if now[: len(pinned)] != pinned:
        found.append("a deployed migration's pin was edited or taken away: add a migration")
    return found


def _base_lock() -> dict | None:
    """The lock on the base branch; None when the base has none yet."""
    known = subprocess.run(["git", "rev-parse", "--verify", "--quiet", BASE], cwd=REPO,
                           capture_output=True, text=True, check=False)
    assert known.returncode == 0, (
        f"{BASE} is not in this checkout: fetch it (the guard never passes without its base)")
    shown = subprocess.run(["git", "show", f"{BASE}:{LOCK}"], cwd=REPO, capture_output=True,
                           text=True, check=False)
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def test_the_lock_only_grows_against_the_base_branch() -> None:
    base = _base_lock()
    head = json.loads((REPO / LOCK).read_text(encoding="utf-8"))
    assert problems(base or {"first_deploy": None, "deployed": []}, head) == []


def test_an_edited_pin_is_refused_with_the_migration_it_pins() -> None:
    first = {"at": "2026-11-04T00:00:00.000Z", "commit": "abc123"}
    base = {"first_deploy": first, "deployed": ["a" * 64, "b" * 64]}
    assert problems(base, {"first_deploy": first, "deployed": ["a" * 64, "b" * 64, "c" * 64]}) == []
    # The review's attack: a deployed migration and its pin edited together.
    assert problems(base, {"first_deploy": first, "deployed": ["e" * 64, "b" * 64]}) == [
        "a deployed migration's pin was edited or taken away: add a migration"]
    assert problems(base, {"first_deploy": first, "deployed": ["a" * 64]}) == [
        "a deployed migration's pin was edited or taken away: add a migration"]
    assert problems(base, {"first_deploy": None, "deployed": []}) == [
        "the first-deploy marker changed",
        "a deployed migration's pin was edited or taken away: add a migration"]
    # Before the first deploy, migrations may still change in place.
    assert problems({"first_deploy": None, "deployed": []},
                    {"first_deploy": first, "deployed": ["e" * 64]}) == []


pytestmark = pytest.mark.verifies('clean-sheet-platform:A deployed migration is never edited')

"""Move platform state out of command-center-writable folders, once.

The consent database went first and alone, because its placement produced a
forgeable *authority answer* rather than a redirectable read: it lived inside
the command center, its schema adopted a file that already existed, and the
external-call gate read the result. See
``openspec/changes/platform-state-outside-command-centers`` and
``docs/concerns/2026-10-03-a-universe-database-name-steers-the-daemon.md``.

Three rules, all of them about not trusting what is already there:

* **Nothing is carried forward.** There is no provenance record for rows written
  before the move, so a carried row is indistinguishable from the forgery this
  exists to prevent. The sidecar database is created EMPTY and the old file is
  renamed aside -- not deleted, so an operator can still read it. Every consent
  is granted again, through the ordinary ask at first use.
* **Unexplained copies are a refusal.** A durable per-home journal outside the
  home identifies our own interrupted create/rename, without trusting legacy rows.
* **It runs under the exclusive data-layout lock before any role opens the
  data**, and marks itself in progress durably first, so a crash leaves a
  resumable marker rather than a half-moved volume.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: Marker key: which one-way moves this data has had.
MOVES = "moves"
#: Marker key for this move, and its two states.
CONSENTS = "consents_outside_command_centers"
MIGRATING = "migrating"
DONE = "done"
#: What the old in-folder database is renamed to. Kept, not deleted.
SUPERSEDED_SUFFIX = ".premigration"


class MoveRefused(RuntimeError):
    """A command center could not be moved, and guessing was not acceptable."""


def command_centers(base: Path) -> list[Path]:
    """The command-center folders under the data root, in a stable order.

    A dot-prefixed entry is platform state at the root, not a command center --
    the same predicate every other rule here uses.
    """
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for name in names:
        if name.startswith("."):
            continue
        child = base / name
        if child.is_dir() and not child.is_symlink():
            out.append(child)
    return out


def move_needed(document: dict | None) -> bool:
    """Has this data had the consents move?"""
    if not isinstance(document, dict):
        return True
    return (document.get(MOVES) or {}).get(CONSENTS) != DONE


def _move_one(command_center: Path) -> str:
    """Move one command center's consent database. Returns what was done."""
    from tinyassets.storage.effector_consents import (
        consents_db_path,
        initialize_consents_db,
        legacy_consents_db_path,
    )
    from tinyassets.storage_layout import _write_atomically
    from tinyassets.universe_files import read_data_path

    legacy = legacy_consents_db_path(command_center)
    target = consents_db_path(command_center)
    legacy_there = legacy.exists() or legacy.is_symlink()
    target_there = target.exists() or target.is_symlink()
    progress = target.with_name(".consents-move.json")
    raw = read_data_path(progress)
    journal = json.loads(raw) if raw is not None else None
    resuming = journal == {"version": 1, "state": MIGRATING}
    if journal is not None and not resuming and journal != {"version": 1, "state": DONE}:
        raise MoveRefused(f"{command_center.name}: unrecognized consent move journal")

    if target_there and legacy_there and not resuming:
        raise MoveRefused(
            f"{command_center.name}: a consent database exists both inside the "
            f"command center ({legacy}) and in its sidecar folder ({target}). "
            "There is no pending move journal; neither copy is "
            "safe to merge. Move or remove one by hand and start again.")
    if target_there and not resuming:
        return "already-moved"
    if not legacy_there and not resuming:
        return "unused"

    # Keep the old file, renamed, so it can still be inspected. A LINK at the
    # old name is renamed too: it is evidence, and following it to delete or
    # read through it is what this whole change refuses to do.
    superseded = legacy.with_name(legacy.name + SUPERSEDED_SUFFIX)
    if legacy_there and (superseded.exists() or superseded.is_symlink()):
        raise MoveRefused(
            f"{command_center.name}: {superseded.name} already exists, so the "
            "old database cannot be set aside without overwriting it. Move it "
            "out of the way by hand and start again.")
    # Persist intent BEFORE creating the empty target. Only this platform-owned
    # journal permits both names on restart. Initialization is idempotent even
    # if the process died part-way through SQLite's schema transaction.
    if not resuming:
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(progress, {"version": 1, "state": MIGRATING})
    initialize_consents_db(command_center)
    if legacy_there:
        os.replace(legacy, superseded)
    if hasattr(os, "O_DIRECTORY"):
        fd = os.open(command_center, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    _write_atomically(progress, {"version": 1, "state": DONE})
    return "superseded"


def run(base: Path, *, mark) -> dict[str, int]:
    """Run the move for every command center under ``base``.

    ``mark(state)`` records progress in the layout marker; it is called with
    ``MIGRATING`` before the first change and ``DONE`` after the last, so a
    crash in between leaves a marker the next start resumes from.

    Refusals are collected and raised together: one unsafe command center must
    not hide the state of the rest, and an operator wants the whole list.
    """
    mark(MIGRATING)
    counts = {"already-moved": 0, "unused": 0, "superseded": 0}
    refused: list[str] = []
    for command_center in command_centers(base):
        try:
            counts[_move_one(command_center)] += 1
        except MoveRefused as exc:
            refused.append(str(exc))
    if refused:
        raise MoveRefused(
            f"{len(refused)} command center(s) could not be moved; the data is "
            "fenced and the move will resume when they are resolved:\n"
            + "\n".join(f"  - {line}" for line in refused))
    mark(DONE)
    return counts

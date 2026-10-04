"""The universe's learn/write path: apply a governed soul edit.

Implements the `soul.edit` contract from the universe-creation spec: the
execution path READS AND FOLLOWS the universe's own ``soul.edit.md`` policy
(the authority lives in the file, not in a hardcoded list). An edit is a
learning event — proposed learning with source and context, never a blind
overwrite — that updates only the explicitly changed governed files, appends
``log.md``, and writes a new ``soul_versions/`` snapshot.

This is what lets a universe REMEMBER what its founder teaches it: learned
files flip ``status: not-learned`` → ``learned``, and the persona
(``tinyassets.persona.resolve_persona`` over
``tinyassets.universe_self_model.read_self_model``) voices them from then on.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import stat
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tinyassets.universe_files import (
    MAX_BRAIN_FILE_BYTES,
    MAX_FRONTMATTER_BYTES,
    load_untrusted_yaml,
    open_lock_file,
    read_universe_text,
)
from tinyassets.universe_soul import SOUL_FILENAME, SOUL_VERSIONS_DIR

_logger = logging.getLogger(__name__)

SOUL_EDIT_POLICY_FILENAME = "soul.edit.md"

# Sidecar lock serializing soul edits for ONE universe. A soul edit is a
# read→modify→write→snapshot-allocate sequence; the snapshot number is derived
# from a directory listing, so concurrent edits without a lock can collide on
# the number and lose an update (Codex ADAPT 2026-07-02). One writer at a time
# per universe closes that window.
SOUL_LOCK_FILENAME = ".soul.lock"

# Files whose frontmatter records the learning event. soul.md is the
# operational entrypoint — its frontmatter (okf_source, edit_authority, …) is
# preserved verbatim and carries no learned-status flag.
_LEARNED_STATUS_EXEMPT = frozenset({SOUL_FILENAME})


class SoulEditError(ValueError):
    """A soul edit that violates the universe's soul.edit.md policy."""


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split an OKF concept doc into (frontmatter dict, body).

    The frontmatter is untrusted (the universe agent writes these files), so it
    is bounded and parsed with anchors/aliases refused before any expansion
    (:func:`tinyassets.universe_files.load_untrusted_yaml`).
    """
    if not text.startswith("---"):
        raise SoulEditError("governed file is missing OKF frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SoulEditError("governed file has malformed OKF frontmatter")
    try:
        meta = load_untrusted_yaml(parts[1], max_bytes=MAX_FRONTMATTER_BYTES)
    except OSError as exc:
        raise SoulEditError(f"governed file frontmatter refused: {exc}") from exc
    if not isinstance(meta, dict):
        raise SoulEditError("governed file frontmatter is not a mapping")
    return meta, parts[2].lstrip("\n")


def _render(meta: dict[str, Any], body: str) -> str:
    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    if not body.endswith("\n"):
        body += "\n"
    return f"---\n{fm}\n---\n\n{body}"


def assert_contained(root: Path, path: Path) -> None:
    """Refuse a path that escapes ``root`` through a symlinked component.

    ``os.path.realpath`` resolves EVERY symlink in the path (the file itself AND
    any parent directory, e.g. a ``soul_versions`` symlinked to an external dir),
    so requiring both the path and its parent to resolve inside ``root`` closes
    symlink write-escape and read-through-disclosure across the soul-edit sinks
    (Codex brain-loop re-review 2026-08-22). Hardlinks (which share no distinct
    path) are handled separately by the per-file link-count guard + atomic writes.
    """
    root_r = os.path.realpath(root)
    prefix = root_r + os.sep
    for candidate in (os.path.realpath(path), os.path.realpath(Path(path).parent)):
        if candidate != root_r and not candidate.startswith(prefix):
            raise SoulEditError(
                f"path escapes the command center via a symlinked component: {path}"
            )


def _atomic_write_text(
    path: Path, text: str, *, encoding: str = "utf-8", mode: str = "replace",
) -> None:
    """Inode-safe write: write a FRESH temp file in the same dir + os.replace.

    ``os.replace`` repoints the NAME at a new inode, so a SYMLINK or HARDLINK at
    ``path`` can never redirect the write onto another inode (e.g. soul.md's
    control-plane frontmatter or an external file). This closes the inode-alias
    bypass across EVERY soul-edit sink — the governed files, log.md, the snapshot,
    and the version index — and also the check→use TOCTOU window, since the write
    itself is safe regardless of what the path pointed at a moment earlier (Codex
    brain-loop re-review 2026-08-22).

    Through :func:`tinyassets.universe_files.write_data_path`: the temp file is
    created ``O_EXCL|O_NOFOLLOW`` inside a directory opened with no link at any
    component, so a ``soul_versions`` swapped for a link to another universe
    between allocation and write refuses instead of writing there.
    """
    from tinyassets.universe_files import write_data_path

    if encoding.lower().replace("-", "") != "utf8":
        raise ValueError(f"soul files are UTF-8, not {encoding!r}")
    write_data_path(Path(path), text, mode=mode)


def read_governed_files(universe_dir: Path) -> tuple[str, ...]:
    """Governed files = a platform-enforced floor PLUS the universe's own extensions.

    The universe's ``soul.edit.md`` lists files it governs; a PRESENT policy is still
    required (no policy file → no soul edits, fail-closed). The returned set is the
    UNION of that list with the platform baseline (``SOUL_EDIT_GOVERNED``): the core
    grounding files are ALWAYS governable (Codex review 2026-08-23). This is
    deliberate — per-universe OMISSION of a baseline file is NOT a supported deny
    mechanism (soul.edit.md is platform-seeded from the baseline, never a
    restrictive user override), and it lets an existing universe gain a newly
    baselined grounding file (e.g. orgchart.md) without a data migration while a core
    soul file can never be silently un-governed. A universe MAY still ADD files above
    the floor by listing them. ``soul.edit.md`` itself is deliberately NOT in the
    baseline or the brain-writable mapping, so an agent cannot edit its own policy.
    """
    try:
        policy = read_universe_text(
            universe_dir, SOUL_EDIT_POLICY_FILENAME, max_bytes=MAX_BRAIN_FILE_BYTES,
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise SoulEditError(
            f"soul edit policy missing: {SOUL_EDIT_POLICY_FILENAME} is required "
            "(the execution path reads and follows it)"
        ) from exc

    section = re.search(
        r"^##\s+Governed files\s*$(.*?)(?=^##\s|\Z)",
        policy,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not section:
        raise SoulEditError("soul.edit.md has no 'Governed files' section")
    governed = tuple(re.findall(r"^\s*-\s*`([^`]+)`", section.group(1), flags=re.MULTILINE))
    if not governed:
        raise SoulEditError("soul.edit.md governs no files")
    # Baseline migration (2026-08-23): the platform-seeded baseline of governed
    # grounding files is ALWAYS governable, even for universes whose soul.edit.md was
    # seeded before a file joined the baseline (e.g. orgchart.md). Union the parsed
    # policy list with the current baseline so an existing universe does not need a
    # data migration to gain a new baseline grounding file, while any files the
    # universe itself added to its policy are preserved. (This is what lets the live
    # universe finally record its org chart instead of re-asking every turn.)
    from tinyassets.universe_bundle import SOUL_EDIT_GOVERNED

    merged = list(governed)
    for baseline in SOUL_EDIT_GOVERNED:
        if baseline not in merged:
            merged.append(baseline)
    return tuple(merged)


@contextlib.contextmanager
def _soul_lock(universe_dir: Path) -> Iterator[None]:
    """Cross-platform exclusive lock serializing soul edits for one universe.

    Mirrors the sidecar-lock pattern in ``branch_tasks._file_lock`` (msvcrt on
    Windows, fcntl on POSIX). Held across the whole read→write→snapshot section
    of :func:`apply_soul_edit` so the snapshot-number allocation cannot race.

    Opened through :func:`~tinyassets.universe_files.open_lock_file`: the lock
    name sits in a directory the universe's own processes can write, and a plain
    ``O_RDWR|O_CREAT`` on it follows a planted link and CREATES the link's
    target outside this universe.
    """
    universe_dir = Path(universe_dir)
    universe_dir.mkdir(parents=True, exist_ok=True)
    fd = open_lock_file(universe_dir, SOUL_LOCK_FILENAME)
    try:
        if sys.platform == "win32":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt

                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        os.close(fd)


def current_soul_versions(
    universe_dir: Path, filenames: Iterable[str]
) -> dict[str, str]:
    """Snapshot the current content hash of each governed file.

    A caller reads these BEFORE composing a learning edit, then passes them as
    ``expected_versions`` to :func:`apply_soul_edit`; the write is rejected if
    the file changed in between (optimistic concurrency / compare-and-swap).
    Missing files are omitted.
    """
    universe_dir = Path(universe_dir)
    out: dict[str, str] = {}
    for filename in filenames:
        try:
            raw = read_universe_text(universe_dir, filename, max_bytes=MAX_BRAIN_FILE_BYTES)
        except (OSError, UnicodeDecodeError):
            continue
        out[filename] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return out


def apply_soul_edit(
    universe_dir: Path,
    *,
    changes: dict[str, str],
    source: str,
    context: str,
    summary: str = "",
    name: str = "",
    expected_versions: dict[str, str] | None = None,
    agent_id: str,
) -> dict[str, Any]:
    """Apply one governed learning event to the universe's soul bundle.

    ``agent_id`` is the agent whose learning this is (harness §4.18), and is
    REQUIRED: every writer states it, so no path defaults to the main agent.
    The brain is shared by every agent, but the name and ``identity.md`` are
    the MAIN agent's identity, so any other agent's edit that touches them is
    refused here, at the one door every soul write goes through.

    ``changes`` maps governed filename → new markdown BODY (frontmatter is
    managed here: preserved, with ``status: learned`` + ``learned_from``
    recorded). ``name`` optionally records the universe's learned self-name in
    ``identity.md`` frontmatter — a name-only learning event needs no body.
    ``source`` and ``context`` are required: an edit is proposed learning, not
    a blind overwrite. ``expected_versions`` (filename → sha256 of the content
    the caller last read, from :func:`current_soul_versions`) makes the write a
    compare-and-swap: if a governed file changed since it was read the edit is
    rejected rather than clobbering the newer state. The whole read→write→
    snapshot section runs under a per-universe lock.
    """
    universe_dir = Path(universe_dir)
    source = (source or "").strip()
    context = (context or "").strip()
    name = (name or "").strip()
    if not source or not context:
        raise SoulEditError(
            "source and context are required — a soul edit is a learning "
            "event, not a blind overwrite"
        )

    if not isinstance(agent_id, str) or not agent_id.strip():
        raise SoulEditError("agent_id is required: whose learning is this edit?")
    if agent_id.strip() != "main" and (name or "identity.md" in (changes or {})):
        raise SoulEditError(
            "only the main agent writes its name and identity.md; another agent's "
            "learning goes into the shared brain's other files"
        )
    governed = read_governed_files(universe_dir)
    changes = dict(changes or {})
    if name and "identity.md" not in changes:
        changes["identity.md"] = ""  # name-only: keep the existing body
    if not changes:
        raise SoulEditError("nothing to learn: provide changes and/or a name")

    for filename in changes:
        if filename != Path(filename).name or filename.startswith("."):
            raise SoulEditError(f"invalid governed filename: {filename!r}")
        if filename not in governed:
            raise SoulEditError(
                f"'{filename}' is not governed by {SOUL_EDIT_POLICY_FILENAME} "
                f"(governed: {', '.join(governed)})"
            )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    expected = dict(expected_versions or {})
    updated: list[str] = []
    new_contents: dict[str, str] = {}
    # The read→write→snapshot section runs under a per-universe lock: the
    # snapshot number is allocated from a directory listing, so a concurrent
    # edit could otherwise pick the same number and lose an update. The
    # compare-and-swap check is evaluated against the same locked read.
    with _soul_lock(universe_dir):
        # Pass 1 — read current state + compare-and-swap check. All checks run
        # before any write, so a mismatch leaves the bundle untouched.
        parsed: dict[str, tuple[dict[str, Any], str]] = {}
        udir_resolved = universe_dir.resolve()
        for filename in changes:
            path = universe_dir / filename
            # Inode-safety (Codex brain-loop review 2026-08-22): validate the
            # resolved FILE OBJECT, not just the filename string. A governed file
            # that is a SYMLINK, or a HARDLINK aliasing another inode (e.g.
            # identity.md hardlinked to soul.md or to an external file), would let
            # a whitelisted write mutate a NON-governed target — overwriting the
            # soul's executable frontmatter (loop_branch_def_id / effect_authority)
            # or a file outside the universe. That is a control-plane bypass, so
            # refuse rather than write THROUGH the aliased path. Requires a planted
            # link (crafted/restored/compromised universe), but the boundary must
            # survive that.
            try:
                st = os.lstat(path)
            except OSError as exc:
                raise SoulEditError(
                    f"governed file missing on disk: {filename}"
                ) from exc
            if stat.S_ISLNK(st.st_mode):
                raise SoulEditError(
                    f"governed file is a symlink, refusing to write: {filename}"
                )
            if st.st_nlink > 1:
                raise SoulEditError(
                    f"governed file is hardlinked (nlink={st.st_nlink}); refusing "
                    f"to write through an aliased inode: {filename}"
                )
            resolved = path.resolve()
            if resolved != udir_resolved / filename:
                raise SoulEditError(
                    f"governed file resolves outside its command center slot, refusing: "
                    f"{filename}"
                )
            try:
                raw = read_universe_text(universe_dir, filename, max_bytes=MAX_BRAIN_FILE_BYTES)
            except (OSError, UnicodeDecodeError) as exc:
                raise SoulEditError(
                    f"governed file unreadable or over its bound: {filename}"
                ) from exc
            want = expected.get(filename)
            if want is not None:
                actual = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                if actual != want:
                    raise SoulEditError(
                        f"stale soul edit: {filename} changed since it was read "
                        "(expected-version mismatch) — re-read and retry"
                    )
            parsed[filename] = _split_frontmatter(raw)
        # Pass 2 — apply.
        for filename, new_body in changes.items():
            meta, old_body = parsed[filename]
            body = new_body if (new_body or "").strip() else old_body
            if filename not in _LEARNED_STATUS_EXEMPT:
                meta["status"] = "learned"
                meta["learned_from"] = source
                meta["learned_at"] = now
            if name and filename == "identity.md":
                meta["name"] = name
            rendered = _render(meta, body)
            new_contents[filename] = rendered
            _atomic_write_text(universe_dir / filename, rendered)
            updated.append(filename)

        log_entry = summary.strip() or f"learned {', '.join(sorted(updated))}"
        _append_log(universe_dir, f"- learned: {log_entry} (source: {source})")
        snapshot_rel = _write_edit_snapshot(
            universe_dir,
            files=new_contents,
            source=source,
            context=context,
            summary=log_entry,
            stamp=now,
        )

    return {
        "updated_files": sorted(updated),
        "snapshot": snapshot_rel,
        "log_entry": log_entry,
        "source": source,
    }


def _append_log(universe_dir: Path, line: str) -> None:
    log_path = universe_dir / "log.md"
    assert_contained(universe_dir, log_path)
    try:
        text = read_universe_text(universe_dir, "log.md", max_bytes=MAX_BRAIN_FILE_BYTES)
    except FileNotFoundError:
        text = "# Update Log\n"
    except (OSError, UnicodeDecodeError) as exc:
        # Over its bound or not a plain file: never rewrite it (that would drop
        # the owner's log); skip this line and say so.
        _logger.warning("soul edit log not appended: log.md refused (%s)", exc)
        return
    if not text.endswith("\n"):
        text += "\n"
    _atomic_write_text(log_path, text + line + "\n")


def _write_edit_snapshot(
    universe_dir: Path,
    *,
    files: dict[str, str],
    source: str,
    context: str,
    summary: str,
    stamp: str,
) -> str:
    """Write a self-describing snapshot of this edit and index it.

    Every accepted edit writes a NEW snapshot (policy), so the record embeds
    the edit metadata — two identical-content edits still produce distinct
    snapshots because each records its own event.
    """
    versions_dir = universe_dir / SOUL_VERSIONS_DIR
    # Refuse a soul_versions symlinked to an external dir BEFORE any fs op
    # (parent-directory symlink write-escape — Codex re-review). realpath of a
    # not-yet-created dir stays inside the universe; a symlink resolves out and is
    # refused.
    assert_contained(universe_dir, versions_dir)
    versions_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(versions_dir.glob("[0-9][0-9][0-9][0-9].md"))
    next_number = 1
    if existing:
        try:
            next_number = int(existing[-1].stem) + 1
        except ValueError:
            next_number = len(existing) + 1

    meta = {
        "type": "Soul Edit Snapshot",
        "title": f"Soul Edit {next_number:04d}",
        "description": summary,
        "source": source,
        "learned_at": stamp,
        "files": ", ".join(sorted(files)),
    }
    body_parts = [f"# Soul Edit {next_number:04d}", "", context, ""]
    for filename in sorted(files):
        body_parts += [f"## {filename}", "", "```markdown", files[filename].rstrip(), "```", ""]
    snapshot_name = f"{next_number:04d}.md"
    # Exclusive: a numbered snapshot is never overwritten, and an entry that
    # appeared at this number since allocation refuses rather than being replaced.
    _atomic_write_text(
        versions_dir / snapshot_name, _render(meta, "\n".join(body_parts)), mode="exclusive",
    )

    index_path = versions_dir / "index.md"
    try:
        index_text = read_universe_text(
            universe_dir, f"{SOUL_VERSIONS_DIR}/index.md", max_bytes=MAX_BRAIN_FILE_BYTES,
        )
    except FileNotFoundError:
        index_text = "# Soul Version Index\n"
    except (OSError, UnicodeDecodeError) as exc:
        _logger.warning("soul version index not appended: index.md refused (%s)", exc)
        return f"{SOUL_VERSIONS_DIR}/{snapshot_name}"
    if not index_text.endswith("\n"):
        index_text += "\n"
    _atomic_write_text(
        index_path,
        index_text
        + f"- [{next_number:04d}]({snapshot_name}) — learned: {summary}\n",
    )
    return f"{SOUL_VERSIONS_DIR}/{snapshot_name}"

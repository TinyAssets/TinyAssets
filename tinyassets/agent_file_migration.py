"""C8 cutover: every addressable agent binding leaves SQL for ``agents/<name>/``.

One shot, all or nothing, run as a deploy step (``deploy/deploy_fail_safe.sh``
section 3c) from the candidate image with the daemon stopped, after the
full-volume snapshot that is the rollback point::

    python -m tinyassets.agent_file_migration check /data
    python -m tinyassets.agent_file_migration apply /data --backup /backup/<snapshot>

``check`` exits 10 while the volume holds bindings to move and 0 once it holds
none, so a deploy of a migrated volume runs nothing. ``apply`` verifies the
backup, takes the data-layout lock exclusively (a running daemon holds it
shared for its lifetime: stop it first), writes ``"migrating"`` to the marker,
and in ONE catalogue transaction reads every addressable binding, writes each
agent's files into its owner's command center, deletes the rows, hands the
SQLite sidecars back to the daemon and prepares the ``"stable"`` marker beside
the live one; then the commit and the marker publication. A pre-commit error
rolls back the transaction and removes the files this run wrote. A post-commit
marker or cleanup error is logged without changing the successful exit: SQL
already records the cutover. After any failed apply, deploy checks SQL again
before deciding whether the previous image can restart. Nothing is retried or
recorded; a later run on the migrated volume finds no rows.

What moves: main's ``addressed_agents.is_conversable`` rule (2026-10-12) minus
its per-caller parts -- a configured binding that is neither the
conversation-design installation nor a turn consumer, of any owner, in any
command center, retired or not (a retired one keeps its id for its thread).
Each becomes ``agents/<slug>/`` in its command center: ``AGENTS.md`` holds the
instructions it spoke with and ``settings.yaml`` names it (``agent.id`` the
binding id, ``agent.name``, ``agent.owner`` the account that created it,
``retired: true`` for a retired row, other configuration verbatim). Slugs are
unique among the center's existing ``agents/`` names and each other. Serving
bindings, the installation, turn consumers and catalogue definitions stay.

On an owner-labelled command center (the owner split) the files are created
by this root process through ``role_content.publish_as_root``, labelled exactly
as the owner-content cell leaves new content, so the owner's agent reads and
edits them as its own.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from tinyassets import storage_layout as layout
from tinyassets.agent_definition import (
    AGENTS_DIR,
    INSTRUCTIONS_FILE,
    SETTINGS_FILE,
    parse_settings,
)
from tinyassets.storage import db_path
from tinyassets.storage.platform_state_move import MIGRATING
from tinyassets.universe_files import (
    list_universe_dir,
    unlink_universe_file,
    write_universe_file,
)

MIGRATION = "agent-files"
NOT_AN_AGENT_ROLES = frozenset({"app_experience"})
#: ``check``'s exit status while the volume still holds bindings to move.
PENDING = 10
#: A rollback point older than this is not the one this run restores to.
MAX_BACKUP_AGE_S = 3600


@dataclass
class _Agent:
    universe_id: str
    binding_id: str
    directory: str
    files: list[tuple[str, bytes]]


def _instructions(definition: dict | None) -> str:
    sections = []
    for key, component in sorted(((definition or {}).get("components") or {}).items()):
        config = component.get("config") if isinstance(component, dict) else None
        text = config.get("instructions") if isinstance(config, dict) else None
        if isinstance(text, str) and text.strip():
            sections.append((key, component.get("kind") or "", text.strip()))
    if len(sections) == 1:
        return sections[0][2] + "\n"
    return "\n\n".join(f"## {key} ({kind})\n\n{text}" if kind else f"## {key}\n\n{text}"
                       for key, kind, text in sections) + ("\n" if sections else "")


def _is_agent(binding: dict) -> bool:
    """Main's ``is_conversable`` without its owner, universe and retirement filters."""
    configuration = binding.get("configuration")
    return bool(
        binding.get("status") == "configured"
        and isinstance(configuration, dict)
        and configuration.get("role") not in NOT_AN_AGENT_ROLES
        and "turn_consumer" not in configuration
    )


def _plan(conn: sqlite3.Connection, base: Path) -> tuple[list[_Agent], set[str]]:
    """Every agent to write, with its unique directory, and the command centers
    whose ``agents/`` directory does not exist yet."""
    from tinyassets.command_center_packages import slug as slugify
    from tinyassets.commons_catalog import (
        _binding_from_row,
        _definition_from_row,
        _read_definition_row,
    )

    taken: dict[str, set[str]] = {}
    without_agents_dir: set[str] = set()
    agents: list[_Agent] = []
    rows = conn.execute(
        "SELECT * FROM agent_bindings ORDER BY universe_id, created_at, agent_binding_id",
    ).fetchall()
    for row in rows:
        binding = _binding_from_row(row)
        if not _is_agent(binding):
            continue
        uid = binding["universe_id"]
        if uid not in taken:
            if not (base / uid).is_dir():
                raise FileNotFoundError(f"command center directory {uid!r} is missing")
            try:
                taken[uid] = set(list_universe_dir(base / uid, AGENTS_DIR))
            except FileNotFoundError:
                taken[uid] = set()
                without_agents_dir.add(uid)
        definition_row = _read_definition_row(conn, binding["agent_definition_id"])
        definition = (_definition_from_row(conn, definition_row)
                      if definition_row is not None else None)
        configuration = dict(binding["configuration"])
        name = str(configuration.pop("name", "") or (definition or {}).get("name")
                   or "").strip() or "Unnamed agent"
        configuration.pop("schema_version", None)
        stem = slugify(name)
        slug, n = stem, 1
        while slug in taken[uid]:
            n += 1
            slug = f"{stem}-{n}"
        taken[uid].add(slug)
        identity = {"id": binding["agent_binding_id"], "name": name,
                    "owner": binding["created_by"]}
        if binding["retired"]:
            identity["retired"] = True
        if configuration:
            identity["configuration"] = configuration
        settings = yaml.safe_dump({"schema_version": 1, "agent": identity}, sort_keys=False,
                                  allow_unicode=True).encode("utf-8")
        parse_settings(settings)  # the file must read back as settings
        directory = f"{AGENTS_DIR}/{slug}"
        files = [(f"{directory}/{SETTINGS_FILE}", settings)]
        instructions = _instructions(definition)
        if instructions:
            files.append((f"{directory}/{INSTRUCTIONS_FILE}", instructions.encode("utf-8")))
        agents.append(_Agent(uid, binding["agent_binding_id"], directory, files))
    return agents, without_agents_dir


# ---------------------------------------------------------------------------
# Files: plain directories get the one writer; owner-labelled centers get the
# label the owner-content cell would have given new content.
# ---------------------------------------------------------------------------


def _machine(universe_dir: Path) -> int | None:
    """The owner uid of an owner-labelled command center; None for a plain
    directory (a test root, Windows, a local install)."""
    if os.name != "posix":
        return None
    from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST
    from tinyassets.workspace_fs import open_dir_nofollow

    fd = open_dir_nofollow(universe_dir)
    try:
        gid = os.fstat(fd).st_gid
    finally:
        os.close(fd)
    if OWNER_ID_FIRST <= gid <= OWNER_ID_LAST:
        # Owner-split content roots were moved into owner boxes by the one-shot
        # cutover; agent files migrate before that window, never after it.
        raise PermissionError(f"{universe_dir.name}: owner-labelled content root; "
                              "agent files migrate before the owner-box cutover")
    return None


class _Writer:
    """Writes files and remembers them, so a failure can take back every one."""

    def __init__(self, base: Path, without_agents_dir: set[str]) -> None:
        self.base = base
        self.files: list[tuple[Path, str]] = []
        self.directories: list[Path] = [base / uid / AGENTS_DIR for uid in without_agents_dir]
        self._machines: dict[str, int | None] = {}

    def write(self, agent: _Agent) -> None:
        universe_dir = self.base / agent.universe_id
        if agent.universe_id not in self._machines:
            self._machines[agent.universe_id] = _machine(universe_dir)
        machine = self._machines[agent.universe_id]
        self.directories.append(universe_dir / agent.directory)
        for relpath, data in agent.files:
            self.files.append((universe_dir, relpath))
            assert machine is None
            write_universe_file(universe_dir, relpath, data, mode="exclusive")

    def undo(self) -> None:
        """Best effort, on a path that is already failing: the snapshot is the backstop."""
        for universe_dir, relpath in reversed(self.files):
            try:
                unlink_universe_file(universe_dir, relpath)
            except OSError:
                pass
        for path in reversed(self.directories):
            try:
                path.rmdir()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _verify_backup(path: Path) -> None:
    """The rollback point: a fresh, complete full-volume snapshot
    (``deploy/backup.sh``, ``BACKUP_MODE=snapshot``) with its digest beside it."""
    try:
        info = path.stat()
    except FileNotFoundError:
        raise FileNotFoundError(f"backup {path} does not exist; take the full backup "
                                "first") from None
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise ValueError(f"backup {path} is not a non-empty file")
    if time.time() - info.st_mtime > MAX_BACKUP_AGE_S:
        raise ValueError(f"backup {path} is older than {MAX_BACKUP_AGE_S}s; take a fresh one")
    digest = path.with_name(path.name + ".sha256")
    if not digest.is_file() or digest.stat().st_size == 0:
        raise ValueError(f"{digest} is missing: not a snapshot deploy/backup.sh completed")


def _catalogue_exists(base: Path) -> bool:
    """Whether the binding table was ever created here. A fresh installation
    has nothing to migrate and must not grow a catalogue from this process."""
    path = db_path(base)
    if not path.is_file():
        return False
    with sqlite3.connect(path, timeout=30.0) as conn:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_bindings'",
        ).fetchone() is not None


def _prepare_marker(base: Path, document: dict, state: str) -> Path:
    """Write the marker's next state to a file beside the marker, with the
    marker's owner and mode (this process is root on the owner-split volume;
    the daemon reads the marker as itself), so publishing it is one rename."""
    path = layout.marker_path(base)
    try:
        before = path.stat()
    except FileNotFoundError:
        before = None
    marked = {**document, "state": state}
    if state == MIGRATING:
        marked["migration"] = MIGRATION
    else:
        marked.pop("migration", None)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(marked, handle)
            handle.flush()
            os.fsync(handle.fileno())
        if before is not None:
            if hasattr(os, "chown"):
                os.chown(tmp, before.st_uid, before.st_gid)
            os.chmod(tmp, stat.S_IMODE(before.st_mode))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp)


def _publish_marker(base: Path, prepared: Path) -> None:
    """The one rename that makes a prepared marker the marker, durably."""
    path = layout.marker_path(base)
    os.replace(prepared, path)
    if hasattr(os, "O_DIRECTORY"):
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def _mark(base: Path, document: dict, state: str) -> None:
    _publish_marker(base, _prepare_marker(base, document, state))


def _keep_sidecars_readable(base: Path) -> None:
    """SQLite sidecars this root process may have left beside the catalogue
    belong to the daemon, like the database."""
    if not hasattr(os, "chown"):
        return
    database = db_path(base)
    owner = database.stat()
    for suffix in ("-wal", "-shm", "-journal"):
        side = database.with_name(database.name + suffix)
        try:
            info = side.lstat()
        except FileNotFoundError:
            continue
        if (info.st_uid, info.st_gid) != (owner.st_uid, owner.st_gid):
            os.chown(side, owner.st_uid, owner.st_gid)


def inspect_bindings(base_path: str | Path) -> dict[str, object]:
    """What ``check`` looked at. ``pending`` is the addressable count ``apply`` moves.

    A catalogue that was never created is a real zero. An unreadable catalogue
    raises, so a caller cannot confuse "could not look" with "nothing to move".
    """
    from tinyassets.commons_catalog import _binding_from_row

    base = Path(base_path)
    if not _catalogue_exists(base):
        return {"pending": 0, "catalogue": False, "bindings": 0, "addressable": 0}
    conn = sqlite3.connect(db_path(base), timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM agent_bindings").fetchall()
    finally:
        conn.close()
    addressable = sum(1 for row in rows if _is_agent(_binding_from_row(row)))
    return {
        "pending": addressable,
        "catalogue": True,
        "bindings": len(rows),
        "addressable": addressable,
    }


def pending(base_path: str | Path) -> int:
    """How many bindings ``apply`` would move. Read-only; opens no schema."""
    return int(inspect_bindings(base_path)["pending"])


def _post_commit_warning(step: str, error: Exception) -> None:
    try:
        print(f"agent-files migration committed; {step} failed: {error}", file=sys.stderr)
    except OSError:
        pass


def apply(base_path: str | Path, *, backup: str | Path) -> dict[str, list[str]]:
    """Move every addressable binding, or nothing. ``{universe_id: [directories]}``."""
    from tinyassets.commons_catalog import _agent_connect

    base = Path(base_path)
    _verify_backup(Path(backup))
    if not _catalogue_exists(base):
        return {}
    fd = layout._open_lock(base)
    committed = False
    try:
        try:
            layout._lock(fd, exclusive=True, blocking=False)
        except BlockingIOError:
            raise RuntimeError("the data-layout lock is held: stop the daemon before "
                               "migrating") from None
        try:
            document = layout.read_marker(base) or {"layout": layout.LAYOUT,
                                                    "state": layout.STABLE}
            layout._validated(base, document)
            result: dict[str, list[str]] = {}
            writer = stable = None
            try:
                with _agent_connect(base) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    agents, without_agents_dir = _plan(conn, base)
                    if not agents:
                        return {}
                    _mark(base, document, MIGRATING)
                    writer = _Writer(base, without_agents_dir)
                    for agent in agents:
                        writer.write(agent)
                        result.setdefault(agent.universe_id, []).append(agent.directory)
                    for agent in agents:
                        conn.execute("DELETE FROM agent_bindings WHERE agent_binding_id = ?",
                                     (agent.binding_id,))
                    # Prepare all file content and metadata before the SQL commit.
                    _keep_sidecars_readable(base)
                    stable = _prepare_marker(base, document, layout.STABLE)
            except BaseException:
                if stable is not None:
                    stable.unlink(missing_ok=True)
                if writer is not None:
                    writer.undo()
                raise
            committed = True
            try:
                _publish_marker(base, stable)
            except Exception as exc:
                _post_commit_warning("stable marker publication", exc)
        finally:
            try:
                layout._unlock(fd)
            except Exception as exc:
                if not committed:
                    raise
                _post_commit_warning("layout unlock", exc)
    finally:
        try:
            os.close(fd)
        except Exception as exc:
            if not committed:
                raise
            _post_commit_warning("layout lock close", exc)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tinyassets.agent_file_migration",
                                     description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="exit 10 while bindings remain to move, 0 "
                                              "when none")
    check.add_argument("base", help="the data root")
    run = commands.add_parser("apply", help="move every addressable binding, all or nothing")
    run.add_argument("base", help="the data root")
    run.add_argument("--backup", required=True,
                     help="the fresh full-volume snapshot this run restores to")
    args = parser.parse_args(argv)
    if args.command == "check":
        report = inspect_bindings(args.base)
        print(json.dumps(report, sort_keys=True))
        return PENDING if report["pending"] else 0
    print(json.dumps(apply(args.base, backup=args.backup), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

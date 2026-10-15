"""Activities conversion inside the owner-box layout migration.

The original definitions and immutable versions stay in their SQL stores. A
per-center failure is recorded on its activities and cannot hide another
owner's work. The marker is written only after every center was visited.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from tinyassets import agent_activities as activities
from tinyassets import runs
from tinyassets.storage import DB_FILENAME

REASON = "Legacy Activities work could not be carried into the new agent session"
UNAVAILABLE = "ended during the activity cutover: your box was unavailable; start it again"
DEFAULT_PROMPT = (
    "You are working on one of your activities in the background; your owner "
    "is not in this chat.\n\nActivity: {title}\n\n{brief}\n\n"
    "Work until it is done. When you finish, reply with a short summary of "
    "the result and where you put it."
)


@contextmanager
def _db(path: Path, *, write: bool = False):
    mode = "rw" if write else "ro"
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _centers(data: Path) -> dict[str, str]:
    if not (data / DB_FILENAME).is_file():
        return {}
    with _db(data / DB_FILENAME) as db:
        return dict(db.execute("SELECT universe_id, owner_id FROM universe_owner"))


def _pending(data: Path) -> list[str]:
    branch_centers = set()
    if (data / DB_FILENAME).is_file():
        with _db(data / DB_FILENAME) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='branch_definitions'").fetchone():
                branch_centers = {row[0].removeprefix("activities::") for row in db.execute(
                    "SELECT branch_def_id FROM branch_definitions "
                    "WHERE branch_def_id LIKE 'activities::%'")}
    activity_centers = {p.parent.name for p in (data / ".agent-sessions").glob(
        "*/agent-activities.db")}
    run_centers = set()
    if (data / ".runs.db").is_file():
        with _db(data / ".runs.db") as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='runs'").fetchone():
                run_centers = {row[0].removeprefix("activities::") for row in db.execute(
                    "SELECT DISTINCT branch_def_id FROM runs "
                    "WHERE branch_def_id LIKE 'activities::%' "
                    "AND status IN ('queued','running','resumed')")}
    pending = sorted(center for center in branch_centers | activity_centers | run_centers
                     if not _converted(data, center))
    return pending


def _converted(data: Path, center: str) -> bool:
    path = activities.store_path(data / center)
    if any(part.is_symlink() for part in (path.parent.parent, path.parent, path)):
        raise RuntimeError(f"unsafe activity record store {path}")
    if not path.is_file():
        return False
    if path.stat().st_nlink != 1:
        raise RuntimeError(f"linked activity record store {path}")
    with _db(path) as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='activity_shape_cutover'"
        ).fetchone()
        return bool(table and db.execute(
            "SELECT 1 FROM activity_shape_cutover WHERE version=1"
        ).fetchone())


def _record_converted(data: Path, center: str) -> None:
    path = activities.store_path(data / center)
    for directory in (path.parent.parent, path.parent):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise RuntimeError(f"unsafe activity record directory {directory}")
        if not directory.exists():
            directory.mkdir()
            if hasattr(os, "chown"):
                os.chown(directory, 1001, 1001)
    if (path.is_symlink() or (path.exists() and not path.is_file())
            or (path.exists() and path.stat().st_nlink != 1)):
        raise RuntimeError(f"unsafe activity record store {path}")
    new_store = not path.exists()
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS activity_shape_cutover "
                   "(version INTEGER PRIMARY KEY CHECK(version=1))")
        db.execute("INSERT OR IGNORE INTO activity_shape_cutover VALUES (1)")
        db.commit()
    finally:
        db.close()
    if new_store and hasattr(os, "chown"):
        os.chown(path, 1001, 1001)


def _write_owner_file(center: Path, name: str, text: str) -> None:
    path = center / name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"unsafe activity instruction target {path}")
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise RuntimeError(f"owner already has a different {name}; refusing overwrite")
    if path.exists():
        return
    temporary = center / f".{name}.{os.getpid()}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if hasattr(os, "chown"):
            owner = center.stat()
            os.chown(temporary, owner.st_uid, owner.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _definition(data: Path, center: str, owner: str) -> tuple[str | None, list | None, str]:
    from tinyassets.graph_compiler import _placeholder_keys
    from tinyassets.shared_self import _agent_nodes
    from tinyassets.universe_files import MAX_UNIVERSE_FILE_BYTES

    with _db(data / DB_FILENAME) as db:
        row = db.execute("SELECT author, node_defs_json, graph_json FROM branch_definitions "
                         "WHERE branch_def_id=?", (f"activities::{center}",)).fetchone()
    if not owner:
        return None, None, "Activities have no bound owner"
    if row is None:
        return DEFAULT_PROMPT, ["agent"], ""
    nodes = json.loads(row["node_defs_json"])
    graph = json.loads(row["graph_json"])
    prompt = next((n.get("prompt_template") for n in nodes
                   if isinstance(n, dict) and isinstance(n.get("prompt_template"), str)), None)
    reason = ""
    if row["author"] != owner:
        reason = "Activities definition belongs to a different owner"
        prompt = None  # Never copy a foreign author's instructions into this box.
    elif (len(nodes) != 1 or len(graph.get("nodes", [])) != 1
          or not isinstance(prompt, str) or not prompt.strip()
          or any(n.get(k) for n in nodes for k in
                 ("effects", "invoke_branch_spec", "invoke_branch_version_spec"))):
        reason = "Activities definition is not one agent instruction node"
    else:
        try:
            if len(_agent_nodes({"node_defs": nodes})) != 1:
                reason = "Activities definition has no executable agent node"
            elif set(_placeholder_keys(prompt)) - {"title", "brief"}:
                reason = "Activities instructions reference unavailable legacy state"
            elif len(prompt.encode("utf-8")) > MAX_UNIVERSE_FILE_BYTES:
                reason = "Activities instructions exceed the owner file limit"
        except (TypeError, ValueError) as exc:
            reason = f"Activities agent node cannot be represented: {exc}"
    tools = nodes[0].get("tools_allowed") if len(nodes) == 1 and prompt is not None else None
    if not reason and not isinstance(tools, list):
        reason = "Activities tool grant is not representable"
    return prompt, tools, reason


def _settle_center(data: Path, center: str, owner: str, reason: str) -> dict:
    root = data / center
    path = activities.store_path(root)
    converted = failed = 0
    old_runs = []
    if (data / ".runs.db").is_file():
        with _db(data / ".runs.db") as runs_db:
            old_runs = runs_db.execute(
                "SELECT run_id, status, owner_user_id, queue_universe_id FROM runs "
                "WHERE branch_def_id=? "
                "AND status IN ('queued','running','resumed')",
                (f"activities::{center}",),
            ).fetchall()
    linked = set()
    if path.is_file():
        with activities._txn(root) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM activities WHERE status NOT IN "
                              "('completed','failed')").fetchall()
            for row in rows:
                if row["owner_principal"] != owner:
                    continue
                if row["runner_token"]:
                    linked.add(row["runner_token"])
                old = next((run for run in old_runs
                            if run["run_id"] == row["runner_token"]), None)
                cause = reason
                if old and old["owner_user_id"] != owner:
                    cause = "cross-owner legacy run refused"
                if old and old["queue_universe_id"] != center:
                    cause = "cross-center legacy run refused"
                if (row["status"] == activities.IN_PROGRESS
                        and (old is None or old["status"] != "queued")):
                    cause = cause or ("Legacy run may already have made effects; "
                                      "review its history")
                if cause:
                    failed += 1
                    status = activities.FAILED
                    summary = f"{REASON}: {cause}. Original run and definition are retained."
                elif row["status"] == activities.IN_PROGRESS:
                    converted += 1
                    status = activities.SCHEDULED
                    summary = "Legacy queued run replaced by a new activity session."
                else:
                    continue
                db.execute("UPDATE activities SET status=?, outcome=?, result_summary=?, "
                           "runner_token='', retiring_token='', runner_generation="
                           "runner_generation+1, revision=revision+1, updated_at=?, "
                           "finished_at=? WHERE activity_id=?",
                           (status, "legacy_conversion_failed" if cause else "",
                            summary, time.time(), time.time() if cause else 0,
                            row["activity_id"]))
                activities._event(db, {**dict(row), "status": status}, status,
                                  summary)
    owned_runs = [run for run in old_runs if run["owner_user_id"] == owner]
    for run in owned_runs:
        if run["queue_universe_id"] != center:
            message = f"{REASON}: cross-center run refused"
        elif reason:
            message = f"{REASON}: {reason}"
        elif run["run_id"] not in linked:
            message = f"{REASON}: no activity record names this run"
        else:
            message = ("Legacy Activities run ended for activity-session "
                       "conversion; see its activity status")
        runs.update_run_status(data, run["run_id"], status="failed", error=message,
                               finished_at=time.time())
    return {"converted": converted, "failed": failed, "legacy_runs_ended": len(owned_runs)}


def convert(data: Path, owner_roots: dict[str, Path], *, unavailable=None) -> dict:
    """Convert under the box migration's stopped-writer and paired-backup gate."""
    report = {}
    unsafe = []
    centers = _centers(data)
    unavailable = unavailable or {}
    for center in _pending(data):
        owner = centers.get(center, "")
        root = owner_roots.get(center)
        try:
            if center in unavailable:
                report[center] = _settle_center(data, center, owner, UNAVAILABLE)
                _record_converted(data, center)
                continue
            if root is None or not root.is_dir() or root.is_symlink():
                raise RuntimeError("admitted owner box is absent")
            prompt, tools, reason = _definition(data, center, owner)
            if prompt is not None:
                _write_owner_file(root, "Activities.md", prompt)
            if tools is not None:
                _write_owner_file(root, "Activities.tools.json", json.dumps(tools))
            if reason:
                _write_owner_file(root, "Activities.error.txt", reason)
            report[center] = _settle_center(data, center, owner, reason)
            _record_converted(data, center)
        except Exception as exc:
            reason = f"Activities conversion refused: {exc}"
            try:
                if root is not None and not (root / "Activities.error.txt").is_file():
                    _write_owner_file(root, "Activities.error.txt", reason)
                report[center] = _settle_center(data, center, owner, reason)
                _record_converted(data, center)
            except Exception as fence_error:
                unsafe.append(f"{center}: {fence_error}")
            report.setdefault(center, {"error": reason})
    if unsafe:
        raise RuntimeError("activity conversion could not fence: " + "; ".join(unsafe))
    print(json.dumps(report, sort_keys=True))
    return report

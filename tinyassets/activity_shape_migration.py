"""One-shot Activities cutover under deploy_fail_safe's stopped/snapshot gate.

The original definitions and immutable versions stay in their SQL stores. A
per-center failure is recorded on its activities and cannot hide another
owner's work. The marker is written only after every center was visited.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from tinyassets import agent_activities as activities
from tinyassets import runs, storage_layout
from tinyassets.box_layout_migration import _atomic_json, _marker, _verified_archive
from tinyassets.storage import DB_FILENAME

PENDING = 10
MARKER = ".activity-shape-v1.json"
REASON = "Legacy Activities work could not be carried into the new agent session"
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
    if (data / MARKER).is_file():
        marker = json.loads((data / MARKER).read_text(encoding="utf-8"))
        if not isinstance(marker, dict):
            raise RuntimeError("invalid activity conversion marker")
        return []
    branch_centers = set()
    if (data / DB_FILENAME).is_file():
        with _db(data / DB_FILENAME) as db:
            branch_centers = {row[0].removeprefix("activities::") for row in db.execute(
                "SELECT branch_def_id FROM branch_definitions "
                "WHERE branch_def_id LIKE 'activities::%'")}
    activity_centers = {p.parent.name for p in (data / ".agent-sessions").glob(
        "*/agent-activities.db")}
    run_centers = set()
    if (data / ".runs.db").is_file():
        with _db(data / ".runs.db") as db:
            run_centers = {row[0].removeprefix("activities::") for row in db.execute(
                "SELECT DISTINCT branch_def_id FROM runs "
                "WHERE branch_def_id LIKE 'activities::%' "
                "AND status IN ('queued','running','resumed')")}
    pending = sorted(branch_centers | activity_centers | run_centers)
    return pending


def check(data: Path) -> int:
    pending = _pending(data)
    print(json.dumps({"activity_shape_pending": pending}))
    return PENDING if pending else 0


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
                    if row["runner_token"]:
                        linked.add(row["runner_token"])
                    old = next((run for run in old_runs
                                if run["run_id"] == row["runner_token"]), None)
                    cause = reason
                    if row["owner_principal"] != owner:
                        cause = "cross-owner activity record refused"
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
    for run in old_runs:
        if run["owner_user_id"] != owner or run["queue_universe_id"] != center:
            message = f"{REASON}: cross-owner or cross-center run refused"
        elif reason:
            message = f"{REASON}: {reason}"
        elif run["run_id"] not in linked:
            message = f"{REASON}: no activity record names this run"
        else:
            message = ("Legacy Activities run ended for activity-session "
                       "conversion; see its activity status")
        runs.update_run_status(data, run["run_id"], status="failed", error=message,
                               finished_at=time.time())
    return {"converted": converted, "failed": failed, "legacy_runs_ended": len(old_runs)}


def _apply(data: Path) -> dict:
    if (data / MARKER).exists():
        return json.loads((data / MARKER).read_text(encoding="utf-8"))
    report = {}
    unsafe = []
    centers = _centers(data)
    for center in _pending(data):
        owner = centers.get(center, "")
        root = data / center
        try:
            if not root.is_dir() or root.is_symlink():
                raise RuntimeError("owner center is not a real directory")
            prompt, tools, reason = _definition(data, center, owner)
            if prompt is not None:
                _write_owner_file(root, "Activities.md", prompt)
            if tools is not None:
                _write_owner_file(root, "Activities.tools.json", json.dumps(tools))
            if reason:
                _write_owner_file(root, "Activities.error.txt", reason)
            report[center] = _settle_center(data, center, owner, reason)
        except Exception as exc:
            reason = f"Activities conversion refused: {exc}"
            try:
                if not (root / "Activities.error.txt").is_file():
                    _write_owner_file(root, "Activities.error.txt", reason)
                with _db(data / ".runs.db") as db:
                    outstanding = [row[0] for row in db.execute(
                        "SELECT run_id FROM runs WHERE branch_def_id=? "
                        "AND status IN ('queued','running','resumed')",
                        (f"activities::{center}",))]
                for run_id in outstanding:
                    runs.update_run_status(data, run_id, status="failed", error=reason,
                                           finished_at=time.time())
            except Exception as fence_error:
                unsafe.append(f"{center}: {fence_error}")
            report[center] = {"error": reason}
    if unsafe:
        raise RuntimeError("activity conversion could not fence: " + "; ".join(unsafe))
    temporary = data / f"{MARKER}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    os.replace(temporary, data / MARKER)
    print(json.dumps(report, sort_keys=True))
    return report


def apply(data: Path, backup: Path) -> dict:
    """Run only behind the deploy's stopped-writer and verified backup gate."""
    _verified_archive(backup)
    lock = storage_layout._open_lock(data)
    try:
        storage_layout._lock(lock, exclusive=True, blocking=False)
        marker = (_marker(data) if storage_layout.marker_path(data).is_file() else {
            "layout": storage_layout.LAYOUT, "state": storage_layout.STABLE,
        })
        if (data / MARKER).is_file():
            return json.loads((data / MARKER).read_text(encoding="utf-8"))
        _atomic_json(storage_layout.marker_path(data), {
            **marker, "state": "migrating", "migration": "activity-shape",
        })
        result = _apply(data)
        stable = {**marker, "state": "stable"}
        stable.pop("migration", None)
        _atomic_json(storage_layout.marker_path(data), stable)
        return result
    finally:
        storage_layout._unlock(lock)
        os.close(lock)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "apply"))
    parser.add_argument("data", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "check":
            return check(args.data)
        if args.backup is None:
            parser.error("apply requires the verified deploy snapshot")
        apply(args.data, args.backup)
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Activities conversion refused: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

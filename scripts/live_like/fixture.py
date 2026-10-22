#!/usr/bin/env python3
"""Build a synthetic, census-valid owner-split data volume for the deploy gate."""

from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.live_like import shape_profile
from tinyassets.storage import platform_state_move as psm

BUCKETS = shape_profile.BUCKETS
DEFAULT = shape_profile.DEFAULT
REPO = Path(__file__).resolve().parents[2]

OWNERS = (("alice", 300001), ("bob", 300002), ("carol", 300003))
CENTERS = (("a1", "alice"), ("a2", "alice"), ("b1", "bob"), ("b2", "bob"), ("c1", "carol"))


def volume_root(name: str) -> Path:
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in name):
        raise ValueError("invalid Docker volume name")
    subprocess.run(["docker", "volume", "create", name], check=True, capture_output=True)
    result = subprocess.run(
        ["docker", "volume", "inspect", name, "--format", "{{ .Mountpoint }}"],
        check=True,
        capture_output=True,
        text=True,
    )
    root = Path(result.stdout.strip())
    if not root.is_absolute() or not root.is_dir():
        raise RuntimeError("Docker volume has no directory mountpoint")
    return root


def sql(path: Path, statements: str, rows: list[tuple] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(statements)
        if rows:
            db.executemany("INSERT INTO fixture_rows VALUES (?,?,?)", rows)


def _automations_schema() -> str:
    """tinyassets/automations.py's own DDL, read without importing its deps."""
    source = (REPO / "tinyassets/automations.py").read_text(encoding="utf-8")
    names = {"_AUTOMATIONS_TABLE", "_SCHEMA"}
    body = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and {getattr(t, "id", "") for t in node.targets} & names
    ]
    namespace: dict = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "automations.py", "exec"), namespace)
    return namespace["_SCHEMA"]


def fixture_coverage(profile: dict) -> list[str]:
    """Validate the shapes the profile-driven builder can materialize."""
    errors = []
    for path, store in profile["stores"].items():
        if not path.endswith((".db", ".sqlite3")) or not store["tables"]:
            errors.append(f"fixture cannot build store: {path}")
        for table, spec in store["tables"].items():
            if not spec["columns"]:
                errors.append(f"fixture cannot build table without columns: {path}/{table}")
    for item in profile["files"]:
        if (
            item["count"] not in BUCKETS
            or set(item["kinds"]) - {"file", "socket", "fifo", "symlink"}
            or set(item["links"]) - {"hardlink"}
        ):
            errors.append(f"fixture cannot build file shape: {item['pattern']}")
    return errors


def _representative(bucket_name: str) -> int:
    return {"0": 0, "1-10": 1, "11-1k": 11, "1k-100k": 1001, ">100k": 1001}[bucket_name]


def _materialize_pattern(root: Path, pattern: str, index: int = 0) -> Path:
    parts = []
    for part in pattern.split("/"):
        part = (
            part.replace("<owner>", "300001")
            .replace("<center>", "a1")
            .replace("<universe>", "a1")
            .replace("<id>", f"synthetic-{index}")
            .replace("<router-private>", ".connect")
            .replace("<file>", f"synthetic-{index}")
        )
        parts.append(part)
    return root.joinpath(*parts)


def apply_profile(root: Path, profile: dict) -> None:
    errors = fixture_coverage(profile)
    if errors:
        raise ValueError("; ".join(errors))
    for name, store in profile["stores"].items():
        # Stores and tables that hold no rows are left to the code to create
        # when the live image boots: profile columns can be a partial parse,
        # and a short table would turn the code's create into a no-op and
        # break the daemon.
        tables = {t: spec for t, spec in store["tables"].items() if spec["rows"] != "0"}
        if not tables:
            continue
        path = _materialize_pattern(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            for table, spec in tables.items():
                columns = spec["columns"]
                existing = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
                if not existing:
                    ddl = ", ".join(f'"{col}" {kind or "TEXT"}' for col, kind in columns.items())
                    db.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({ddl})')
                else:
                    for col, kind in columns.items():
                        if col not in existing:
                            db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {kind or "TEXT"}')
                actual = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                needed = max(0, _representative(spec["rows"]) - actual)
                if needed:
                    # Insert only into nullable fields; generated shape rows carry no real identity.
                    db.executemany(f'INSERT INTO "{table}" DEFAULT VALUES', [()] * needed)
    for item in profile["files"]:
        if item["class"] == "database_files":
            continue  # Store creation above owns these paths.
        if item["count"] == "0":
            continue
        matcher = shape_profile._pattern_regex(item["pattern"])
        existing = {"file": 0, "fifo": 0, "socket": 0, "symlink": 0}
        for base, dirs, files in os.walk(root, followlinks=False):
            for name in files + [n for n in dirs if (Path(base) / n).is_symlink()]:
                path = Path(base) / name
                if not matcher.fullmatch(path.relative_to(root).as_posix()):
                    continue
                mode = path.lstat().st_mode
                kind = (
                    "socket"
                    if stat.S_ISSOCK(mode)
                    else "fifo"
                    if stat.S_ISFIFO(mode)
                    else "symlink"
                    if stat.S_ISLNK(mode)
                    else "file"
                )
                existing[kind] += 1
        for kind, count_bucket in item["kinds"].items():
            needed = max(0, _representative(count_bucket) - existing[kind])
            for index in range(needed):
                path = _materialize_pattern(root, item["pattern"], index)
                if path.exists() or path.is_symlink():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "file":
                    path.write_bytes(b"synthetic\n")
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "socket":
                    import socket

                    # Production's only sockets are the daemon's relay
                    # sockets (role_relays.directory names, which backup.sh's
                    # snapshot inventory admits); a synthetic name is one no
                    # live volume can hold.
                    path = path.with_name(f"egress-{index + 1}.sock")
                    with socket.socket(socket.AF_UNIX) as sock:
                        sock.bind(str(path))
                else:
                    target = path.parent / "synthetic-target"
                    target.write_bytes(b"synthetic\n")
                    path.symlink_to(target.name)
        if item["links"].get("hardlink"):
            path = _materialize_pattern(root, item["pattern"])
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic\n")
            if path.is_file() and not path.is_symlink():
                sibling = path.with_name(path.name + ".synthetic-hardlink")
                if not sibling.exists():
                    os.link(path, sibling)


def build(root: Path, profile: dict | None = None) -> dict:
    if any(root.iterdir()):
        raise RuntimeError(f"fixture volume must be empty: {root}")
    (root / ".layout.json").write_text(
        json.dumps(
            {
                "layout": 2,
                "split": "owner-split",
                "state": "stable",
                psm.MOVES: {psm.CONSENTS: psm.DONE},
            }
        ),
        encoding="utf-8",
    )
    (root / "platform-expected-instance.json").write_text(
        json.dumps(
            {
                "schema": "platform_expected_instance",
                "version": 1,
                "expected_instance_id": "123456789",
            }
        ),
        encoding="utf-8",
    )
    (root / ".layout.lock").touch()
    identity_rows = ",".join(f"('{owner}',{machine})" for owner, machine in OWNERS)
    admission_rows = ",".join(
        f"({index},'admit','{owner}','{center}',{dict(OWNERS)[owner]})"
        for index, (center, owner) in enumerate(CENTERS, 1)
    )
    sql(
        root / ".broker/state/owner-identities.db",
        f"""
        CREATE TABLE owner_identities (principal TEXT PRIMARY KEY, machine_id INTEGER);
        INSERT INTO owner_identities VALUES {identity_rows};
        CREATE TABLE center_admissions (generation INTEGER, event TEXT, principal TEXT,
                                        center TEXT, machine INTEGER);
        INSERT INTO center_admissions VALUES {admission_rows};
    """,
    )
    # Tables the fixture fills carry the code's own DDL (universe_owner.py,
    # storage/agent_turn_journal.py): a short table makes the code's
    # CREATE ... IF NOT EXISTS a no-op and its indexes and queries fail on
    # columns production has.
    bindings = ",".join(f"('{center}','{owner}',1,'creation')" for center, owner in CENTERS)
    turns = ",".join(
        f"('{owner}','{center}','turn-{center}',1,1,'completed',0,'{{}}','2026-01-01T00:00:00Z')"
        for center, owner in CENTERS
    )
    sql(
        root / ".tinyassets.db",
        f"""
        CREATE TABLE universe_owner (
            universe_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, bound_at REAL NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('creation', 'founder_home')));
        CREATE INDEX idx_universe_owner_owner ON universe_owner(owner_id);
        INSERT INTO universe_owner VALUES {bindings};
        CREATE TABLE agent_turns (
            owner_user_id TEXT NOT NULL, universe_id TEXT NOT NULL, turn_id TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version = 1),
            generation INTEGER NOT NULL CHECK(generation > 0),
            state TEXT NOT NULL, round_ordinal INTEGER NOT NULL CHECK(round_ordinal >= 0),
            input_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(owner_user_id, universe_id, turn_id));
        INSERT INTO agent_turns VALUES {turns};
    """,
    )
    sql(
        root / ".runs.db",
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, branch_def_id TEXT NOT NULL,
            run_name TEXT NOT NULL DEFAULT '', thread_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', actor TEXT NOT NULL,
            owner_user_id TEXT NOT NULL DEFAULT '', inputs_json TEXT NOT NULL DEFAULT '{}',
            output_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
            last_node_id TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL,
            finished_at REAL, provider_used TEXT, model TEXT, token_count INTEGER,
            daemon_id TEXT, runtime_instance_id TEXT, worker_id TEXT,
            branch_task_id TEXT, queue_universe_id TEXT
        );
        INSERT INTO runs (run_id,branch_def_id,thread_id,actor,owner_user_id,started_at,
                          queue_universe_id) VALUES
            ('run-a1','branch-a1','thread-a1','alice','alice',1,'a1'),
            ('run-b1','branch-b1','thread-b1','bob','bob',1,'b1'),
            ('run-c1','branch-c1','thread-c1','carol','carol',1,'c1');
    """,
    )
    sql(root / ".automations.db", _automations_schema())
    sql(
        root / ".engine_run_admissions.db",
        """
        CREATE TABLE admissions (universe_id TEXT NOT NULL, ts REAL NOT NULL,
                                 kind TEXT NOT NULL DEFAULT 'write',
                                 run_id TEXT NOT NULL DEFAULT '');
        CREATE TABLE settlements (run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, ts REAL NOT NULL);
        CREATE INDEX admissions_ts ON admissions(ts);
    """,
    )
    files = 0
    for center, owner in CENTERS:
        center_root = root / center
        center_root.mkdir()
        (center_root / "notes.txt").write_text(
            f"synthetic history for {center}\n", encoding="utf-8"
        )
        sidecar = root / ".universe-sidecars" / center
        sidecar.mkdir(parents=True)
        (sidecar / "state.json").write_text(
            json.dumps({"center": center, "status": "idle"}), encoding="utf-8"
        )
        workspace = (
            root
            / ".platform/owners"
            / str(dict(OWNERS)[owner])
            / "agent-home"
            / "workspace"
            / center
        )
        workspace.mkdir(parents=True, exist_ok=True)
        for index in range(600):
            (workspace / f"file-{index:04d}.txt").write_text(
                f"synthetic {owner}/{center}/{index}\n", encoding="utf-8"
            )
            files += 1
    if profile is not None:
        apply_profile(root, profile)
    # Compose daemon (uid 1001) and broker (uid 1002) need to read the shared tree.
    for base, dirs, names in os.walk(root):
        os.chmod(base, 0o775)
        os.chown(base, 1001, 1100)
        for name in names:
            path = Path(base) / name
            os.chmod(path, 0o664)
            os.chown(path, 1001, 1100)
    for base, dirs, names in os.walk(root / ".broker"):
        os.chown(base, 1002, 1102)
        os.chmod(base, 0o700)
        for name in names:
            path = Path(base) / name
            os.chown(path, 1002, 1102)
            os.chmod(path, 0o600)
    for center, owner in CENTERS:
        machine = dict(OWNERS)[owner]
        for tree in (root / center, root / ".universe-sidecars" / center):
            for base, dirs, names in os.walk(tree):
                os.chown(base, 1001, machine)
                os.chmod(base, 0o770)
                for name in names:
                    path = Path(base) / name
                    os.chown(path, 1001, machine)
                    os.chmod(path, 0o660)
    return {
        "volume": str(root),
        "owners": len(OWNERS),
        "centers": len(CENTERS),
        "workspace_files": files,
        "sqlite_stores": 5,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build",))
    parser.add_argument("--volume", default="tinyassets-data")
    parser.add_argument("--profile", type=Path, default=DEFAULT)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("fixture build requires root")
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    shape_profile.check_redaction(profile)
    fresh = {
        **profile,
        "stores": shape_profile.known_stores(shape_profile.source_schema()),
        "code_tables": sorted(shape_profile.source_schema()),
        "code_stores": shape_profile.source_stores(),
    }
    failures = shape_profile.diff(profile, fresh, require_fixture=False) + fixture_coverage(profile)
    if failures:
        parser.error("fixture shape drift:\n" + "\n".join(failures))
    print(
        json.dumps(
            build(volume_root(args.volume), profile),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

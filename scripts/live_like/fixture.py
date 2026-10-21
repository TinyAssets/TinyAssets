#!/usr/bin/env python3
"""Build a synthetic, census-valid owner-split data volume for the deploy gate."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path

OWNERS = (("alice", 300001), ("bob", 300002), ("carol", 300003))
CENTERS = (("a1", "alice"), ("a2", "alice"), ("b1", "bob"),
           ("b2", "bob"), ("c1", "carol"))


def volume_root(name: str) -> Path:
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in name):
        raise ValueError("invalid Docker volume name")
    subprocess.run(["docker", "volume", "create", name], check=True, capture_output=True)
    result = subprocess.run(["docker", "volume", "inspect", name, "--format", "{{ .Mountpoint }}"],
                            check=True, capture_output=True, text=True)
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


def build(root: Path) -> dict:
    if any(root.iterdir()):
        raise RuntimeError(f"fixture volume must be empty: {root}")
    (root / ".layout.json").write_text(
        json.dumps({"layout": 2, "split": "owner-split", "state": "stable"}), encoding="utf-8")
    (root / ".layout.lock").touch()
    identity_rows = ",".join(f"('{owner}',{machine})" for owner, machine in OWNERS)
    admission_rows = ",".join(
        f"({index},'admit','{owner}','{center}',{dict(OWNERS)[owner]})"
        for index, (center, owner) in enumerate(CENTERS, 1))
    sql(root / ".broker/state/owner-identities.db", f"""
        CREATE TABLE owner_identities (principal TEXT PRIMARY KEY, machine_id INTEGER);
        INSERT INTO owner_identities VALUES {identity_rows};
        CREATE TABLE center_admissions (generation INTEGER, event TEXT, principal TEXT,
                                        center TEXT, machine INTEGER);
        INSERT INTO center_admissions VALUES {admission_rows};
    """)
    bindings = ",".join(f"('{center}','{owner}')" for center, owner in CENTERS)
    turns = ",".join(f"('{owner}','{center}','turn-{center}')" for center, owner in CENTERS)
    sql(root / ".tinyassets.db", f"""
        CREATE TABLE universe_owner (universe_id TEXT, owner_id TEXT);
        INSERT INTO universe_owner VALUES {bindings};
        CREATE TABLE agent_turns (owner_user_id TEXT, universe_id TEXT, turn_id TEXT);
        INSERT INTO agent_turns VALUES {turns};
    """)
    sql(root / ".runs.db", """
        CREATE TABLE runs (run_id TEXT, owner_user_id TEXT, queue_universe_id TEXT);
        INSERT INTO runs VALUES ('run-a1','alice','a1'),('run-b1','bob','b1'),
                                ('run-c1','carol','c1');
    """)
    sql(root / ".automations.db", """
        CREATE TABLE automations (automation_id TEXT, owner_principal_id TEXT, universe_id TEXT);
    """)
    sql(root / ".engine_run_admissions.db", """
        CREATE TABLE admissions (run_id TEXT, owner_user_id TEXT, universe_id TEXT);
    """)
    files = 0
    for center, owner in CENTERS:
        center_root = root / center
        center_root.mkdir()
        (center_root / "notes.txt").write_text(
            f"synthetic history for {center}\n", encoding="utf-8")
        sidecar = root / ".universe-sidecars" / center
        sidecar.mkdir(parents=True)
        (sidecar / "state.json").write_text(json.dumps({"center": center, "status": "idle"}),
                                            encoding="utf-8")
        workspace = (root / ".platform/owners" / str(dict(OWNERS)[owner]) /
                     "agent-home" / "workspace" / center)
        workspace.mkdir(parents=True, exist_ok=True)
        for index in range(600):
            (workspace / f"file-{index:04d}.txt").write_text(
                f"synthetic {owner}/{center}/{index}\n", encoding="utf-8")
            files += 1
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
        for name in names:
            os.chown(Path(base) / name, 1002, 1102)
    return {"volume": str(root), "owners": len(OWNERS), "centers": len(CENTERS),
            "workspace_files": files, "sqlite_stores": 5}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build",))
    parser.add_argument("--volume", default="tinyassets-data")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("fixture build requires root")
    print(json.dumps(build(volume_root(args.volume)), sort_keys=True))


if __name__ == "__main__":
    main()

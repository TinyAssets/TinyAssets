"""Offline M1: remove retired workflow difficulty overrides after a full backup."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tarfile
from pathlib import Path


def _backup_contains_database(archive: Path) -> bool:
    if not archive.is_file() or archive.stat().st_size == 0:
        return False
    with tarfile.open(archive, "r:*") as source:
        return any(
            member.isfile() and member.name.removeprefix("./") == ".tinyassets.db"
            for member in source
        )


def _rewrite(raw: str, *, nodes: bool) -> tuple[str, int]:
    value = json.loads(raw)
    if nodes and not isinstance(value, list):
        raise ValueError("branch node definitions are not a list")
    if not nodes and value is not None and not isinstance(value, dict):
        raise ValueError("branch default model policy is not an object")
    count = 0
    if nodes:
        if any(not isinstance(node, dict) for node in value):
            raise ValueError("branch node definition is not an object")
        policies = [node.get("llm_policy") for node in value]
    else:
        policies = [value]
    for policy in policies:
        if policy is not None and not isinstance(policy, dict):
            raise ValueError("branch model policy is not an object")
        if isinstance(policy, dict) and "difficulty_override" in policy:
            del policy["difficulty_override"]
            count += 1
    if not count:
        return raw, 0
    return json.dumps(value, sort_keys=True, separators=(",", ":")), count


def migrate(data_root: Path, *, archive: Path | None = None, apply: bool = False,
            writers_stopped: bool = False) -> dict:
    database = data_root.resolve() / ".tinyassets.db"
    if not database.is_file():
        raise FileNotFoundError(database)
    if apply and (not writers_stopped or archive is None or not _backup_contains_database(archive)):
        raise PermissionError("apply requires stopped writers and a full backup archive")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode={'rw' if apply else 'ro'}",
                                 uri=True, timeout=0)
    try:
        connection.execute("BEGIN EXCLUSIVE" if apply else "BEGIN")
        changed = []
        removed = 0
        for branch_id, nodes_raw, default_raw in connection.execute(
            "SELECT branch_def_id, node_defs_json, default_llm_policy_json "
            "FROM branch_definitions ORDER BY branch_def_id"
        ):
            nodes, node_count = _rewrite(nodes_raw, nodes=True)
            default, default_count = (
                _rewrite(default_raw, nodes=False) if default_raw is not None
                else (None, 0)
            )
            if node_count or default_count:
                changed.append(branch_id)
                removed += node_count + default_count
                if apply:
                    connection.execute(
                        "UPDATE branch_definitions SET node_defs_json=?, "
                        "default_llm_policy_json=? WHERE branch_def_id=?",
                        (nodes, default, branch_id),
                    )
        if apply:
            connection.commit()
        else:
            connection.rollback()
        return {"branches": changed, "removed": removed, "applied": apply}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--backup-archive", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.data_root, archive=args.backup_archive,
                             apply=args.apply, writers_stopped=args.writers_stopped),
                     sort_keys=True))


if __name__ == "__main__":
    main()

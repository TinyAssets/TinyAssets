"""Re-establish owner-lease generations after a platform restore. Offline only.

Change ``execution-owner-lease`` D2. A restore can bring back a lease store that
is BEHIND the stores it fences (a fence advanced, or turn rows written, after the
lease store's last backup). Acquiring from that lease store would reuse a
generation a recovered store already carries, and an old owner's leftovers would
then read as the new owner's own. So after a restore, before any owner starts:

1. take the host-mutation flock and hold it to the end;
2. stop every running container with ANY mount whose source overlaps the data
   directory being restored (the named volume's mountpoint, a bind of it, or a
   bind of a parent or child path), found by inspecting mounts -- never by a
   hardcoded volume name -- unless ``--stack-already-stopped``;
3. commit ``restore_state = 'in_progress'`` -- every acquisition refuses while it
   is set, so a crash anywhere below fails CLOSED until this tool is re-run;
4. compute each key's high-water from the recovered lease generation, every fence
   in every store -- the UNION of the recovered catalog and each store kind's own
   path enumerator, so neither a store the catalog never saw nor one only the
   catalog knows is missed -- and every ``agent_turns.owner_generation``;
5. in ONE transaction: write each key ``released`` at its high-water, record the
   manifest and clear ``restore_state``.

Containers are restarted only after this exits 0.

    python scripts/owner_lease_restore.py --data-dir /var/lib/docker/volumes/tinyassets-data/_data
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tinyassets import owner_lease  # noqa: E402
from tinyassets.storage.owner_fence import stored_fences  # noqa: E402

LOCK_FILE = "/var/lock/tinyassets-host-mutation.lock"


def begin(data_dir: Path) -> None:
    with owner_lease.lease_db(data_dir) as conn:
        conn.execute(
            "INSERT INTO restore_state VALUES (1, 'in_progress', ?, NULL) "
            "ON CONFLICT(singleton) DO UPDATE SET state = 'in_progress', "
            "started_at = excluded.started_at, manifest_json = NULL",
            (owner_lease._now(),),
        )


def _turn_generations(store: Path) -> dict[str, int]:
    """Max owner_generation per command center key in one journal store."""
    conn = sqlite3.connect(store.as_uri() + "?mode=rw", uri=True, timeout=10.0)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='agent_turns'").fetchone():
            return {}
        columns = {r[1] for r in conn.execute("PRAGMA table_info(agent_turns)")}
        if "owner_generation" not in columns:
            return {}
        return {
            owner_lease.key_for(universe): int(generation)
            for universe, generation in conn.execute(
                "SELECT universe_id, MAX(owner_generation) FROM agent_turns GROUP BY universe_id"
            )
        }
    finally:
        conn.close()


def high_water(data_dir: Path) -> dict[str, int]:
    marks: dict[str, int] = {}

    def lift(key: str, generation: int) -> None:
        marks[key] = max(marks.get(key, 0), int(generation))

    with owner_lease.lease_db(data_dir) as conn:
        for row in conn.execute("SELECT owner_key, generation FROM owner_lease"):
            lift(row["owner_key"], row["generation"])
    stores: set[Path] = {path.resolve() for path, _kind in owner_lease.catalog(data_dir)
                         if path.is_file()}
    for _kind, enumerate_paths in owner_lease.STORE_ENUMERATORS.items():
        stores.update(p.resolve() for p in enumerate_paths(data_dir))
    for store in sorted(stores):
        for key, generation in stored_fences(store).items():
            lift(key, generation)
        for key, generation in _turn_generations(store).items():
            lift(key, generation)
    return marks


def finish(data_dir: Path, marks: dict[str, int]) -> dict:
    manifest = {"high_water": marks, "finished_at": owner_lease._now()}
    with owner_lease.lease_db(data_dir) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for key, generation in marks.items():
                conn.execute(
                    "INSERT INTO owner_lease VALUES (?, ?, 'restore', '', 'released', ?, ?) "
                    "ON CONFLICT(owner_key) DO UPDATE SET generation = excluded.generation, "
                    "holder_tree = 'restore', proof_sha256 = '', state = 'released', "
                    "released_at = excluded.released_at",
                    (key, generation, manifest["finished_at"], manifest["finished_at"]),
                )
            conn.execute(
                "UPDATE restore_state SET state = 'none', manifest_json = ? WHERE singleton = 1",
                (json.dumps(manifest, sort_keys=True),),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return manifest


def run(data_dir: Path) -> dict:
    """Steps 3-5. The caller holds the host lock and has stopped the stack."""
    begin(data_dir)
    return finish(data_dir, high_water(data_dir))


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def consumers_of(data_dir: Path, inspected: list[dict]) -> list[str]:
    """Running containers with any mount whose source overlaps ``data_dir``."""
    target = data_dir.resolve()
    found = []
    for container in inspected:
        for mount in container.get("Mounts") or []:
            source = mount.get("Source") or ""
            if source and _overlaps(Path(source).resolve(), target):
                found.append(container["Id"])
                break
    return found


def _running_consumers(data_dir: Path) -> list[str]:
    ids = subprocess.run(["docker", "ps", "-q", "--no-trunc"], capture_output=True,
                         text=True, check=True).stdout.split()
    if not ids:
        return []
    inspected = json.loads(subprocess.run(["docker", "inspect", *ids], capture_output=True,
                                          text=True, check=True).stdout)
    return consumers_of(data_dir, inspected)


def _stop_volume_consumers(data_dir: Path) -> list[str]:
    ids = _running_consumers(data_dir)
    if ids:
        subprocess.run(["docker", "stop", *ids], check=True, capture_output=True)
    still = _running_consumers(data_dir)
    if still:
        raise SystemExit(f"containers still mount {data_dir}: {still}")
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--stack-already-stopped", action="store_true",
                        help="skip step 2; the operator verified nothing mounts the volume")
    args = parser.parse_args(argv)
    if not args.data_dir.is_absolute() or not args.data_dir.is_dir():
        raise SystemExit("--data-dir must be an existing absolute directory")
    import fcntl  # Linux host only, by design

    with open(LOCK_FILE, "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not args.stack_already_stopped:
            stopped = _stop_volume_consumers(args.data_dir)
            print(json.dumps({"stopped": stopped}))
        print(json.dumps(run(args.data_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Broker entrypoint for the credential-vault cutover.

Refuses unless this process is the broker, the key-service probe is green, and
a checksummed legacy backup exists. ``--dry-run`` inventories and deletes
nothing. Archive rewrites use an isolated key database inside each extract.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

from tinyassets.broker.key_service import KeyServiceUnavailable, account_id, for_data_root
from tinyassets.broker.vault_cutover import (
    VAULT_NAME,
    CutoverStopped,
    _is_new_envelope,
    _legacy_members,
    _sha256,
    delete_legacy_browser_keys,
    migrate_browser_sessions,
    migrate_data_root,
    owner_for_center_dir,
    read_legacy_browser_key,
    reseal_extracted_archive,
    rewrite_backup_directory,
)


def _broker_allowed() -> bool:
    uid = os.geteuid()
    if os.environ.get("TINYASSETS_ENV") == "production":
        return uid == 1002
    return uid == 1002 or os.environ.get("TINYASSETS_BROKER_PROCESS") == "1"


def _verify_legacy_backup(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise CutoverStopped(
            "verified legacy backup is missing", manifest={"phase": "backup"}
        )
    sidecar = Path(str(path) + ".sha256")
    if not sidecar.is_file() or sidecar.is_symlink():
        raise CutoverStopped(
            "verified legacy backup is missing", manifest={"phase": "backup"}
        )
    field = sidecar.read_text(encoding="utf-8").split()
    if not field or field[0] != _sha256(path):
        raise CutoverStopped(
            "verified legacy backup checksum did not match",
            manifest={"phase": "backup"},
        )
    try:
        with tarfile.open(path, "r:gz") as handle:
            names = handle.getnames()
    except (tarfile.TarError, OSError) as exc:
        raise CutoverStopped(
            "verified legacy backup is not a credential archive",
            manifest={"phase": "backup"},
        ) from exc
    if not _legacy_members(names):
        raise CutoverStopped(
            "verified legacy backup is not a credential archive",
            manifest={"phase": "backup"},
        )


def _sealed_rows(data_root: Path) -> list[sqlite3.Row]:
    ledger = data_root / ".broker" / "outbound.db"
    if not ledger.is_file() or ledger.is_symlink():
        return []
    with sqlite3.connect(f"file:{ledger}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'browser_vault'"
        ).fetchone()
        if present is None:
            return []
        return list(
            conn.execute(
                "SELECT id, owner, home, revision, sealed FROM browser_vault "
                "WHERE length(sealed) > 0"
            )
        )


def _volume_key_root() -> Path | None:
    try:
        completed = subprocess.run(
            [
                "docker",
                "volume",
                "inspect",
                "-f",
                "{{.Mountpoint}}",
                "tinyassets-browser-keys",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    mount = completed.stdout.strip()
    if completed.returncode != 0 or not mount:
        return None
    return Path(mount) / "browser-vault"


def _key_roots(data_root: Path, browser_key_root: Path, rows: list[sqlite3.Row]) -> list[Path]:
    roots = [browser_key_root, data_root / ".broker" / "browser-vault"]
    needs_volume = False
    for row in rows:
        if _is_new_envelope(bytes(row["sealed"] or b"")):
            continue
        if read_legacy_browser_key(str(row["owner"]), roots) is None:
            needs_volume = True
            break
    if needs_volume:
        extra = _volume_key_root()
        if extra is not None:
            roots.append(extra)
    return roots


def _samples(data_root: Path) -> list[bytes]:
    samples: list[bytes] = []
    for path in data_root.rglob(VAULT_NAME):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for token in ("token", "api_key", "secret"):
            marker = f'"{token}": "'
            start = 0
            while True:
                found = text.find(marker, start)
                if found < 0:
                    break
                begin = found + len(marker)
                end = text.find('"', begin)
                if end > begin + 8:
                    samples.append(text[begin:end].encode())
                start = begin
    return samples


def _refuse_missing_keys(rows: list[sqlite3.Row], roots: list[Path], tombstoned: set[str]) -> bool:
    for row in rows:
        sealed = bytes(row["sealed"] or b"")
        if _is_new_envelope(sealed) or account_id(str(row["owner"])) in tombstoned:
            continue
        try:
            key = read_legacy_browser_key(str(row["owner"]), roots)
        except CutoverStopped as exc:
            print(exc, file=sys.stderr)
            return True
        if key is None:
            print(
                "browser session has no legacy key; nothing was changed",
                file=sys.stderr,
            )
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/vault_cutover.py")
    parser.add_argument("--data-root", type=Path, default=Path("/data"))
    parser.add_argument("--archive-dir", type=Path, default=Path("/var/backups/tinyassets"))
    parser.add_argument("--legacy-backup", type=Path, required=True)
    parser.add_argument(
        "--browser-key-root",
        type=Path,
        default=Path("/var/lib/ta-broker/browser-vault"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not _broker_allowed():
        print("vault cutover runs as the broker (uid 1002)", file=sys.stderr)
        return 1
    try:
        _verify_legacy_backup(args.legacy_backup)
    except CutoverStopped as exc:
        print(exc, file=sys.stderr)
        return 1
    if not args.archive_dir.is_dir():
        print(
            f"archive directory {args.archive_dir} is missing; "
            "old snapshots were not scrubbed",
            file=sys.stderr,
        )
        return 1
    try:
        service = for_data_root(args.data_root)
        probe = service.probe()
    except KeyServiceUnavailable:
        print("key service probe is not green; nothing was changed", file=sys.stderr)
        return 1
    if probe.get("status") != "ok":
        print("key service probe is not green; nothing was changed", file=sys.stderr)
        return 1
    try:
        rows = _sealed_rows(args.data_root)
    except sqlite3.Error:
        print("restored browser session check could not run", file=sys.stderr)
        return 1
    try:
        roots = _key_roots(args.data_root, args.browser_key_root, rows)
    except CutoverStopped as exc:
        print(exc, file=sys.stderr)
        return 1
    tombstoned = service.tombstoned_accounts()
    if _refuse_missing_keys(rows, roots, tombstoned):
        return 1
    vaults = [
        path
        for path in args.data_root.rglob(VAULT_NAME)
        if path.is_file() and not path.is_symlink()
    ]
    archives = [
        path
        for path in args.archive_dir.iterdir()
        if path.is_file() and path.name.endswith(".tar.gz")
    ]
    if args.dry_run:
        print(f"vault files {len(vaults)}")
        print(f"browser sessions {len(rows)}")
        print(f"legacy archives {len(archives)}")
        print("dry-run: nothing removed")
        return 0
    samples = _samples(args.data_root)
    try:
        browser = migrate_browser_sessions(
            args.data_root,
            service=service,
            key_roots=roots,
            tombstoned=tombstoned,
            delete_keys=False,
        )
        if browser["failed"]:
            count = len(browser["failed"])
            raise CutoverStopped(
                f"{count} record(s) did not re-seal; original archive kept",
                manifest={"phase": "browser", "failed": browser["failed"], "failed_count": count},
            )
        migrated = migrate_data_root(
            args.data_root,
            owner_for_center=owner_for_center_dir,
            service=service,
        )
        backup = args.legacy_backup.resolve()
        preserve = {backup.name} if backup.parent == args.archive_dir.resolve() else set()

        def reseal(extracted: Path) -> dict:
            return reseal_extracted_archive(
                extracted,
                provider=service.provider,
                owner_for_center=owner_for_center_dir,
                tombstoned=service.tombstoned_accounts(),
                key_roots=roots,
            )

        manifest = rewrite_backup_directory(
            args.archive_dir,
            reseal=reseal,
            samples=samples,
            preserve=preserve,
        )
    except CutoverStopped as exc:
        print(exc, file=sys.stderr)
        count = exc.manifest.get("failed_count")
        if count:
            print(f"failed_count {count}", file=sys.stderr)
        return 1
    delete_legacy_browser_keys(roots)
    print(f"migrated vault files {migrated['removed']}")
    print(f"browser sessions {browser['migrated']}")
    print(f"archives {len(manifest.get('entries', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

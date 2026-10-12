"""One cutover from the legacy cleartext vault, and the backup rewrite.

The legacy reader is not a runtime path. This module is the only code that
opens a ``.credential-vault.json`` or a raw browser key, and only long enough
to seal, compare, and then delete. A mismatch deletes nothing.

An extracted archive is sealed into its own key database. That rewrite never
calls the live service's ``replace_center``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from tinyassets.broker.key_service import (
    KeyService,
    KeyServiceUnavailable,
    account_id,
    for_data_root,
    open_blob,
    seal_blob,
)
from tinyassets.broker.root_key import ReconnectRequired, RootKeyError
from tinyassets.vault_envelope import ENVELOPE_FORMAT, EnvelopeError, gcm_open

VAULT_NAME = ".credential-vault.json"
LEGACY_KEY_SUFFIX = ".key"
_DONE = {"replaced", "canary", "kept"}


class CutoverStopped(RuntimeError):
    """The sweep stopped. Originals that were not yet replaced are intact."""

    def __init__(self, message: str, *, manifest: dict[str, Any]) -> None:
        super().__init__(message)
        self.manifest = manifest


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("credentials", [])
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RootKeyError("legacy credential vault is not a record list")
    return payload


def _failure(path: str, count: int, reason: str) -> dict[str, Any]:
    return {"path": path, "count": count, "reason": reason}


def migrate_data_root(
    data_root: Path,
    *,
    owner_for_center,
    service: KeyService | None = None,
) -> dict[str, Any]:
    """Seal every legacy vault. Delete the files only after every compare passes.

    ``owner_for_center(center_dir) -> owner`` supplies the account. A mismatch
    raises and leaves every legacy file in place. Tombstoned accounts are
    removed rather than sealed.
    """
    root = Path(data_root)
    service = service or for_data_root(root)
    try:
        probe = service.probe()
    except KeyServiceUnavailable as exc:
        raise CutoverStopped("key service", manifest={"phase": "probe"}) from exc
    if probe.get("status") != "ok":
        raise CutoverStopped("key service", manifest={"phase": "probe", "probe": probe})
    tombstoned = service.tombstoned_accounts()
    files = sorted(path for path in root.rglob(VAULT_NAME) if not _outside(root, path))
    staged: list[tuple[Path, str, str, list[dict[str, Any]]]] = []
    stripped: list[Path] = []
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise CutoverStopped(
                "legacy vault is not a regular file; nothing removed",
                manifest={"phase": "read", "path": path.name},
            )
        center_dir = path.parent
        center = center_dir.name
        owner = owner_for_center(center_dir)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records = _records(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CutoverStopped(
                f"legacy vault in {center} did not parse; nothing removed",
                manifest={"phase": "read", "command_center": center},
            ) from exc
        if account_id(owner) in tombstoned:
            stripped.append(path)
            continue
        staged.append((path, center, owner, records))
    for path, center, owner, records in staged:
        before = service.load_center(center)
        try:
            service.replace_center(owner=owner, center=center, records=records)
            opened = service.load_center(center)
        except (RootKeyError, ReconnectRequired, KeyServiceUnavailable) as exc:
            raise CutoverStopped(
                f"legacy vault in {center} did not round-trip; nothing removed",
                manifest={"phase": "seal", "command_center": center},
            ) from exc
        if _canonical_set(opened) != _canonical_set(records):
            service.replace_center(owner=owner, center=center, records=before)
            raise CutoverStopped(
                f"legacy vault in {center} did not round-trip; nothing removed",
                manifest={"phase": "compare", "command_center": center},
            )
    for path, _center, _owner, _records_ in staged:
        path.unlink()
    for path in stripped:
        path.unlink()
    return {
        "migrated": [str(path) for path, *_rest in staged],
        "removed": len(staged) + len(stripped),
        "stripped": len(stripped),
    }


def _canonical_set(records: list[dict[str, Any]]) -> list[str]:
    return sorted(
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for record in records
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_members(names: list[str]) -> bool:
    for name in names:
        base = name.rsplit("/", 1)[-1]
        if base == VAULT_NAME:
            return True
        if "/browser-vault/" in f"/{name}" and base.endswith(LEGACY_KEY_SUFFIX):
            return True
    return False


def _scan_tree(root: Path, samples: list[bytes]) -> list[str]:
    """Return problem names. Never include sample bytes in the result."""
    problems: list[str] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        base = path.name
        if base == VAULT_NAME or (base.endswith(LEGACY_KEY_SUFFIX) and "browser-vault" in relative):
            problems.append(relative)
            continue
        if samples:
            blob = path.read_bytes()
            if any(sample and sample in blob for sample in samples):
                problems.append(relative)
    return problems


def _outside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return True
    return False


def _unsafe_member(member: tarfile.TarInfo) -> str | None:
    name = member.name
    pure = PurePosixPath(name)
    if not name or "\x00" in name or "\\" in name or pure.is_absolute() or ".." in pure.parts:
        return "archive path escapes the extract"
    if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
        return "archive member is not a regular file"
    return None


def _safe_extract(handle: tarfile.TarFile, dest: Path) -> None:
    """Write regular files under ``dest``. Refuse links and paths that escape."""
    root = dest.resolve()
    for member in handle.getmembers():
        reason = _unsafe_member(member)
        if reason:
            raise CutoverStopped(reason, manifest={"phase": "extract"})
        target = (root / member.name).resolve()
        if not target.is_relative_to(root):
            raise CutoverStopped(
                "archive path escapes the extract", manifest={"phase": "extract"}
            )
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = handle.extractfile(member)
        if source is None:
            raise CutoverStopped(
                "archive member is not a regular file", manifest={"phase": "extract"}
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags, 0o600)
        except OSError as exc:
            raise CutoverStopped(
                "archive path escapes the extract", manifest={"phase": "extract"}
            ) from exc
        with os.fdopen(descriptor, "wb") as out, source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)


def rewrite_backup_directory(
    directory: Path,
    *,
    reseal,
    samples: list[bytes] | None = None,
    manifest_path: Path | None = None,
    preserve: set[str] | None = None,
) -> dict[str, Any]:
    """Rewrite legacy archives in ``directory`` in the D8 order.

    ``reseal(extracted_root) -> report`` seals vault files inside one extracted
    archive and deletes legacy names there. The report's ``failed`` list stops
    the sweep before the original tar is replaced. The newest legacy archive is
    the canary: its original is kept and the manifest marks it done. Names in
    ``preserve`` are resealed and kept. A failure stops the sweep; re-running
    resumes from the manifest.
    """
    root = Path(directory)
    samples = samples or []
    preserve = preserve or set()
    manifest_path = manifest_path or (root / "rewrite-manifest.json")
    manifest: dict[str, Any] = {"entries": [], "stopped_at": None, "failed_count": 0}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    done = {
        entry["old_name"]
        for entry in manifest.get("entries", [])
        if entry.get("status") in _DONE or entry.get("done") is True
    }
    archives = sorted(
        [path for path in root.iterdir() if path.is_file() and path.name.endswith(".tar.gz")],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    legacy = []
    inventory = []
    for path in archives:
        digest = _sha256(path)
        with tarfile.open(path, "r:gz") as handle:
            names = handle.getnames()
        is_legacy = _legacy_members(names)
        inventory.append({"name": path.name, "sha256": digest, "legacy": is_legacy})
        if is_legacy and path.name not in done:
            legacy.append(path)
    manifest["inventory"] = inventory
    _write_manifest(manifest_path, manifest)
    if not legacy:
        manifest["stopped_at"] = None
        _write_manifest(manifest_path, manifest)
        return manifest

    def _stop(path: Path, message: str, failed: list[dict[str, Any]]) -> None:
        count = sum(int(item.get("count") or 1) for item in failed) or len(failed)
        manifest["failed"] = failed
        manifest["failed_count"] = count
        manifest["stopped_at"] = path.name
        _write_manifest(manifest_path, manifest)
        raise CutoverStopped(message, manifest=manifest)

    def _rewrite(path: Path, *, status: str) -> None:
        old_sha = _sha256(path)
        new_name = path.name.replace(".tar.gz", ".sealed.tar.gz")
        new_path = path.with_name(new_name)
        extract = root / f".rewrite-{path.stem}"
        if extract.exists():
            _remove_tree(extract)
        extract.mkdir()
        try:
            with tarfile.open(path, "r:gz") as handle:
                _safe_extract(handle, extract)
            report = reseal(extract) or {}
            failed = list(report.get("failed") or [])
            if failed:
                _stop(
                    path,
                    f"{sum(int(item.get('count') or 1) for item in failed)} record(s) "
                    "did not re-seal; original archive kept",
                    failed,
                )
            problems = _scan_tree(extract, samples)
            if problems:
                _stop(
                    path,
                    "rewritten archive still holds a legacy credential",
                    [_failure(name, 1, "legacy") for name in problems],
                )
            if new_path.exists():
                new_path.unlink()
            with tarfile.open(new_path, "w:gz") as handle:
                for child in sorted(extract.rglob("*")):
                    handle.add(
                        child, arcname=child.relative_to(extract).as_posix(), recursive=False
                    )
            new_sha = _sha256(new_path)
            with tarfile.open(new_path, "r:gz") as handle:
                names = handle.getnames()
            if _legacy_members(names):
                new_path.unlink()
                _stop(
                    path,
                    "rewritten archive still holds a legacy credential",
                    [_failure(new_name, 1, "legacy")],
                )
            entry = {
                "old_name": path.name,
                "old_sha256": old_sha,
                "new_name": new_name,
                "new_sha256": new_sha,
                "status": status,
                "done": True,
                "sealed": report.get("sealed", 0),
                "stripped": report.get("stripped", 0),
                "failed_count": 0,
            }
            if status == "replaced":
                path.unlink()
            manifest.setdefault("entries", []).append(entry)
            manifest["stopped_at"] = None
            manifest["failed_count"] = 0
            _write_manifest(manifest_path, manifest)
        except CutoverStopped:
            manifest["stopped_at"] = path.name
            _write_manifest(manifest_path, manifest)
            raise
        finally:
            _remove_tree(extract)

    canary = legacy[0]
    try:
        _rewrite(canary, status="canary")
    except CutoverStopped:
        raise
    for path in legacy[1:]:
        status = "kept" if path.name in preserve else "replaced"
        try:
            _rewrite(path, status=status)
        except CutoverStopped:
            raise
    return manifest


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            child.rmdir()
    path.rmdir()


def assert_backup_tree_clean(root: Path) -> None:
    """Refuse a backup source that still holds a legacy vault or a raw browser key."""
    problems = _scan_tree(root, [])
    if problems:
        raise SystemExit("backup refused: legacy credential material is still in the data volume")


def legacy_browser_key_name(principal: str) -> str:
    return hashlib.sha256(principal.encode()).hexdigest() + LEGACY_KEY_SUFFIX


def read_legacy_browser_key(principal: str, roots: list[Path]) -> bytes | None:
    """Return the one 32-byte key file for ``principal``.

    ``None`` means no root has it. A symlink, a short file, or two different
    keys stops the cutover.
    """
    name = legacy_browser_key_name(principal)
    found: list[bytes] = []
    for root in roots:
        root = Path(root)
        path = root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file() or _outside(root, path):
            raise CutoverStopped(
                "legacy browser key is not a regular file",
                manifest={"phase": "browser-key"},
            )
        blob = path.read_bytes()
        if len(blob) != 32:
            raise CutoverStopped(
                "legacy browser key is not a regular file",
                manifest={"phase": "browser-key"},
            )
        found.append(blob)
    if any(blob != found[0] for blob in found):
        raise CutoverStopped(
            "legacy browser keys disagree",
            manifest={"phase": "browser-key"},
        )
    return found[0] if found else None


def delete_legacy_browser_keys(roots: list[Path]) -> int:
    """Unlink regular ``*.key`` files directly inside each root."""
    removed = 0
    for root in roots:
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            continue
        for path in list(root.iterdir()):
            if path.is_symlink() or not path.is_file() or _outside(root, path):
                continue
            if not path.name.endswith(LEGACY_KEY_SUFFIX):
                continue
            path.unlink()
            removed += 1
    return removed


def _legacy_browser_aad(principal: str, center: str, ident: str, revision: int) -> bytes:
    return json.dumps([1, principal, center, ident, revision]).encode()


def _open_legacy_browser(
    key: bytes, blob: bytes, principal: str, center: str, ident: str, revision: int
) -> dict[str, Any]:
    raw = gcm_open(key, blob, _legacy_browser_aad(principal, center, ident, revision))
    state = json.loads(raw.decode("utf-8"))
    if not isinstance(state, dict):
        raise EnvelopeError("credential vault authentication failed")
    return state


def _is_new_envelope(blob: bytes) -> bool:
    try:
        envelope = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(envelope, dict) and envelope.get("format") == ENVELOPE_FORMAT


def _browser_rows(ledger: Path) -> list[sqlite3.Row] | None:
    with sqlite3.connect(ledger) as conn:
        conn.row_factory = sqlite3.Row
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'browser_vault'"
        ).fetchone()
        if present is None:
            return None
        return list(
            conn.execute("SELECT id, owner, home, revision, sealed FROM browser_vault")
        )


def _write_browser_rows(
    ledger: Path, updates: list[tuple[bytes, str]], strips: list[str]
) -> None:
    conn = sqlite3.connect(ledger, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for blob, ident in updates:
            conn.execute("UPDATE browser_vault SET sealed = ? WHERE id = ?", (blob, ident))
        for ident in strips:
            conn.execute("UPDATE browser_vault SET sealed = ? WHERE id = ?", (b"", ident))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def migrate_browser_sessions(
    data_root: Path,
    *,
    service: KeyService,
    key_roots: list[Path],
    tombstoned: set[str] | None = None,
    delete_keys: bool = False,
) -> dict[str, Any]:
    """Reseal legacy browser ciphertext under ``service``.

    Key files are removed only when ``delete_keys`` is set and every sealed row
    verified. A failure writes nothing and deletes nothing.
    """
    ledger = Path(data_root) / ".broker" / "outbound.db"
    report: dict[str, Any] = {
        "migrated": 0,
        "already": 0,
        "stripped": 0,
        "failed": [],
        "skipped_empty": 0,
    }
    if not ledger.exists():
        return report
    if ledger.is_symlink() or not ledger.is_file():
        report["failed"].append(_failure("outbound.db", 1, "browser-ledger"))
        return report
    try:
        rows = _browser_rows(ledger)
    except sqlite3.Error:
        report["failed"].append(_failure("outbound.db", 1, "browser-ledger"))
        return report
    if rows is None:
        return report
    tombstoned = set(tombstoned or ())
    updates: list[tuple[bytes, str]] = []
    strips: list[str] = []
    for row in rows:
        ident = str(row["id"])
        sealed = bytes(row["sealed"] or b"")
        label = f"browser:{ident}"
        if not sealed:
            report["skipped_empty"] += 1
            continue
        owner = str(row["owner"])
        center = str(row["home"])
        revision = row["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            report["failed"].append(_failure(label, 1, "browser-open"))
            continue
        if account_id(owner) in tombstoned:
            strips.append(ident)
            continue
        if _is_new_envelope(sealed):
            try:
                opened = open_blob(
                    service,
                    owner=owner,
                    center=center,
                    blob=sealed,
                    generation=revision,
                    record_id=ident,
                )
                json.loads(opened.decode("utf-8"))
            except (ReconnectRequired, EnvelopeError, UnicodeDecodeError, json.JSONDecodeError):
                report["failed"].append(_failure(label, 1, "browser-open"))
                continue
            report["already"] += 1
            continue
        try:
            key = read_legacy_browser_key(owner, key_roots)
        except CutoverStopped:
            report["failed"].append(_failure(label, 1, "browser-key"))
            continue
        if key is None:
            report["failed"].append(_failure(label, 1, "browser-key"))
            continue
        try:
            state = _open_legacy_browser(key, sealed, owner, center, ident, revision)
            blob = seal_blob(
                service,
                owner=owner,
                center=center,
                record_id=ident,
                generation=revision,
                plaintext=json.dumps(state).encode(),
            )
            opened = json.loads(
                open_blob(
                    service,
                    owner=owner,
                    center=center,
                    blob=blob,
                    generation=revision,
                    record_id=ident,
                ).decode("utf-8")
            )
        except (
            EnvelopeError,
            ReconnectRequired,
            RootKeyError,
            KeyServiceUnavailable,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            report["failed"].append(_failure(label, 1, "browser-open"))
            continue
        if opened != state:
            report["failed"].append(_failure(label, 1, "browser-compare"))
            continue
        updates.append((blob, ident))
    if report["failed"]:
        return report
    if updates or strips:
        _write_browser_rows(ledger, updates, strips)
    if delete_keys:
        delete_legacy_browser_keys(key_roots)
    report["migrated"] = len(updates)
    report["stripped"] = len(strips)
    return report


def reseal_extracted_archive(
    extracted: Path,
    *,
    provider,
    owner_for_center,
    tombstoned: set[str] | None = None,
    key_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """Seal one extracted backup into a key database under that extract.

    ``provider`` is the same root the live broker uses. The sqlite file is
    ``<data-root>/.broker/vault-keys.db`` inside the extract, never the live
    store. Records that do not re-seal stay in the extract and are counted.
    """
    extracted = Path(extracted)
    tombstoned = set(tombstoned or ())
    external = [Path(path) for path in (key_roots or [])]
    sealed = 0
    stripped = 0
    failed: list[dict[str, Any]] = []
    files = sorted(
        path
        for path in extracted.rglob(VAULT_NAME)
        if not path.is_symlink() and path.is_file() and not _outside(extracted, path)
    )
    by_root: dict[Path, list[Path]] = {}
    for path in files:
        data_root = path.parent.parent
        if _outside(extracted, data_root):
            failed.append(_failure(path.name, 1, "path"))
            continue
        by_root.setdefault(data_root, []).append(path)
    for path in extracted.rglob(VAULT_NAME):
        if path.is_symlink():
            failed.append(_failure(path.name, 1, "link"))
    for data_root, paths in by_root.items():
        service = KeyService(data_root, provider)
        for path in paths:
            relative = path.relative_to(extracted).as_posix()
            center = path.parent.name
            try:
                owner = owner_for_center(path.parent)
                records = _records(json.loads(path.read_text(encoding="utf-8")))
            except CutoverStopped:
                raise
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, RootKeyError):
                failed.append(_failure(relative, 1, "parse"))
                continue
            if account_id(owner) in tombstoned:
                path.unlink()
                stripped += len(records)
                continue
            try:
                service.replace_center(owner=owner, center=center, records=records)
                opened = service.load_center(center)
            except (ReconnectRequired, RootKeyError, KeyServiceUnavailable):
                failed.append(_failure(relative, len(records) or 1, "seal"))
                continue
            if _canonical_set(opened) != _canonical_set(records):
                failed.append(_failure(relative, len(records) or 1, "compare"))
                continue
            path.unlink()
            sealed += len(records)
        if failed:
            continue
        local_roots = [data_root / ".broker" / "browser-vault"]
        browser = migrate_browser_sessions(
            data_root,
            service=service,
            key_roots=[*local_roots, *external],
            tombstoned=tombstoned,
            delete_keys=False,
        )
        failed.extend(browser["failed"])
        if not failed:
            delete_legacy_browser_keys(local_roots)
    return {"sealed": sealed, "stripped": stripped, "failed": failed}


def owner_for_center_dir(center_dir: Path) -> str:
    """The single grant owner of this center, or ``unowned:<center>``.

    Two owners stop the cutover. A missing ledger is an unowned center.
    """
    center = center_dir.name
    ledger = center_dir.parent / ".broker" / "outbound.db"
    if not ledger.is_file() or ledger.is_symlink():
        return f"unowned:{center}"
    try:
        with sqlite3.connect(f"file:{ledger}?mode=ro", uri=True) as conn:
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND "
                "name = 'outbound_connection_grants'"
            ).fetchone()
            if present is None:
                return f"unowned:{center}"
            rows = conn.execute(
                "SELECT DISTINCT owner_user_id FROM outbound_connection_grants "
                "WHERE universe_id = ?",
                (center,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise CutoverStopped(
            f"{center} owner lookup failed",
            manifest={"phase": "owner", "command_center": center},
        ) from exc
    owners = sorted({row[0] for row in rows if row[0]})
    if len(owners) > 1:
        raise CutoverStopped(
            f"{center} has more than one owner",
            manifest={"phase": "owner", "command_center": center},
        )
    if not owners:
        return f"unowned:{center}"
    return owners[0]

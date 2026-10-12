"""One cutover from the legacy cleartext vault, and the backup rewrite.

The legacy reader is not a runtime path. This module is the only code that
opens a ``.credential-vault.json`` or a raw browser key, and only long enough
to seal, compare, and then delete. A mismatch deletes nothing.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path
from typing import Any

from tinyassets.broker.key_service import KeyService, KeyServiceUnavailable, for_data_root
from tinyassets.broker.root_key import ReconnectRequired, RootKeyError

VAULT_NAME = ".credential-vault.json"
LEGACY_KEY_SUFFIX = ".key"


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


def migrate_data_root(
    data_root: Path,
    *,
    owner_for_center,
    service: KeyService | None = None,
) -> dict[str, Any]:
    """Seal every legacy vault. Delete the files only after every compare passes.

    ``owner_for_center(center_dir) -> owner`` supplies the account. A mismatch
    raises and leaves every legacy file in place.
    """
    root = Path(data_root)
    service = service or for_data_root(root)
    try:
        probe = service.probe()
    except KeyServiceUnavailable as exc:
        raise CutoverStopped("key service", manifest={"phase": "probe"}) from exc
    if probe.get("status") != "ok":
        raise CutoverStopped("key service", manifest={"phase": "probe", "probe": probe})
    files = sorted(root.rglob(VAULT_NAME))
    staged: list[tuple[Path, str, str, list[dict[str, Any]]]] = []
    for path in files:
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
            # Put back whatever this center had, then stop. No legacy file is removed.
            service.replace_center(owner=owner, center=center, records=before)
            raise CutoverStopped(
                f"legacy vault in {center} did not round-trip; nothing removed",
                manifest={"phase": "compare", "command_center": center},
            )
    for path, _center, _owner, _records_ in staged:
        path.unlink()
    return {"migrated": [str(path) for path, *_rest in staged], "removed": len(staged)}


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
        if not path.is_file():
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


def rewrite_backup_directory(
    directory: Path,
    *,
    reseal,
    samples: list[bytes] | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Rewrite legacy archives in ``directory`` in the D8 order.

    ``reseal(extracted_root) -> None`` seals vault files inside one extracted
    archive and deletes legacy names. The newest legacy archive is the canary
    and its original is kept until the canary restore proof returns. Further
    archives are deleted only after their rewrite verifies. A failure stops
    the sweep; re-running resumes from the manifest.
    """
    root = Path(directory)
    samples = samples or []
    manifest_path = manifest_path or (root / "rewrite-manifest.json")
    manifest: dict[str, Any] = {"entries": [], "stopped_at": None}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    done = {
        entry["old_name"]
        for entry in manifest.get("entries", [])
        if entry.get("status") == "replaced"
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

    def _rewrite(path: Path, *, delete_original: bool) -> None:
        old_sha = _sha256(path)
        new_name = path.name.replace(".tar.gz", ".sealed.tar.gz")
        new_path = path.with_name(new_name)
        extract = root / f".rewrite-{path.stem}"
        if extract.exists():
            _remove_tree(extract)
        extract.mkdir()
        try:
            with tarfile.open(path, "r:gz") as handle:
                handle.extractall(extract)
            reseal(extract)
            problems = _scan_tree(extract, samples)
            if problems:
                raise CutoverStopped(
                    "rewritten archive still holds a legacy credential",
                    manifest=manifest,
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
                raise CutoverStopped(
                    "rewritten archive still holds a legacy credential",
                    manifest=manifest,
                )
            entry = {
                "old_name": path.name,
                "old_sha256": old_sha,
                "new_name": new_name,
                "new_sha256": new_sha,
                "status": "rewritten" if not delete_original else "replaced",
            }
            if delete_original:
                path.unlink()
                entry["status"] = "replaced"
            else:
                entry["status"] = "canary"
            manifest.setdefault("entries", []).append(entry)
            manifest["stopped_at"] = None
            _write_manifest(manifest_path, manifest)
        except CutoverStopped:
            manifest["stopped_at"] = path.name
            _write_manifest(manifest_path, manifest)
            raise
        finally:
            _remove_tree(extract)

    canary = legacy[0]
    try:
        _rewrite(canary, delete_original=False)
    except CutoverStopped:
        raise
    # The canary original stays until the caller proves a restore. The rest of
    # the sweep deletes each original only after that archive verifies.
    for path in legacy[1:]:
        try:
            _rewrite(path, delete_original=True)
        except CutoverStopped:
            raise
    return manifest


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            child.rmdir()
    path.rmdir()


def assert_backup_tree_clean(root: Path) -> None:
    """Refuse a backup source that still holds a legacy vault or a raw browser key."""
    problems = _scan_tree(root, [])
    if problems:
        raise SystemExit("backup refused: legacy credential material is still in the data volume")


def reseal_extracted_archive(extracted: Path, *, service: KeyService, owner_for_center) -> None:
    """Seal legacy vault files inside one extracted backup. Erased accounts are stripped."""
    for path in list(extracted.rglob(VAULT_NAME)):
        center = path.parent.name
        owner = owner_for_center(path.parent)
        try:
            records = _records(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RootKeyError):
            path.unlink()
            continue
        kept = []
        for record in records:
            try:
                service.replace_center(owner=owner, center=center, records=[record])
                opened = service.load_center(center)
            except (ReconnectRequired, RootKeyError, KeyServiceUnavailable):
                continue
            if record not in opened and _canonical_set([record]) != _canonical_set(
                [
                    item
                    for item in opened
                    if item.get("credential_type") == record.get("credential_type")
                ]
            ):
                continue
            kept.append(record)
        if kept:
            service.replace_center(owner=owner, center=center, records=kept)
        path.unlink()
    for path in list(extracted.rglob("*")):
        if (
            path.is_file()
            and path.name.endswith(LEGACY_KEY_SUFFIX)
            and "browser-vault" in path.as_posix()
        ):
            path.unlink()
    # Sealed records for a rewritten archive live in the caller's key service.

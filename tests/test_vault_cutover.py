"""Cutover entrypoint, isolated archive reseal, and browser-session migration."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scripts.vault_cutover import main as cutover_main
from scripts.vault_dr_open import main as dr_main
from tinyassets.broker.key_service import (
    KeyService,
    KeyServiceUnavailable,
    broker_service,
    open_blob,
    reset_for_tests,
)
from tinyassets.broker.root_key import MemoryRootKey, ReconnectRequired, kms_http_failure
from tinyassets.broker.vault_cutover import (
    CutoverStopped,
    migrate_browser_sessions,
    reseal_extracted_archive,
    rewrite_backup_directory,
)


@pytest.fixture(autouse=True)
def _fresh_service():
    reset_for_tests()
    yield
    reset_for_tests()


def _record(token: str) -> dict:
    return {
        "credential_type": "http",
        "destination": "fixture",
        "service": "fixture",
        "token": token,
    }


def _tar(path: Path, root: Path) -> None:
    with tarfile.open(path, "w:gz") as handle:
        for child in sorted(root.rglob("*")):
            handle.add(child, arcname=child.relative_to(root).as_posix(), recursive=False)


def _sidecar(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def _legacy_browser(data_root: Path, *, key: bytes, state: dict, file_key: bytes | None = None):
    principal, center, ident, revision = "alice", "home", "ab" * 16, 2
    aad = json.dumps([1, principal, center, ident, revision]).encode()
    nonce = b"0123456789ab"
    blob = nonce + AESGCM(key).encrypt(nonce, json.dumps(state).encode(), aad)
    broker = data_root / ".broker"
    key_dir = broker / "browser-vault"
    key_dir.mkdir(parents=True, exist_ok=True)
    (key_dir / (hashlib.sha256(principal.encode()).hexdigest() + ".key")).write_bytes(
        key if file_key is None else file_key
    )
    with sqlite3.connect(broker / "outbound.db") as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS browser_vault ("
            "id TEXT PRIMARY KEY, owner TEXT NOT NULL, home TEXT NOT NULL, "
            "url TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, "
            "sealed BLOB NOT NULL, account TEXT NOT NULL, verify TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO browser_vault VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ident,
                principal,
                center,
                "https://example.com",
                revision,
                "connected",
                blob,
                "",
                "{}",
            ),
        )
    return ident, blob


def _sealed(data_root: Path, ident: str) -> bytes:
    with sqlite3.connect(data_root / ".broker" / "outbound.db") as conn:
        row = conn.execute("SELECT sealed FROM browser_vault WHERE id = ?", (ident,)).fetchone()
    return bytes(row[0])


def test_archive_reseal_does_not_write_the_live_store(tmp_path):
    provider = MemoryRootKey()
    live = KeyService(tmp_path / "live", provider)
    live.replace_center(owner="alice", center="home", records=[_record("live-token")])
    extract = tmp_path / "extract"
    center = extract / "home"
    center.mkdir(parents=True)
    (center / ".credential-vault.json").write_text(
        json.dumps({"credentials": [_record("archived-token")]}),
        encoding="utf-8",
    )
    report = reseal_extracted_archive(
        extract, provider=provider, owner_for_center=lambda _path: "alice"
    )
    assert report["failed"] == []
    assert report["sealed"] == 1
    assert not (center / ".credential-vault.json").exists()
    assert live.load_center("home") == [_record("live-token")]
    assert KeyService(extract, provider).load_center("home") == [_record("archived-token")]


def test_extract_refuses_a_path_that_escapes(tmp_path):
    archive_dir = tmp_path / "backups"
    archive_dir.mkdir()
    archive = archive_dir / "old.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        escaped = tarfile.TarInfo("../escape.txt")
        payload = b"nope"
        escaped.size = len(payload)
        handle.addfile(escaped, io.BytesIO(payload))
        vault = tarfile.TarInfo("home/.credential-vault.json")
        body = b"{}"
        vault.size = len(body)
        handle.addfile(vault, io.BytesIO(body))
    with pytest.raises(CutoverStopped, match="escapes"):
        rewrite_backup_directory(archive_dir, reseal=lambda _root: {"failed": []})
    assert not (tmp_path / "escape.txt").exists()
    assert archive.is_file()
    source = Path(__file__).resolve().parents[1] / "tinyassets/broker/vault_cutover.py"
    assert "extractall" not in source.read_text(encoding="utf-8")


def test_canary_is_done_and_a_second_rewrite_keeps_the_original(tmp_path):
    archive_dir = tmp_path / "backups"
    source = tmp_path / "src" / "home"
    source.mkdir(parents=True)
    (source / ".credential-vault.json").write_text("[]", encoding="utf-8")
    archive_dir.mkdir()
    archive = archive_dir / "old.tar.gz"
    _tar(archive, tmp_path / "src")

    def reseal(root: Path) -> dict:
        for path in root.rglob(".credential-vault.json"):
            path.unlink()
        return {"sealed": 1, "stripped": 0, "failed": []}

    first = rewrite_backup_directory(archive_dir, reseal=reseal)
    assert first["entries"][0]["status"] == "canary"
    assert first["entries"][0]["done"] is True
    original = hashlib.sha256(archive.read_bytes()).hexdigest()
    sealed = hashlib.sha256((archive_dir / "old.sealed.tar.gz").read_bytes()).hexdigest()
    second = rewrite_backup_directory(archive_dir, reseal=reseal)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == original
    assert hashlib.sha256((archive_dir / "old.sealed.tar.gz").read_bytes()).hexdigest() == sealed
    assert second["entries"] == first["entries"]


def test_a_record_that_fails_to_reseal_is_counted_and_the_original_stays(tmp_path):
    archive_dir = tmp_path / "backups"
    source = tmp_path / "src" / "home"
    source.mkdir(parents=True)
    (source / ".credential-vault.json").write_text("not-json", encoding="utf-8")
    archive_dir.mkdir()
    archive = archive_dir / "old.tar.gz"
    _tar(archive, tmp_path / "src")
    provider = MemoryRootKey()

    def reseal(root: Path) -> dict:
        return reseal_extracted_archive(
            root, provider=provider, owner_for_center=lambda _path: "alice"
        )

    with pytest.raises(CutoverStopped, match="did not re-seal"):
        rewrite_backup_directory(archive_dir, reseal=reseal)
    assert archive.is_file()
    assert not (archive_dir / "old.sealed.tar.gz").exists()
    manifest = json.loads((archive_dir / "rewrite-manifest.json").read_text(encoding="utf-8"))
    assert manifest["failed_count"] == 1
    assert manifest["failed"][0]["reason"] == "parse"


def test_browser_session_round_trips_and_the_key_is_kept_until_it_does(tmp_path):
    service = KeyService(tmp_path, MemoryRootKey())
    key = AESGCM.generate_key(bit_length=256)
    state = {"cookie": "keep-me"}
    ident, _blob = _legacy_browser(tmp_path, key=key, state=state)
    report = migrate_browser_sessions(
        tmp_path,
        service=service,
        key_roots=[tmp_path / ".broker" / "browser-vault"],
        delete_keys=True,
    )
    assert report["failed"] == []
    assert report["migrated"] == 1
    assert not list((tmp_path / ".broker" / "browser-vault").glob("*.key"))
    opened = open_blob(
        service,
        owner="alice",
        center="home",
        blob=_sealed(tmp_path, ident),
        generation=2,
        record_id=ident,
    )
    assert json.loads(opened) == state

    other = tmp_path / "other"
    other_service = KeyService(other, MemoryRootKey())
    bad = AESGCM.generate_key(bit_length=256)
    ident, before = _legacy_browser(other, key=key, state=state, file_key=bad)
    failed = migrate_browser_sessions(
        other,
        service=other_service,
        key_roots=[other / ".broker" / "browser-vault"],
        delete_keys=True,
    )
    assert failed["migrated"] == 0
    assert failed["failed"][0]["count"] == 1
    assert _sealed(other, ident) == before
    assert list((other / ".broker" / "browser-vault").glob("*.key"))


def _broker(monkeypatch):
    monkeypatch.setenv("TINYASSETS_BROKER_PROCESS", "1")
    monkeypatch.delenv("TINYASSETS_ENV", raising=False)


def _vault(data: Path, token: str) -> None:
    center = data / "home"
    center.mkdir(parents=True, exist_ok=True)
    (center / ".credential-vault.json").write_text(
        json.dumps({"credentials": [_record(token)]}),
        encoding="utf-8",
    )


def test_dry_run_refuses_without_a_backup_or_a_green_probe(tmp_path, monkeypatch, capsys):
    _broker(monkeypatch)
    data = tmp_path / "data"
    _vault(data, "live-token")
    archives = tmp_path / "backups"
    archives.mkdir()
    code = cutover_main(
        [
            "--data-root",
            str(data),
            "--archive-dir",
            str(archives),
            "--legacy-backup",
            str(tmp_path / "missing.tar.gz"),
            "--dry-run",
        ]
    )
    assert code == 1
    assert "verified legacy backup is missing" in capsys.readouterr().err
    assert (data / "home" / ".credential-vault.json").is_file()

    newest = archives / "newest.tar.gz"
    _tar(newest, data)
    _sidecar(newest)
    os.utime(newest, (2_000, 2_000))

    def broken(self, plaintext, context):
        raise KeyServiceUnavailable("kms unreachable")

    monkeypatch.setattr(MemoryRootKey, "wrap", broken)
    code = cutover_main(
        [
            "--data-root",
            str(data),
            "--archive-dir",
            str(archives),
            "--legacy-backup",
            str(newest),
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "not green" in captured.err
    assert "dry-run: nothing removed" not in captured.out
    assert (data / "home" / ".credential-vault.json").is_file()
    assert not (archives / "newest.sealed.tar.gz").exists()


def test_production_cutover_refuses_the_broker_env_unless_uid_1002(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TINYASSETS_ENV", "production")
    monkeypatch.setenv("TINYASSETS_BROKER_PROCESS", "1")
    code = cutover_main(
        ["--data-root", str(tmp_path), "--legacy-backup", str(tmp_path / "missing.tar.gz")]
    )
    assert code == 1
    assert "uid 1002" in capsys.readouterr().err


def test_cutover_migrates_sessions_and_leaves_the_live_store_alone(tmp_path, monkeypatch, capsys):
    _broker(monkeypatch)
    data = tmp_path / "data"
    _vault(data, "live-token")
    key = AESGCM.generate_key(bit_length=256)
    ident, _blob = _legacy_browser(data, key=key, state={"cookie": "live"})
    archives = tmp_path / "backups"
    older_src = tmp_path / "older"
    _vault(older_src, "archived-token")
    archives.mkdir()
    older = archives / "older.tar.gz"
    _tar(older, older_src)
    os.utime(older, (1_000, 1_000))
    newest = archives / "newest.tar.gz"
    _tar(newest, data)
    os.utime(newest, (2_000, 2_000))
    _sidecar(newest)
    code = cutover_main(
        [
            "--data-root",
            str(data),
            "--archive-dir",
            str(archives),
            "--legacy-backup",
            str(newest),
            "--browser-key-root",
            str(data / ".broker" / "browser-vault"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert not (data / "home" / ".credential-vault.json").exists()
    service = broker_service(data)
    assert service.load_center("home") == [_record("live-token")]
    opened = open_blob(
        service,
        owner="alice",
        center="home",
        blob=_sealed(data, ident),
        generation=2,
        record_id=ident,
    )
    assert json.loads(opened) == {"cookie": "live"}
    assert not list((data / ".broker" / "browser-vault").glob("*.key"))
    assert newest.is_file()
    assert (archives / "newest.sealed.tar.gz").is_file()
    assert not older.is_file()
    assert (archives / "older.sealed.tar.gz").is_file()
    manifest = json.loads((archives / "rewrite-manifest.json").read_text(encoding="utf-8"))
    assert {"canary", "replaced"} <= {entry["status"] for entry in manifest["entries"]}
    assert all(entry["done"] is True for entry in manifest["entries"])


def test_missing_archive_directory_is_a_refusal(tmp_path, monkeypatch, capsys):
    _broker(monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    backup = tmp_path / "keep.tar.gz"
    source = tmp_path / "src"
    _vault(source, "live-token")
    _tar(backup, source)
    _sidecar(backup)
    code = cutover_main(
        [
            "--data-root",
            str(data),
            "--archive-dir",
            str(tmp_path / "absent"),
            "--legacy-backup",
            str(backup),
            "--dry-run",
        ]
    )
    assert code == 1
    assert "not scrubbed" in capsys.readouterr().err


def test_dr_browser_check_fails_when_it_cannot_run(tmp_path, capsys):
    service = broker_service(tmp_path)
    service.replace_center(owner="alice", center="home", records=[_record("tok")])
    code = dr_main(["--data-root", str(tmp_path), "--center", "home", "--owner", "alice"])
    captured = capsys.readouterr()
    assert code == 1
    assert "opened one restored browser session" not in captured.out
    assert "could not run" in captured.err

    broker = tmp_path / ".broker"
    broker.mkdir(exist_ok=True)
    ledger = broker / "outbound.db"
    ledger.write_bytes(b"")
    code = dr_main(["--data-root", str(tmp_path), "--center", "home", "--owner", "alice"])
    captured = capsys.readouterr()
    assert code == 1
    assert "opened one restored browser session" not in captured.out
    assert "could not run" in captured.err

    ledger.unlink()
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            "CREATE TABLE browser_vault ("
            "id TEXT PRIMARY KEY, owner TEXT, home TEXT, revision INTEGER, sealed BLOB)"
        )
        conn.execute(
            "INSERT INTO browser_vault VALUES (?,?,?,?,?)",
            ("ab" * 16, "alice", "home", 2, b"not-a-session"),
        )
    code = dr_main(["--data-root", str(tmp_path), "--center", "home", "--owner", "alice"])
    captured = capsys.readouterr()
    assert code == 1
    assert "opened one restored browser session" not in captured.out
    assert "did not open" in captured.err


def test_kms_access_denied_and_bad_ciphertext_are_reconnect():
    for detail in ("AccessDeniedException", "InvalidCiphertextException", "IncorrectKeyException"):
        failure = kms_http_failure(detail)
        assert isinstance(failure, ReconnectRequired)
        assert failure.reason == "reconnect_required"
    throttled = kms_http_failure("ThrottlingException")
    assert isinstance(throttled, KeyServiceUnavailable)
    assert not isinstance(throttled, ReconnectRequired)
    assert throttled.reason == "key_service_unavailable"

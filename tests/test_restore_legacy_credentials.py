"""Old-layout vaults convert to owner hash paths, and only those rows merge."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import tarfile
import tempfile
from pathlib import Path

import pytest

from tinyassets import restore_credentials as rc
from tinyassets.providers.definition import _definition_id

pytestmark = pytest.mark.verifies("owner-daemon-in-box:C3")

ROOT = Path(__file__).resolve().parents[1]
OWNER_A = "owner-a"
OWNER_B = "owner-b"
OWNER_D = "owner-d"
TOKEN_OK = "LEAK-TOKEN-ok-0123456789abcdef"
TOKEN_REVOKED = "LEAK-TOKEN-revoked-0123456789"
TOKEN_UNMATCHED = "LEAK-TOKEN-unmatched-01234567"
TOKEN_BETA = "LEAK-TOKEN-beta-ambiguous-0123"
TOKEN_GAMMA = "LEAK-TOKEN-gamma-zero-owner-01"
TOKEN_SUB = "LEAK-TOKEN-subscription-cli-01"
TOKEN_OTHER = "LEAK-TOKEN-other-type-0123456"
TOKEN_LIVE = "LEAK-TOKEN-live-must-stay-012"
DEST_OK = "api.example.com"
DEST_REVOKED = "revoked.example.com"
DEST_UNMATCHED = "other.example.com"
DEST_BETA = "beta.example.com"
DEST_GAMMA = "gamma.example.com"
ARCHIVE = "tinyassets-snapshot-2026-10-22T04-41-03Z.tar.gz"
SECRETS = (
    TOKEN_OK, TOKEN_REVOKED, TOKEN_UNMATCHED, TOKEN_BETA, TOKEN_GAMMA,
    TOKEN_SUB, TOKEN_OTHER, OWNER_A, OWNER_B, OWNER_D,
)

SCHEMA = """
CREATE TABLE outbound_connections (
    connection_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    revoked_at TEXT,
    credential_ref TEXT,
    label TEXT,
    granted_at TEXT
);
CREATE TABLE outbound_connection_grants (
    grant_id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    revoked_at TEXT,
    granted_at TEXT,
    unprompted_action_cap_json TEXT
);
CREATE TABLE connection_capabilities (
    connection_id TEXT NOT NULL,
    capability_kind TEXT NOT NULL,
    PRIMARY KEY (connection_id, capability_kind)
);
CREATE TABLE box_model_sources (
    owner TEXT NOT NULL,
    id TEXT NOT NULL,
    definition TEXT NOT NULL,
    PRIMARY KEY (owner, id)
);
"""


def _http(dest, token):
    return {
        "credential_type": "http", "service": dest, "destination": dest, "token": token,
    }


def _vault_bytes(records):
    return json.dumps({"schema_version": 1, "credentials": records}).encode()


def _definition():
    fields = {
        "universe_id": "alpha",
        "owner_user_id": OWNER_A,
        "access_method": "api_key_http",
        "protocol": "openai_chat",
        "ref": "ga-live",
        "model": "gpt-test",
    }
    return {
        "id": _definition_id(**fields),
        **fields,
        "visibility": "private",
        "created_at": "2026-10-22T04:41:03+00:00",
    }


def _sqlite_bytes(script, rows):
    fd, name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    path_fd = Path(name)
    try:
        with sqlite3.connect(path_fd) as db:
            db.executescript(script)
            for table, records in rows.items():
                for record in records:
                    names = list(record)
                    db.execute(
                        f"INSERT INTO {table} ({','.join(names)}) "
                        f"VALUES ({','.join('?' for _ in names)})",
                        [record[name] for name in names],
                    )
        return path_fd.read_bytes()
    finally:
        path_fd.unlink()


def _custody_bytes():
    return _sqlite_bytes(
        "CREATE TABLE llm_credential_deposit_owners "
        "(universe_id TEXT, owner_user_id TEXT)",
        {"llm_credential_deposit_owners": [
            {"universe_id": "alpha", "owner_user_id": OWNER_A},
            {"universe_id": "beta", "owner_user_id": OWNER_A},
            {"universe_id": "beta", "owner_user_id": OWNER_B},
            {"universe_id": "delta", "owner_user_id": OWNER_D},
        ]},
    )


def _conn(owner, connection_id, dest, revoked=None):
    return {
        "connection_id": connection_id,
        "owner_user_id": owner,
        "revoked_at": revoked,
        "credential_ref": f"vault://http/{dest}",
        "label": "ok",
    }


def _grant(owner, grant_id, connection_id, revoked=None):
    return {
        "grant_id": grant_id,
        "connection_id": connection_id,
        "owner_user_id": owner,
        "revoked_at": revoked,
        "granted_at": "2026-10-22T04:41:03+00:00",
        "unprompted_action_cap_json": None,
    }


def _ledger_bytes():
    definition = _definition()
    return _sqlite_bytes(SCHEMA, {
        "outbound_connections": [
            _conn(OWNER_A, "c-live", DEST_OK),
            _conn(OWNER_A, "c-revoked", DEST_REVOKED, revoked="2026-10-01"),
            _conn(OWNER_A, "c-orphan", "missing.example.com"),
            _conn(OWNER_B, "c-beta", DEST_BETA),
            _conn(OWNER_D, "c-delta", "delta.example.com"),
        ],
        "outbound_connection_grants": [
            _grant(OWNER_A, "ga-live", "c-live"),
            _grant(OWNER_A, "ga-revoked", "c-revoked", revoked="2026-10-01"),
            _grant(OWNER_A, "ga-orphan", "c-orphan"),
            _grant(OWNER_B, "gb-beta", "c-beta"),
            _grant(OWNER_D, "gd-delta", "c-delta"),
        ],
        "connection_capabilities": [
            {"connection_id": "c-live", "capability_kind": "model_use"},
            {"connection_id": "c-revoked", "capability_kind": "model_use"},
            {"connection_id": "c-orphan", "capability_kind": "model_use"},
            {"connection_id": "c-beta", "capability_kind": "model_use"},
            {"connection_id": "c-delta", "capability_kind": "model_use"},
        ],
        "box_model_sources": [{
            "owner": OWNER_A,
            "id": definition["id"],
            "definition": json.dumps(definition),
        }],
    })


def _members():
    return {
        "_data/.broker/outbound.db": _ledger_bytes(),
        "_data/.broker/credential-custody.db": _custody_bytes(),
        "_data/.platform/credential-vaults/alpha/.credential-vault.json": _vault_bytes([
            _http(DEST_OK, TOKEN_OK),
            _http(DEST_REVOKED, TOKEN_REVOKED),
            _http(DEST_UNMATCHED, TOKEN_UNMATCHED),
            {
                "credential_type": "llm_subscription",
                "service": "cli",
                "destination": "cli",
                "token": TOKEN_SUB,
            },
            {
                "credential_type": "social",
                "service": "x",
                "destination": "x",
                "token": TOKEN_OTHER,
            },
        ]),
        "_data/.platform/credential-vaults/beta/.credential-vault.json": _vault_bytes([
            _http(DEST_BETA, TOKEN_BETA),
        ]),
        "_data/gamma/.credential-vault.json": _vault_bytes([
            _http(DEST_GAMMA, TOKEN_GAMMA),
        ]),
        "_data/delta/.credential-vault.json": b"{",
    }


def _tar(path):
    with tarfile.open(path, "w:gz") as handle:
        for name, data in _members().items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
    return path


def _live(tmp):
    live = tmp / "live"
    ledger = live / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    with sqlite3.connect(ledger) as db:
        db.executescript(SCHEMA)
    (live / "alpha").mkdir()
    return live


def _ids(live):
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        rows = db.execute("SELECT connection_id FROM outbound_connections").fetchall()
    return {row[0] for row in rows}


def _assert_hidden(text):
    for secret in SECRETS:
        assert secret not in text
    for owner in (OWNER_A, OWNER_B, OWNER_D):
        assert hashlib.sha256(owner.encode()).hexdigest() not in text


def test_inspect_legacy_reports_counts_only(tmp_path, monkeypatch, capsys):
    snap = tmp_path / "snaps"
    snap.mkdir()
    _tar(snap / ARCHIVE)
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    assert rc.main([
        "--mode", "inspect-legacy", "--source", ARCHIVE,
        "--root", str(tmp_path / "live"),
        "--snapshot-dir", str(snap),
        "--backup-log", str(tmp_path / "missing.log"),
    ]) == 0
    out = capsys.readouterr().out
    _assert_hidden(out)
    report = json.loads(out)
    assert report["mode"] == "inspect-legacy"
    assert report["selected"] == ARCHIVE
    legacy = report["legacy"]
    assert legacy["platform_vault_files"] == 2
    assert legacy["center_vault_files"] == 2
    assert legacy["http"] == 5
    assert legacy["llm_subscription"] == 1
    assert legacy["other"] == 1
    assert legacy["custody_db"] is True
    assert legacy["centers_one_owner"] == 2
    assert legacy["centers_zero_owners"] == 1
    assert legacy["centers_several_owners"] == 1
    assert "error" not in report


def test_legacy_conversion_keeps_only_matching_http_secrets(tmp_path, monkeypatch):
    snap = tmp_path / "snaps"
    snap.mkdir()
    archive = _tar(snap / ARCHIVE)
    live = _live(tmp_path)
    extracted = rc.extract_needed(archive, tmp_path / "extracted")
    assert (extracted / ".platform/credential-vaults/alpha/.credential-vault.json").is_file()
    assert (extracted / ".broker/credential-custody.db").is_file()
    assert (extracted / "gamma/.credential-vault.json").is_file()
    plan = rc.merge(live, extracted)
    assert plan["legacy"]["http_kept"] == 1
    assert plan["legacy"]["llm_subscription"] == 1
    assert plan["legacy"]["unmatched"] == 1
    assert plan["legacy"]["revoked"] == 1
    assert plan["legacy"]["accounts_failed"] >= 1
    assert plan["tables"]["outbound_connections"]["insert"] == 1
    assert plan["tables"]["outbound_connection_grants"]["insert"] == 1
    assert plan["tables"]["connection_capabilities"]["insert"] == 1
    assert _ids(live) == {"c-live"}
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    vault = (
        live / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    assert vault.is_file()
    assert stat.S_IMODE(vault.stat().st_mode) == 0o600
    document = json.loads(vault.read_text(encoding="utf-8"))
    records = document["credentials"]
    assert [item["destination"] for item in records] == [DEST_OK]
    assert records[0]["token"] == TOKEN_OK
    assert records[0]["credential_type"] == "http"
    assert TOKEN_SUB not in vault.read_text(encoding="utf-8")
    assert TOKEN_REVOKED not in vault.read_text(encoding="utf-8")
    assert TOKEN_BETA not in vault.read_text(encoding="utf-8")
    assert rc._definition_id(_definition()) == _definition()["id"]
    text = json.dumps(plan)
    _assert_hidden(text)

    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver

    resolver = _GeneralVaultCredentialResolver(
        universe_dir=custody_namespace(live, OWNER_A),
    )
    assert resolver(f"vault://http/{DEST_OK}") == TOKEN_OK

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(live))
    from tinyassets.broker import ledger_queries
    from tinyassets.providers.served_model_plan import connected_sources

    class _Ledger:
        def __init__(self, path):
            self.path = path

        def _connect(self):
            db = sqlite3.connect(self.path, isolation_level=None)
            db.row_factory = sqlite3.Row
            return db

    ledger = _Ledger(live / ".broker" / "outbound.db")

    def query_ledger(_root, *, query, principal, command_center, grant_id, connection_id=""):
        return ledger_queries.local_query(
            ledger, query=query, principal=principal, command_center=command_center,
            grant_id=grant_id, connection_id=connection_id,
        )

    monkeypatch.setattr(ledger_queries, "query_ledger", query_ledger)
    found = connected_sources(live, live / "alpha", OWNER_A)
    assert f"api_key_http:{_definition()['id']}" in found


def test_live_vault_is_not_overwritten(tmp_path):
    snap = tmp_path / "snaps"
    snap.mkdir()
    archive = _tar(snap / ARCHIVE)
    live = _live(tmp_path)
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    vault = (
        live / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    vault.parent.mkdir(parents=True)
    vault.write_bytes(TOKEN_LIVE.encode())
    before = vault.read_bytes()
    extracted = rc.extract_needed(archive, tmp_path / "extracted")
    plan = rc.merge(live, extracted)
    assert vault.read_bytes() == before
    assert plan["files"]["credential_vault"]["conflict"] == 1
    assert plan["files"]["credential_vault"]["insert"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert plan["tables"]["outbound_connection_grants"]["insert"] == 0
    assert plan["tables"]["connection_capabilities"]["insert"] == 0
    assert plan["legacy"]["definitions"] == 0
    assert _ids(live) == set()
    assert TOKEN_OK not in vault.read_bytes().decode()


def test_existing_source_vault_is_not_overwritten(tmp_path):
    source = tmp_path / "source"
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    with sqlite3.connect(ledger) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO outbound_connections "
            "(connection_id, owner_user_id, revoked_at, credential_ref, label) "
            "VALUES ('c-live', ?, NULL, ?, 'ok')",
            (OWNER_A, f"vault://http/{DEST_OK}"),
        )
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    existing = (
        source / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    existing.parent.mkdir(parents=True)
    existing.write_text(json.dumps({"schema_version": 1, "credentials": [
        {"credential_type": "http", "destination": "elsewhere", "token": TOKEN_LIVE},
    ]}), encoding="utf-8")
    before = existing.read_bytes()
    platform = source / ".platform" / "credential-vaults" / "alpha"
    platform.mkdir(parents=True)
    (platform / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    custody = source / ".broker" / "credential-custody.db"
    with sqlite3.connect(custody) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.execute(
            "INSERT INTO llm_credential_deposit_owners VALUES ('alpha', ?)", (OWNER_A,),
        )
    stats = rc.convert_legacy_vaults(source)
    assert existing.read_bytes() == before
    assert stats["http_kept"] == 0
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM outbound_connections").fetchone()[0] == 0


def test_definition_requires_the_secret_in_the_live_vault(tmp_path):
    snap = tmp_path / "snaps"
    snap.mkdir()
    archive = _tar(snap / ARCHIVE)
    live = _live(tmp_path)
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    vault = (
        live / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    vault.parent.mkdir(parents=True)
    vault.write_text(json.dumps({"schema_version": 1, "credentials": [
        {"credential_type": "http", "destination": "elsewhere", "token": TOKEN_LIVE},
    ]}), encoding="utf-8")
    before = vault.read_bytes()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        db.execute(
            "INSERT INTO outbound_connections "
            "(connection_id, owner_user_id, revoked_at, credential_ref, label) "
            "VALUES ('c-live', ?, NULL, ?, 'ok')",
            (OWNER_A, f"vault://http/{DEST_OK}"),
        )
        db.execute(
            "INSERT INTO outbound_connection_grants "
            "(grant_id, connection_id, owner_user_id, revoked_at, granted_at) "
            "VALUES ('ga-live', 'c-live', ?, NULL, '2026-10-22T04:41:03+00:00')",
            (OWNER_A,),
        )
    extracted = rc.extract_needed(archive, tmp_path / "extracted")
    plan = rc.merge(live, extracted)
    assert vault.read_bytes() == before
    assert plan["legacy"]["definitions"] == 0
    assert not (live / "alpha" / "provider_definitions.json").exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        present = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='box_model_sources'"
        ).fetchone()
        if present is not None:
            assert db.execute("SELECT COUNT(*) FROM box_model_sources").fetchone()[0] == 0


def test_non_string_destination_does_not_abort_the_other_center(tmp_path):
    source = tmp_path / "source"
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    with sqlite3.connect(ledger) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO outbound_connections "
            "(connection_id, owner_user_id, revoked_at, credential_ref, label) "
            "VALUES ('c-live', ?, NULL, ?, 'ok')",
            (OWNER_A, f"vault://http/{DEST_OK}"),
        )
    custody = source / ".broker" / "credential-custody.db"
    with sqlite3.connect(custody) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.executemany(
            "INSERT INTO llm_credential_deposit_owners VALUES (?, ?)",
            (("alpha", OWNER_A), ("epsilon", OWNER_A)),
        )
    good = source / ".platform" / "credential-vaults" / "alpha"
    bad = source / "epsilon"
    good.mkdir(parents=True)
    bad.mkdir()
    (good / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    (bad / ".credential-vault.json").write_bytes(_vault_bytes([{
        "credential_type": "http",
        "destination": {"not": "a string"},
        "token": TOKEN_OTHER,
    }]))
    stats = rc.convert_legacy_vaults(source)
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    vault = (
        source / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    assert stats["http_kept"] == 1
    assert stats["unmatched"] >= 1
    assert json.loads(vault.read_text(encoding="utf-8"))["credentials"][0]["destination"] == DEST_OK


def test_center_local_record_is_kept_beside_the_platform_file(tmp_path):
    source = tmp_path / "source"
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    with sqlite3.connect(ledger) as db:
        db.executescript(SCHEMA)
        db.executemany(
            "INSERT INTO outbound_connections "
            "(connection_id, owner_user_id, revoked_at, credential_ref, label) "
            "VALUES (?, ?, NULL, ?, 'ok')",
            (
                ("c-live", OWNER_A, f"vault://http/{DEST_OK}"),
                ("c-local", OWNER_A, f"vault://http/{DEST_GAMMA}"),
            ),
        )
    custody = source / ".broker" / "credential-custody.db"
    with sqlite3.connect(custody) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.execute(
            "INSERT INTO llm_credential_deposit_owners VALUES ('alpha', ?)", (OWNER_A,),
        )
    platform = source / ".platform" / "credential-vaults" / "alpha"
    platform.mkdir(parents=True)
    (platform / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    local = source / "alpha"
    local.mkdir()
    (local / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_GAMMA, TOKEN_GAMMA)]))
    stats = rc.convert_legacy_vaults(source)
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    vault = (
        source / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    dests = {
        item["destination"]
        for item in json.loads(vault.read_text(encoding="utf-8"))["credentials"]
    }
    assert stats["http_kept"] == 2
    assert dests == {DEST_OK, DEST_GAMMA}


def test_workflow_modes_include_inspect_legacy():
    workflow = (ROOT / ".github/workflows/restore-credentials.yml").read_text(encoding="utf-8")
    shell = (ROOT / "deploy/restore-credentials.sh").read_text(encoding="utf-8")
    assert "inspect-legacy" in workflow
    assert 'if mode not in {"inspect", "inspect-legacy", "dry-run", "apply"}:' in workflow
    assert "[[ \"$mode\" =~ ^(inspect|inspect-legacy|dry-run|apply)$ ]]" in workflow
    assert "inspect|inspect-legacy|dry-run|apply" in shell
    for banned in ("reset_credentials(", "rotate_box_secrets(", "close_admission(",
                   "backup-restore.sh"):
        assert banned not in workflow
        assert banned not in shell

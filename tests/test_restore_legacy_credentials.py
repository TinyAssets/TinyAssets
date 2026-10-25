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
    assert legacy["centers_ledger_one_owner"] == 1
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
    assert plan["legacy"]["unmatched"] == 2
    assert plan["legacy"]["revoked"] == 0
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


def _source_definition(owner, universe, ref):
    fields = {
        "universe_id": universe,
        "owner_user_id": owner,
        "access_method": "api_key_http",
        "protocol": "openai_chat",
        "ref": ref,
        "model": "gpt-test",
    }
    return {
        "id": _definition_id(**fields),
        **fields,
        "visibility": "private",
        "created_at": "2026-10-22T04:41:03+00:00",
    }


def test_record_without_a_secret_inserts_nothing(tmp_path):
    """A matching destination with no usable secret is skipped, not connected."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    (live / "beta").mkdir()
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    missing = _source_definition(OWNER_A, "alpha", "ga-missing")
    blank = _source_definition(OWNER_A, "alpha", "ga-empty")
    kept = _source_definition(OWNER_B, "beta", "gb-live")
    access = _source_definition(OWNER_B, "beta", "gb-access")
    with sqlite3.connect(ledger) as db:
        db.executescript(SCHEMA)
        db.executemany(
            "INSERT INTO outbound_connections "
            "(connection_id, owner_user_id, revoked_at, credential_ref, label) "
            "VALUES (?, ?, NULL, ?, 'ok')",
            (
                ("c-missing", OWNER_A, "vault://http/missing.example.com"),
                ("c-empty", OWNER_A, "vault://http/empty.example.com"),
                ("c-beta", OWNER_B, f"vault://http/{DEST_BETA}"),
                ("c-access", OWNER_B, "vault://http/access.example.com"),
            ),
        )
        db.executemany(
            "INSERT INTO outbound_connection_grants "
            "(grant_id, connection_id, owner_user_id, revoked_at, granted_at) "
            "VALUES (?, ?, ?, NULL, '2026-10-22T04:41:03+00:00')",
            (
                ("ga-missing", "c-missing", OWNER_A),
                ("ga-empty", "c-empty", OWNER_A),
                ("gb-live", "c-beta", OWNER_B),
                ("gb-access", "c-access", OWNER_B),
            ),
        )
        db.executemany(
            "INSERT INTO connection_capabilities (connection_id, capability_kind) "
            "VALUES (?, 'model_use')",
            (("c-missing",), ("c-empty",), ("c-beta",), ("c-access",)),
        )
        for document in (missing, blank, kept, access):
            db.execute(
                "INSERT INTO box_model_sources (owner, id, definition) VALUES (?, ?, ?)",
                (document["owner_user_id"], document["id"], json.dumps(document)),
            )
    custody = source / ".broker" / "credential-custody.db"
    with sqlite3.connect(custody) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.executemany(
            "INSERT INTO llm_credential_deposit_owners VALUES (?, ?)",
            (("alpha", OWNER_A), ("beta", OWNER_B)),
        )
    alpha = source / ".platform" / "credential-vaults" / "alpha"
    beta = source / ".platform" / "credential-vaults" / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([
        {
            "credential_type": "http",
            "service": "missing.example.com",
            "destination": "missing.example.com",
        },
        {
            "credential_type": "http",
            "service": "empty.example.com",
            "destination": "empty.example.com",
            "token": "   ",
        },
    ]))
    (beta / ".credential-vault.json").write_bytes(_vault_bytes([
        _http(DEST_BETA, TOKEN_OK),
        {
            "credential_type": "http",
            "service": "access.example.com",
            "destination": "access.example.com",
            "access_token": TOKEN_OK,
        },
    ]))
    plan = rc.merge(live, source)
    assert plan["legacy"]["secret_missing"] == 2
    assert plan["legacy"]["http_kept"] == 2
    assert plan["tables"]["outbound_connections"]["insert"] == 2
    assert plan["tables"]["outbound_connection_grants"]["insert"] == 2
    assert plan["tables"]["connection_capabilities"]["insert"] == 2
    assert plan["legacy"]["definitions"] == 2
    assert _ids(live) == {"c-beta", "c-access"}
    absent = hashlib.sha256(OWNER_A.encode()).hexdigest()
    assert not (
        live / ".broker" / "accounts" / absent / "credentials" / ".credential-vault.json"
    ).exists()
    present = hashlib.sha256(OWNER_B.encode()).hexdigest()
    written = (
        live / ".broker" / "accounts" / present / "credentials" / ".credential-vault.json"
    )
    dests = {
        item["destination"]
        for item in json.loads(written.read_text(encoding="utf-8"))["credentials"]
    }
    assert dests == {DEST_BETA, "access.example.com"}
    assert not (live / "alpha" / "provider_definitions.json").exists()
    assert (live / "beta" / "provider_definitions.json").is_file()
    text = json.dumps(plan)
    _assert_hidden(text)


def test_live_vault_without_a_secret_authorizes_no_rows(tmp_path):
    source = tmp_path / "source"
    live = _live(tmp_path)
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    vault = (
        live / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    vault.parent.mkdir(parents=True)
    vault.write_text(json.dumps({"schema_version": 1, "credentials": [
        {"credential_type": "http", "destination": DEST_OK, "token": ""},
    ]}), encoding="utf-8")
    before = vault.read_bytes()
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
        db.execute(
            "INSERT INTO outbound_connection_grants "
            "(grant_id, connection_id, owner_user_id, revoked_at) "
            "VALUES ('ga-live', 'c-live', ?, NULL)",
            (OWNER_A,),
        )
        db.execute(
            "INSERT INTO connection_capabilities (connection_id, capability_kind) "
            "VALUES ('c-live', 'model_use')"
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
    # The same secret-less file on the source side must not keep the row either.
    existing = (
        source / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    existing.parent.mkdir(parents=True)
    existing.write_bytes(before)
    plan = rc.merge(live, source)
    assert vault.read_bytes() == before
    assert existing.read_bytes() == before
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert plan["tables"]["outbound_connection_grants"]["insert"] == 0
    assert plan["tables"]["connection_capabilities"]["insert"] == 0
    assert plan["legacy"]["definitions"] == 0
    assert _ids(live) == set()


def test_service_is_not_a_resolver_destination(tmp_path):
    """Only a destination the real resolver can serve becomes a connection."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    (live / "beta").mkdir()
    bad_def = _source_definition(OWNER_A, "alpha", "ga-service")
    good_def = _source_definition(OWNER_B, "beta", "gb-live")
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    with sqlite3.connect(ledger) as db:
        db.executescript(SCHEMA)
        db.executemany(
            "INSERT INTO outbound_connections "
            "(connection_id, owner_user_id, revoked_at, credential_ref, label) "
            "VALUES (?, ?, NULL, ?, 'ok')",
            (
                ("c-omitted", OWNER_A, "vault://http/omitted.example.com"),
                ("c-blank", OWNER_A, "vault://http/blank.example.com"),
                ("c-mismatch", OWNER_A, "vault://http/mismatch.example.com"),
                ("c-beta", OWNER_B, f"vault://http/{DEST_BETA}"),
            ),
        )
        db.executemany(
            "INSERT INTO outbound_connection_grants "
            "(grant_id, connection_id, owner_user_id, revoked_at, granted_at) "
            "VALUES (?, ?, ?, NULL, '2026-10-22T04:41:03+00:00')",
            (
                ("ga-service", "c-omitted", OWNER_A),
                ("ga-blank", "c-blank", OWNER_A),
                ("ga-mismatch", "c-mismatch", OWNER_A),
                ("gb-live", "c-beta", OWNER_B),
            ),
        )
        db.executemany(
            "INSERT INTO connection_capabilities (connection_id, capability_kind) "
            "VALUES (?, 'model_use')",
            (("c-omitted",), ("c-blank",), ("c-mismatch",), ("c-beta",)),
        )
        for document in (bad_def, good_def):
            db.execute(
                "INSERT INTO box_model_sources (owner, id, definition) VALUES (?, ?, ?)",
                (document["owner_user_id"], document["id"], json.dumps(document)),
            )
    custody = source / ".broker" / "credential-custody.db"
    with sqlite3.connect(custody) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.executemany(
            "INSERT INTO llm_credential_deposit_owners VALUES (?, ?)",
            (("alpha", OWNER_A), ("beta", OWNER_B)),
        )
    alpha = source / ".platform" / "credential-vaults" / "alpha"
    beta = source / ".platform" / "credential-vaults" / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([
        {
            "credential_type": "http",
            "service": "omitted.example.com",
            "token": TOKEN_OK,
        },
        {
            "credential_type": "http",
            "service": "blank.example.com",
            "destination": "   ",
            "token": TOKEN_OK,
        },
        {
            "credential_type": "http",
            "service": "mismatch.example.com",
            "destination": "other.example.com",
            "token": TOKEN_OK,
        },
    ]))
    (beta / ".credential-vault.json").write_bytes(_vault_bytes([
        _http(DEST_BETA, TOKEN_OK),
    ]))
    plan = rc.merge(live, source)
    assert plan["legacy"]["http_kept"] == 1
    assert plan["legacy"]["secret_missing"] == 0
    assert plan["legacy"]["unmatched"] == 3
    assert plan["tables"]["outbound_connections"]["insert"] == 1
    assert plan["tables"]["outbound_connection_grants"]["insert"] == 1
    assert plan["tables"]["connection_capabilities"]["insert"] == 1
    assert plan["legacy"]["definitions"] == 1
    assert _ids(live) == {"c-beta"}
    assert not (live / "alpha" / "provider_definitions.json").exists()
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver
    resolved = _GeneralVaultCredentialResolver(
        universe_dir=custody_namespace(live, OWNER_B),
    )(f"vault://http/{DEST_BETA}")
    assert resolved == TOKEN_OK
    _assert_hidden(json.dumps(plan))


def test_resolver_serves_every_kept_row(tmp_path):
    """Kept rows are exactly the refs the general resolver can answer."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    secret_dest = "secret.example.com"
    key_dest = "key.example.com"
    spaced_dest = "spaced.example.com"
    records = (
        ("c-pad", f"vault://http/{DEST_OK} ", {
            "credential_type": "http ",
            "service": "not-the-host.example.com",
            "destination": f"  {DEST_OK}  ",
            "token": TOKEN_OK,
        }, TOKEN_OK, DEST_OK),
        ("c-lead", f"  vault://http/{spaced_dest}", {
            "credential_type": "http",
            "service": "other-service.example.com",
            "destination": f"  {spaced_dest}  ",
            "token": "   ",
            "secret": TOKEN_OTHER,
        }, TOKEN_OTHER, spaced_dest),
        ("c-secret", f"vault://http/{secret_dest}", {
            "credential_type": "http",
            "service": secret_dest,
            "destination": secret_dest,
            "token": 0,
            "secret": TOKEN_REVOKED,
        }, TOKEN_REVOKED, secret_dest),
        ("c-key", f"vault://http/{key_dest}", {
            "credential_type": "http",
            "service": "unused.example.com",
            "destination": key_dest,
            "api_key": TOKEN_UNMATCHED,
        }, TOKEN_UNMATCHED, key_dest),
    )
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    with sqlite3.connect(ledger) as db:
        db.executescript(SCHEMA)
        db.executemany(
            "INSERT INTO outbound_connections "
            "(connection_id, owner_user_id, revoked_at, credential_ref, label) "
            "VALUES (?, ?, NULL, ?, 'ok')",
            tuple(
                (connection_id, OWNER_A, ref)
                for connection_id, ref, _record, _token, _dest in records
            ),
        )
        db.executemany(
            "INSERT INTO outbound_connection_grants "
            "(grant_id, connection_id, owner_user_id, revoked_at, granted_at) "
            "VALUES (?, ?, ?, NULL, '2026-10-22T04:41:03+00:00')",
            tuple(
                (f"g-{connection_id}", connection_id, OWNER_A)
                for connection_id, _ref, _record, _token, _dest in records
            ),
        )
        db.executemany(
            "INSERT INTO connection_capabilities (connection_id, capability_kind) "
            "VALUES (?, 'model_use')",
            tuple((connection_id,) for connection_id, _ref, _record, _token, _dest in records),
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
    (platform / ".credential-vault.json").write_bytes(
        _vault_bytes([record for _connection_id, _ref, record, _token, _dest in records])
    )
    plan = rc.merge(live, source)
    assert plan["legacy"]["http_kept"] == 4
    assert plan["legacy"]["secret_missing"] == 0
    assert plan["legacy"]["unmatched"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 4
    assert plan["tables"]["outbound_connection_grants"]["insert"] == 4
    assert plan["tables"]["connection_capabilities"]["insert"] == 4
    assert _ids(live) == {connection_id for connection_id, _ref, _record, _token, _dest in records}
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver
    resolver = _GeneralVaultCredentialResolver(universe_dir=custody_namespace(live, OWNER_A))
    for _connection_id, ref, _record, token, dest in records:
        assert resolver(ref) == token
        assert resolver(f"vault://http/{dest}") == token
    _assert_hidden(json.dumps(plan))


def test_earlier_empty_secret_blocks_a_later_token(tmp_path):
    """The resolver raises on the first match, so a later token does not count."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    payload = json.dumps({"schema_version": 1, "credentials": [
        {"credential_type": "http", "destination": DEST_OK, "token": "   "},
        {"credential_type": "http", "destination": DEST_OK, "token": TOKEN_OK},
    ]})
    vault = (
        live / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    vault.parent.mkdir(parents=True)
    vault.write_text(payload, encoding="utf-8")
    before = vault.read_bytes()
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
        db.execute(
            "INSERT INTO outbound_connection_grants "
            "(grant_id, connection_id, owner_user_id, revoked_at, granted_at) "
            "VALUES ('ga-live', 'c-live', ?, NULL, '2026-10-22T04:41:03+00:00')",
            (OWNER_A,),
        )
        db.execute(
            "INSERT INTO connection_capabilities (connection_id, capability_kind) "
            "VALUES ('c-live', 'model_use')"
        )
        db.execute(
            "INSERT INTO box_model_sources (owner, id, definition) VALUES (?, ?, ?)",
            (OWNER_A, _definition()["id"], json.dumps(_definition())),
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
    existing = (
        source / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )
    existing.parent.mkdir(parents=True)
    existing.write_bytes(before)
    plan = rc.merge(live, source)
    assert vault.read_bytes() == before
    assert existing.read_bytes() == before
    assert plan["legacy"]["http_kept"] == 0
    assert plan["legacy"]["secret_missing"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert plan["tables"]["outbound_connection_grants"]["insert"] == 0
    assert plan["tables"]["connection_capabilities"]["insert"] == 0
    assert plan["legacy"]["definitions"] == 0
    assert _ids(live) == set()
    assert not (live / "alpha" / "provider_definitions.json").exists()
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver
    resolver = _GeneralVaultCredentialResolver(universe_dir=custody_namespace(live, OWNER_A))
    with pytest.raises(RuntimeError, match="credential reference is unavailable"):
        resolver(f"vault://http/{DEST_OK}")


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


def _universe_ledger(connections, grants):
    script = SCHEMA.replace(
        "    revoked_at TEXT,\n    granted_at TEXT,\n    unprompted_action_cap_json TEXT",
        "    universe_id TEXT,\n    revoked_at TEXT,\n    granted_at TEXT,\n"
        "    unprompted_action_cap_json TEXT",
    ).replace(
        "    credential_ref TEXT,\n    label TEXT,",
        "    credential_ref TEXT,\n    destination TEXT,\n    label TEXT,",
    )
    return _sqlite_bytes(script, {
        "outbound_connections": connections,
        "outbound_connection_grants": grants,
    })


def _conn_universe(owner, connection_id, dest, universe):
    return {
        "connection_id": connection_id,
        "owner_user_id": owner,
        "revoked_at": None,
        "credential_ref": f"vault://http/{dest}",
        "destination": dest,
        "label": "ok",
    }


def _grant_universe(owner, grant_id, connection_id, universe):
    return {
        "grant_id": grant_id,
        "connection_id": connection_id,
        "owner_user_id": owner,
        "universe_id": universe,
        "revoked_at": None,
        "granted_at": "2026-10-22T04:41:03+00:00",
        "unprompted_action_cap_json": None,
    }


def test_grant_universe_maps_a_center_only_with_its_vault_destination(
        tmp_path, monkeypatch, capsys):
    """The center directory is the universe id, and only with a vault destination."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    (live / "beta").mkdir()
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [
            _conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha"),
            _conn_universe(OWNER_B, "c-beta", DEST_BETA, "beta"),
            _conn_universe(OWNER_A, "c-gamma", DEST_GAMMA, "gamma"),
            _conn_universe(OWNER_D, "c-delta", DEST_OK, "delta"),
        ],
        [
            _grant_universe(OWNER_A, "ga", "c-alpha", "alpha"),
            _grant_universe(OWNER_B, "gb", "c-beta", "beta"),
            _grant_universe(OWNER_A, "gg", "c-gamma", "gamma"),
            _grant_universe(OWNER_D, "gd", "c-delta", "delta"),
        ],
    ))
    alpha = source / "alpha"
    alpha.mkdir(parents=True)
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    beta = source / ".platform" / "credential-vaults" / "beta"
    beta.mkdir(parents=True)
    (beta / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_BETA, TOKEN_BETA)]))
    gamma = source / "gamma"
    gamma.mkdir()
    (gamma / ".credential-vault.json").write_bytes(
        _vault_bytes([_http(DEST_UNMATCHED, TOKEN_UNMATCHED)])
    )
    delta = source / "delta"
    delta.mkdir()
    (delta / ".credential-vault.json").write_bytes(_vault_bytes([{
        "credential_type": "http",
        "service": DEST_OK,
        "token": TOKEN_OTHER,
    }]))
    snap = tmp_path / "snaps"
    snap.mkdir()
    archive = snap / ARCHIVE
    with tarfile.open(archive, "w:gz") as handle:
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            info = tarfile.TarInfo("_data/" + path.relative_to(source).as_posix())
            data = path.read_bytes()
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    assert rc.main([
        "--mode", "inspect-legacy", "--source", ARCHIVE,
        "--root", str(tmp_path / "unused"),
        "--snapshot-dir", str(snap),
        "--backup-log", str(tmp_path / "missing.log"),
    ]) == 0
    text = capsys.readouterr().out
    _assert_hidden(text)
    legacy = json.loads(text)["legacy"]
    assert legacy["custody_db"] is False
    assert legacy["center_vault_files"] == 3
    assert legacy["platform_vault_files"] == 1
    assert legacy["centers_one_owner"] == 2
    assert legacy["centers_ledger_one_owner"] == 2
    assert legacy["centers_zero_owners"] == 2
    assert legacy["centers_several_owners"] == 0
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_ledger_one_owner"] == 2
    assert plan["legacy"]["http_kept"] == 2
    assert plan["tables"]["outbound_connections"]["insert"] == 2
    assert plan["tables"]["outbound_connection_grants"]["insert"] == 2
    assert _ids(live) == {"c-alpha", "c-beta"}
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver
    for owner, dest, token in ((OWNER_A, DEST_OK, TOKEN_OK), (OWNER_B, DEST_BETA, TOKEN_BETA)):
        resolved = _GeneralVaultCredentialResolver(
            universe_dir=custody_namespace(live, owner),
        )(f"vault://http/{dest}")
        assert resolved == token
    _assert_hidden(json.dumps(plan))


def test_two_grant_owners_or_a_custody_disagreement_maps_nobody(tmp_path):
    source = tmp_path / "source"
    live = _live(tmp_path)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [
            _conn_universe(OWNER_A, "c-a", DEST_OK, "alpha"),
            _conn_universe(OWNER_B, "c-b", DEST_BETA, "alpha"),
            _conn_universe(OWNER_B, "c-d", DEST_GAMMA, "delta"),
        ],
        [
            _grant_universe(OWNER_A, "ga", "c-a", "alpha"),
            _grant_universe(OWNER_B, "gb", "c-b", "alpha"),
            _grant_universe(OWNER_B, "gd", "c-d", "delta"),
        ],
    ))
    custody = source / ".broker" / "credential-custody.db"
    with sqlite3.connect(custody) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.execute(
            "INSERT INTO llm_credential_deposit_owners VALUES ('delta', ?)",
            (OWNER_D,),
        )
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([
        _http(DEST_OK, TOKEN_OK),
        _http(DEST_BETA, TOKEN_BETA),
    ]))
    delta = source / "delta"
    delta.mkdir()
    (delta / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_GAMMA, TOKEN_GAMMA)]))
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_ledger_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 2
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert _ids(live) == set()


def test_definition_file_maps_when_its_grant_destination_is_in_the_vault(tmp_path):
    source = tmp_path / "source"
    live = _live(tmp_path)
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
        db.execute(
            "INSERT INTO outbound_connection_grants "
            "(grant_id, connection_id, owner_user_id, revoked_at, granted_at) "
            "VALUES ('ga-live', 'c-live', ?, NULL, '2026-10-22T04:41:03+00:00')",
            (OWNER_A,),
        )
    alpha = source / "alpha"
    alpha.mkdir(parents=True)
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    (alpha / "provider_definitions.json").write_text(json.dumps([{
        "universe_id": "alpha",
        "owner_user_id": OWNER_A,
        "ref": "ga-live",
    }]), encoding="utf-8")
    wrong = source / "beta"
    wrong.mkdir()
    (wrong / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_BETA, TOKEN_BETA)]))
    (wrong / "provider_definitions.json").write_text(json.dumps([{
        "universe_id": "alpha",
        "owner_user_id": OWNER_A,
        "ref": "ga-live",
    }]), encoding="utf-8")
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_one_owner"] == 1
    assert plan["legacy"]["centers_ledger_one_owner"] == 1
    assert plan["legacy"]["centers_zero_owners"] == 1
    assert plan["legacy"]["http_kept"] == 1
    assert _ids(live) == {"c-live"}
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver
    resolved = _GeneralVaultCredentialResolver(
        universe_dir=custody_namespace(live, OWNER_A),
    )(f"vault://http/{DEST_OK}")
    assert resolved == TOKEN_OK


def _owner_vault(root, owner):
    digest = hashlib.sha256(owner.encode()).hexdigest()
    return (
        root / ".broker" / "accounts" / digest / "credentials" / ".credential-vault.json"
    )


def _resolved(root, owner, dest):
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver
    return _GeneralVaultCredentialResolver(
        universe_dir=custody_namespace(root, owner),
    )(f"vault://http/{dest}")


def test_another_universes_grant_does_not_receive_this_centers_token(tmp_path):
    """A token stays behind when its only grant names a different universe."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [
            _conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha"),
            _conn_universe(OWNER_A, "c-gamma", DEST_BETA, "gamma"),
        ],
        [
            _grant_universe(OWNER_A, "ga", "c-alpha", "alpha"),
            _grant_universe(OWNER_A, "gg", "c-gamma", "gamma"),
        ],
    ))
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([
        _http(DEST_OK, TOKEN_OK),
        _http(DEST_BETA, TOKEN_BETA),
    ]))
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_one_owner"] == 1
    assert plan["legacy"]["http_kept"] == 1
    assert plan["tables"]["outbound_connections"]["insert"] == 1
    assert _ids(live) == {"c-alpha"}
    vault = _owner_vault(live, OWNER_A)
    text = vault.read_text(encoding="utf-8")
    assert TOKEN_BETA not in text
    assert [item["destination"] for item in json.loads(text)["credentials"]] == [DEST_OK]
    assert _resolved(live, OWNER_A, DEST_OK) == TOKEN_OK
    _assert_hidden(json.dumps(plan))


def test_destination_disagreement_does_not_restore_that_token(tmp_path):
    """A destination column that disagrees with the ref is not evidence."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    disagreed = _conn_universe(OWNER_A, "c-bad", DEST_BETA, "alpha")
    disagreed["destination"] = DEST_GAMMA
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [
            _conn_universe(OWNER_A, "c-ok", DEST_OK, "alpha"),
            disagreed,
        ],
        [
            _grant_universe(OWNER_A, "ga", "c-ok", "alpha"),
            _grant_universe(OWNER_A, "gb", "c-bad", "alpha"),
        ],
    ))
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([
        _http(DEST_OK, TOKEN_OK),
        _http(DEST_BETA, TOKEN_BETA),
    ]))
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_one_owner"] == 1
    assert plan["legacy"]["http_kept"] == 1
    assert _ids(live) == {"c-ok"}
    text = _owner_vault(live, OWNER_A).read_text(encoding="utf-8")
    assert TOKEN_BETA not in text
    assert _resolved(live, OWNER_A, DEST_OK) == TOKEN_OK
    _assert_hidden(json.dumps(plan))


def test_definition_does_not_copy_another_destination(tmp_path):
    """One definition does not give that owner every token in the vault."""
    source = tmp_path / "source"
    live = _live(tmp_path)
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
                ("c-other", OWNER_A, f"vault://http/{DEST_BETA}"),
                ("c-beta", OWNER_B, f"vault://http/{DEST_BETA}"),
            ),
        )
        db.executemany(
            "INSERT INTO outbound_connection_grants "
            "(grant_id, connection_id, owner_user_id, revoked_at, granted_at) "
            "VALUES (?, ?, ?, NULL, '2026-10-22T04:41:03+00:00')",
            (
                ("ga-live", "c-live", OWNER_A),
                ("ga-other", "c-other", OWNER_A),
                ("gb-beta", "c-beta", OWNER_B),
            ),
        )
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([
        _http(DEST_OK, TOKEN_OK),
        _http(DEST_BETA, TOKEN_BETA),
    ]))
    (alpha / "provider_definitions.json").write_text(json.dumps([{
        "universe_id": "alpha",
        "owner_user_id": OWNER_A,
        "ref": "ga-live",
    }]), encoding="utf-8")
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_one_owner"] == 1
    assert plan["legacy"]["centers_ledger_one_owner"] == 1
    assert plan["legacy"]["http_kept"] == 1
    assert _ids(live) == {"c-live"}
    text = _owner_vault(live, OWNER_A).read_text(encoding="utf-8")
    assert TOKEN_BETA not in text
    assert not _owner_vault(live, OWNER_B).exists()
    assert _resolved(live, OWNER_A, DEST_OK) == TOKEN_OK
    _assert_hidden(json.dumps(plan))


def test_bad_definition_row_does_not_hand_the_center_to_custody(tmp_path):
    """A non-string universe id must not erase two grant owners."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [
            _conn_universe(OWNER_A, "c-a", DEST_OK, "alpha"),
            _conn_universe(OWNER_B, "c-b", DEST_BETA, "alpha"),
        ],
        [
            _grant_universe(OWNER_A, "ga", "c-a", "alpha"),
            _grant_universe(OWNER_B, "gb", "c-b", "alpha"),
        ],
    ))
    with sqlite3.connect(ledger) as db:
        db.execute(
            "INSERT INTO box_model_sources (owner, id, definition) VALUES (?, ?, ?)",
            (OWNER_A, "bad", json.dumps({
                "universe_id": ["alpha"],
                "owner_user_id": OWNER_A,
                "ref": "ga",
            })),
        )
    custody = source / ".broker" / "credential-custody.db"
    with sqlite3.connect(custody) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.execute(
            "INSERT INTO llm_credential_deposit_owners VALUES ('alpha', ?)",
            (OWNER_A,),
        )
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([
        _http(DEST_OK, TOKEN_OK),
        _http(DEST_BETA, TOKEN_BETA),
    ]))
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["legacy"]["centers_ledger_one_owner"] == 0
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_A).exists()
    assert not _owner_vault(source, OWNER_A).exists()
    _assert_hidden(json.dumps(plan))


def test_ledger_type_error_refuses_every_mapped_center(tmp_path, monkeypatch):
    """A failed ledger read refuses the center instead of trusting custody."""
    def boom(*_args, **_kwargs):
        raise TypeError("definition row")

    monkeypatch.setattr(rc, "_add_definition_owners", boom)
    source = tmp_path / "source"
    live = _live(tmp_path)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [_conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha")],
        [_grant_universe(OWNER_A, "ga", "c-alpha", "alpha")],
    ))
    custody = source / ".broker" / "credential-custody.db"
    with sqlite3.connect(custody) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.execute(
            "INSERT INTO llm_credential_deposit_owners VALUES ('alpha', ?)",
            (OWNER_A,),
        )
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["legacy"]["http_kept"] == 0
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_A).exists()
    assert TOKEN_OK not in json.dumps(plan)
    _assert_hidden(json.dumps(plan))


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

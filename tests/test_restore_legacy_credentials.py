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
    live = _live(tmp_path)
    plan = rc.merge(live, source)
    assert existing.read_bytes() == before
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert TOKEN_OK.encode() not in existing.read_bytes()
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM outbound_connections").fetchone()[0] == 1
    copied = _owner_vault(live, OWNER_A)
    if copied.exists():
        assert TOKEN_OK.encode() not in copied.read_bytes()


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
    assert "error" not in plan
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
    assert "error" not in plan
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
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(ledger) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b1', ?, ?)",
            (OWNER_B, sealed),
        )
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
    plan = rc.merge(live, source)
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert plan["tables"]["browser_vault"]["insert"] == 0
    assert plan["legacy"]["definitions"] == 0
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_A).exists()
    assert not _owner_vault(source, OWNER_A).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
    assert sealed.decode() not in json.dumps(plan)
    assert TOKEN_OK not in json.dumps(plan)
    _assert_hidden(json.dumps(plan))


def test_unreadable_custody_does_not_fall_through_to_the_grant(tmp_path, monkeypatch):
    """A custody file that is not a database refuses every row, not only the center."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [_conn_universe(OWNER_B, "c-beta", DEST_OK, "alpha")],
        [_grant_universe(OWNER_B, "gb", "c-beta", "alpha")],
    ))
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(ledger) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b1', ?, ?)",
            (OWNER_B, sealed),
        )
    custody = source / ".broker" / "credential-custody.db"
    custody.write_bytes(b"not a database")
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
    ro_opens, connect_opens = _watch_source_opens(monkeypatch, ledger, custody)
    plan = rc.merge(live, source)
    assert ro_opens.count(ledger.resolve()) == 1
    assert ro_opens.count(custody.resolve()) == 1
    assert connect_opens == []
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["legacy"]["centers_ledger_one_owner"] == 0
    assert plan["legacy"]["http_kept"] == 0
    assert plan["legacy"]["definitions"] == 0
    for table in (
        "outbound_connections", "outbound_connection_grants",
        "connection_capabilities", "browser_vault",
    ):
        assert plan["tables"][table]["insert"] == 0
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_B).exists()
    assert not _owner_vault(source, OWNER_B).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
    text = json.dumps(plan)
    assert sealed.decode() not in text
    _assert_hidden(text)


def test_ledger_open_failure_does_not_let_custody_map(tmp_path, monkeypatch):
    """_open_ro failing on the ledger refuses the center that custody names."""
    real_open = rc._open_ro

    def open_ro(path):
        if Path(path).name == "outbound.db":
            raise sqlite3.OperationalError("ledger unreadable")
        return real_open(path)

    monkeypatch.setattr(rc, "_open_ro", open_ro)
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
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_A).exists()
    assert not _owner_vault(source, OWNER_A).exists()
    _assert_hidden(json.dumps(plan))


def test_non_database_ledger_refuses_without_raising(tmp_path):
    """A ledger that is not a database stays a counts-only refusal."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"not a database")
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    plan = rc.merge(live, source)
    assert plan["error"] == "unreadable"
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_A).exists()
    _assert_hidden(json.dumps(plan))


def _same_file(path, other):
    try:
        return Path(path).resolve() == Path(other).resolve()
    except OSError:
        return False


def test_closed_ledger_is_not_read_again_for_any_row(tmp_path, monkeypatch):
    """The first failed open stays closed, so no later row is written."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    source_db = source / ".broker" / "outbound.db"
    source_db.parent.mkdir(parents=True)
    source_db.write_bytes(_universe_ledger(
        [_conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha")],
        [_grant_universe(OWNER_A, "ga-live", "c-alpha", "alpha")],
    ))
    definition = _source_definition(OWNER_A, "alpha", "ga-live")
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(source_db) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b1', ?, ?)",
            (OWNER_B, sealed),
        )
        db.execute(
            "INSERT INTO box_model_sources (owner, id, definition) VALUES (?, ?, ?)",
            (OWNER_A, definition["id"], json.dumps(definition)),
        )
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    live_db = live / ".broker" / "outbound.db"
    with sqlite3.connect(live_db) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
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
    seeded = _owner_vault(live, OWNER_A)
    seeded.parent.mkdir(parents=True)
    seeded.write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    before = seeded.read_bytes()
    ro_opens = []
    connect_opens = []
    real_open = rc._open_ro
    real_connect = sqlite3.connect

    def open_ro(path):
        if _same_file(path, source_db):
            ro_opens.append(path)
            if len(ro_opens) == 1:
                raise sqlite3.OperationalError("first open")
        return real_open(path)

    def connect(path, *args, **kwargs):
        if _same_file(path, source_db):
            connect_opens.append(path)
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(rc, "_open_ro", open_ro)
    monkeypatch.setattr(rc.sqlite3, "connect", connect)
    plan = rc.merge(live, source)
    assert len(ro_opens) == 1
    assert connect_opens == []
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["legacy"]["http_kept"] == 0
    assert plan["legacy"]["definitions"] == 0
    for table in (
        "outbound_connections", "outbound_connection_grants",
        "connection_capabilities", "browser_vault",
    ):
        assert plan["tables"][table]["insert"] == 0
    assert seeded.read_bytes() == before
    assert not (live / "alpha" / "provider_definitions.json").exists()
    with sqlite3.connect(live_db) as db:
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM box_model_sources").fetchone()[0] == 0
    text = json.dumps(plan)
    assert sealed.decode() not in text
    _assert_hidden(text)


def test_closed_custody_file_is_not_opened_again(tmp_path, monkeypatch):
    """A custody file the scan could not open is not opened for a later pass."""
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
    opens = []
    real_open = rc._open_ro

    def open_ro(path):
        if _same_file(path, custody):
            opens.append(path)
            if len(opens) == 1:
                raise sqlite3.OperationalError("first open")
        return real_open(path)

    monkeypatch.setattr(rc, "_open_ro", open_ro)
    plan = rc.merge(live, source)
    assert len(opens) == 1
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    _mapping, counts, _evidence, _failed = rc.legacy_center_owners(
        custody, {"alpha"}, ledger_path=ledger,
        center_dests={"alpha": {DEST_OK}},
    )
    assert len(opens) == 1
    assert counts["centers_one_owner"] == 0
    assert counts["centers_several_owners"] == 1
    assert not _owner_vault(live, OWNER_A).exists()
    _assert_hidden(json.dumps(plan))


def _watch_source_opens(monkeypatch, *paths):
    """Count sqlite opens of these source files. Copies elsewhere do not count."""
    wanted = [Path(path).resolve() for path in paths]
    ro_opens = []
    connect_opens = []
    real_open = rc._open_ro
    real_connect = sqlite3.connect

    def open_ro(path):
        try:
            resolved = Path(path).resolve()
        except (OSError, TypeError):
            resolved = None
        if resolved in wanted:
            ro_opens.append(resolved)
        return real_open(path)

    def connect(path, *args, **kwargs):
        try:
            resolved = Path(path).resolve()
        except (OSError, TypeError):
            resolved = None
        if resolved in wanted:
            connect_opens.append(resolved)
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(rc, "_open_ro", open_ro)
    monkeypatch.setattr(rc.sqlite3, "connect", connect)
    return ro_opens, connect_opens


def _mapped_source(tmp):
    """One center, one owner, and a browser secret filed under a second owner."""
    source = tmp / "source"
    live = _live(tmp)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [_conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha")],
        [_grant_universe(OWNER_A, "ga", "c-alpha", "alpha")],
    ))
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(ledger) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b1', ?, ?)",
            (OWNER_B, sealed),
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b-empty', '', ?)",
            (sealed,),
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b-sentinel', ?, ?)",
            ("\0nope", sealed),
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
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
    return source, live, ledger, custody, sealed


def _assert_one_owner_restore(live, plan, sealed):
    assert plan["legacy"]["centers_one_owner"] == 1
    assert plan["legacy"]["http_kept"] == 1
    assert plan["tables"]["outbound_connections"]["insert"] == 1
    assert plan["tables"]["browser_vault"]["insert"] == 1
    assert _ids(live) == {"c-alpha"}
    vault = _owner_vault(live, OWNER_A)
    assert TOKEN_OK.encode() in vault.read_bytes()
    assert not _owner_vault(live, OWNER_B).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        owners = [row[0] for row in db.execute("SELECT owner FROM browser_vault")]
    assert owners == [OWNER_B]
    assert "error" not in plan
    text = json.dumps(plan)
    assert sealed.decode() not in text
    _assert_hidden(text)


def test_source_databases_are_opened_once(tmp_path, monkeypatch):
    """Merge reads the source ledger and custody once, then only the private copy."""
    source, live, ledger, custody, sealed = _mapped_source(tmp_path)
    ro_opens, connect_opens = _watch_source_opens(monkeypatch, ledger, custody)
    plan = rc.merge(live, source)
    assert ro_opens.count(ledger.resolve()) == 1
    assert ro_opens.count(custody.resolve()) == 1
    assert connect_opens == []
    _assert_one_owner_restore(live, plan, sealed)


def test_source_replaced_after_the_scan_uses_the_private_copy(tmp_path, monkeypatch):
    """Garbage written after the scan does not reopen the source or drop a row."""
    source, live, ledger, custody, sealed = _mapped_source(tmp_path)
    ro_opens, connect_opens = _watch_source_opens(monkeypatch, ledger, custody)
    garbage = b"not a database"
    real_owners = rc.legacy_center_owners
    real_keep = rc._keep_vault_backed
    real_restrict = rc._restrict_ledger
    real_definitions = rc._restrict_definitions
    real_groups = rc._groups
    real_copy = rc._copy_definitions
    steps = []

    def owners(*args, **kwargs):
        result = real_owners(*args, **kwargs)
        ledger.write_bytes(garbage)
        custody.write_bytes(garbage)
        return result

    def guard(name, real):
        def wrapped(*args, **kwargs):
            assert ledger.read_bytes() == garbage
            assert custody.read_bytes() == garbage
            steps.append(name)
            return real(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(rc, "legacy_center_owners", owners)
    monkeypatch.setattr(rc, "_keep_vault_backed", guard("keep", real_keep))
    monkeypatch.setattr(rc, "_restrict_ledger", guard("restrict", real_restrict))
    monkeypatch.setattr(rc, "_restrict_definitions", guard("definitions", real_definitions))
    monkeypatch.setattr(rc, "_groups", guard("groups", real_groups))
    monkeypatch.setattr(rc, "_copy_definitions", guard("copy", real_copy))
    plan = rc.merge(live, source)
    assert steps == ["keep", "restrict", "definitions", "groups", "copy"]
    assert ro_opens.count(ledger.resolve()) == 1
    assert ro_opens.count(custody.resolve()) == 1
    assert connect_opens == []
    assert ledger.read_bytes() == garbage
    assert custody.read_bytes() == garbage
    _assert_one_owner_restore(live, plan, sealed)


def test_failure_after_the_scan_writes_nothing(tmp_path, monkeypatch):
    """A database error after the scan does not escape merge or leave a secret."""
    source, live, ledger, custody, sealed = _mapped_source(tmp_path)

    def boom(*_args, **_kwargs):
        raise sqlite3.DatabaseError("unreadable after scan")

    monkeypatch.setattr(rc, "_keep_vault_backed", boom)
    plan = rc.merge(live, source)
    assert plan["error"] == "unreadable"
    assert plan["legacy"]["centers_one_owner"] == 0
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["legacy"]["http_kept"] == 0
    assert plan["legacy"]["definitions"] == 0
    for table in (
        "outbound_connections", "outbound_connection_grants",
        "connection_capabilities", "browser_vault",
    ):
        assert plan["tables"][table]["insert"] == 0
    assert _ids(live) == set()
    assert not _owner_vault(source, OWNER_A).exists()
    assert not _owner_vault(live, OWNER_A).exists()
    assert not _owner_vault(live, OWNER_B).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
    text = json.dumps(plan)
    assert sealed.decode() not in text
    _assert_hidden(text)


def test_apply_tree_turns_an_unexpected_error_into_a_refusal(tmp_path, monkeypatch):
    """apply restarts the broker, then returns counts instead of the traceback."""
    _source, live, _ledger, _custody, _sealed = _mapped_source(tmp_path)
    calls = []

    def run(argv, timeout=0):
        calls.append(tuple(argv))
        return type("Done", (), {"returncode": 0})()

    def boom(*_args, **_kwargs):
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(rc, "merge", boom)
    plan, facts, _checked, error = rc.apply_tree(
        live, tmp_path / "source",
        tmp_path / "credential-restore-2026-10-25T01-02-03Z", run=run,
    )
    assert calls == [
        ("docker", "stop", "tinyassets-broker"),
        ("docker", "start", "tinyassets-broker"),
    ]
    assert facts["broker_started"] is True
    assert error == "unreadable"
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["browser_vault"]["insert"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert plan["accounts"] == 0
    _assert_hidden(json.dumps(plan))


def _browser_table(live):
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS browser_vault ("
            "id TEXT PRIMARY KEY, owner TEXT NOT NULL, sealed BLOB)"
        )


def test_failed_read_is_unreadable_through_apply_and_the_cli(
        tmp_path, monkeypatch, capsys):
    """A bad ledger and a grouping failure both exit non-zero as unreadable."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"not a database")
    (source / "alpha").mkdir()
    (source / "alpha" / ".credential-vault.json").write_bytes(
        _vault_bytes([_http(DEST_OK, TOKEN_OK)])
    )
    calls = []

    def run(argv, timeout=0):
        calls.append(tuple(argv))
        return type("Done", (), {"returncode": 0})()

    plan, _facts, _checked, error = rc.apply_tree(
        live, source, tmp_path / "credential-restore-2026-10-25T01-02-03Z", run=run,
    )
    assert calls == [
        ("docker", "stop", "tinyassets-broker"),
        ("docker", "start", "tinyassets-broker"),
    ]
    assert error == "unreadable"
    assert plan["error"] == "unreadable"
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["browser_vault"]["insert"] == 0
    assert not _owner_vault(live, OWNER_A).exists()

    source, live, _ledger, _custody, sealed = _mapped_source(tmp_path / "groups")
    _browser_table(live)

    def boom(*_args, **_kwargs):
        raise sqlite3.DatabaseError("groups failed")

    monkeypatch.setattr(rc, "_groups", boom)
    plan = rc.merge(live, source)
    assert plan["error"] == "unreadable"
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["browser_vault"]["insert"] == 0
    assert not _owner_vault(live, OWNER_A).exists()
    assert not _owner_vault(source, OWNER_A).exists()
    assert sealed.decode() not in json.dumps(plan)

    root = _live(tmp_path / "cli")
    retained = tmp_path / "cli" / ".tinyassets-restore-old.Ab12"
    kept = retained / ".broker" / "outbound.db"
    kept.parent.mkdir(parents=True)
    with sqlite3.connect(kept) as db:
        db.executescript(SCHEMA)
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    monkeypatch.delenv("BACKUP_DEST", raising=False)
    with pytest.raises(SystemExit) as raised:
        rc.main([
            "--mode", "dry-run", "--root", str(root), "--source", retained.name,
            "--snapshot-dir", str(tmp_path / "cli" / "snaps"),
            "--backup-log", str(tmp_path / "cli" / "missing.log"),
            "--mounts", str(tmp_path / "cli" / "mounts"),
        ])
    assert raised.value.code == 2
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["error"] == "unreadable"
    assert report["plan"]["error"] == "unreadable"
    assert report["plan"]["legacy"]["http_kept"] == 0
    _assert_hidden(out)


def test_failed_vault_write_drops_that_owner_only(tmp_path, monkeypatch):
    """A vault write is counted only after it lands. Other owners still merge."""
    source = tmp_path / "source"
    live = _live(tmp_path)
    _browser_table(live)
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [
            _conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha"),
            _conn_universe(OWNER_B, "c-beta", DEST_BETA, "beta"),
        ],
        [
            _grant_universe(OWNER_A, "ga", "c-alpha", "alpha"),
            _grant_universe(OWNER_B, "gb", "c-beta", "beta"),
        ],
    ))
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(ledger) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b1', ?, ?)",
            (OWNER_B, sealed),
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
    for center, dest, token in (
        ("alpha", DEST_OK, TOKEN_OK), ("beta", DEST_BETA, TOKEN_BETA),
    ):
        directory = source / center
        directory.mkdir()
        (directory / ".credential-vault.json").write_bytes(
            _vault_bytes([_http(dest, token)])
        )
    real_write = rc._write_private_json
    failed = hashlib.sha256(OWNER_B.encode()).hexdigest()

    def write(path, payload):
        if failed in str(path):
            raise OSError("vault write failed")
        return real_write(path, payload)

    monkeypatch.setattr(rc, "_write_private_json", write)
    plan = rc.merge(live, source)
    assert "error" not in plan
    assert plan["legacy"]["http_kept"] == 1
    assert plan["legacy"]["accounts_failed"] == 1
    assert plan["tables"]["outbound_connections"]["insert"] == 1
    assert plan["tables"]["browser_vault"]["insert"] == 0
    assert _ids(live) == {"c-alpha"}
    assert TOKEN_OK.encode() in _owner_vault(live, OWNER_A).read_bytes()
    assert not _owner_vault(live, OWNER_B).exists()
    assert not _owner_vault(source, OWNER_B).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
    text = json.dumps(plan)
    assert sealed.decode() not in text
    _assert_hidden(text)


def test_symlinked_broker_is_not_followed(tmp_path, monkeypatch):
    """A symlinked .broker is a failed read for merge and apply."""
    real = tmp_path / "real"
    real_db = real / ".broker" / "outbound.db"
    real_db.parent.mkdir(parents=True)
    real_db.write_bytes(_universe_ledger(
        [_conn_universe(OWNER_B, "c-beta", DEST_OK, "alpha")],
        [_grant_universe(OWNER_B, "gb", "c-beta", "alpha")],
    ))
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(real_db) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b1', ?, ?)",
            (OWNER_B, sealed),
        )
    source = tmp_path / "source"
    source.mkdir()
    (source / ".broker").symlink_to(real / ".broker", target_is_directory=True)
    alpha = source / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    linked_defs = tmp_path / "defs"
    linked_defs.mkdir()
    (linked_defs / "provider_definitions.json").write_text("[]", encoding="utf-8")
    (source / "alpha-link").symlink_to(linked_defs, target_is_directory=True)
    live = _live(tmp_path)
    _browser_table(live)
    ro_opens, connect_opens = _watch_source_opens(monkeypatch, real_db)
    plan = rc.merge(live, source)
    assert ro_opens == []
    assert connect_opens == []
    assert plan["error"] == "unreadable"
    assert plan["legacy"]["http_kept"] == 0
    assert plan["tables"]["browser_vault"]["insert"] == 0
    assert plan["tables"]["outbound_connections"]["insert"] == 0
    assert not _owner_vault(live, OWNER_B).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
    _assert_hidden(json.dumps(plan))

    calls = []

    def run(argv, timeout=0):
        calls.append(tuple(argv))
        return type("Done", (), {"returncode": 0})()

    other = tmp_path / "apply-source"
    other.mkdir()
    (other / ".broker").symlink_to(real / ".broker", target_is_directory=True)
    (other / "alpha").mkdir()
    (other / "alpha" / ".credential-vault.json").write_bytes(
        _vault_bytes([_http(DEST_OK, TOKEN_OK)])
    )
    apply_live = _live(tmp_path / "apply-live")
    _browser_table(apply_live)
    applied, _facts, _checked, error = rc.apply_tree(
        apply_live, other, tmp_path / "credential-restore-2026-10-25T01-02-03Z",
        run=run,
    )
    assert calls == [
        ("docker", "stop", "tinyassets-broker"),
        ("docker", "start", "tinyassets-broker"),
    ]
    assert error == "unreadable"
    assert applied["error"] == "unreadable"
    assert applied["tables"]["browser_vault"]["insert"] == 0
    assert applied["legacy"]["http_kept"] == 0
    with sqlite3.connect(apply_live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
    assert ro_opens == []
    assert connect_opens == []
    _assert_hidden(json.dumps(applied))


def _custody_db(path, owner):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE llm_credential_deposit_owners "
            "(universe_id TEXT, owner_user_id TEXT)"
        )
        db.execute(
            "INSERT INTO llm_credential_deposit_owners VALUES ('alpha', ?)",
            (owner,),
        )
    return path


def _owner_a_tree(root, custody_target):
    """Ledger and vault name owner-a. The browser row names owner-b."""
    ledger = root / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [_conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha")],
        [_grant_universe(OWNER_A, "ga", "c-alpha", "alpha")],
    ))
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(ledger) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b1', ?, ?)",
            (OWNER_B, sealed),
        )
    alpha = root / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    (root / ".broker" / "credential-custody.db").symlink_to(custody_target)
    return sealed


def _assert_nothing_written(live, sealed, text):
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_A).exists()
    assert not _owner_vault(live, OWNER_B).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM outbound_connections").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM outbound_connection_grants"
        ).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
    assert sealed.decode() not in text
    _assert_hidden(text)


def test_symlinked_custody_is_unreadable_on_the_workflow_path(
        tmp_path, monkeypatch, capsys):
    """Catalog, retained copy, and inspect refuse a custody symlink."""
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    monkeypatch.delenv("BACKUP_DEST", raising=False)
    calls = []

    def run(argv, timeout=0):
        calls.append(tuple(argv))
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setitem(rc.apply_tree.__kwdefaults__, "run", run)
    garbage = tmp_path / "not-a-database"
    garbage.write_bytes(b"not a database")
    other = _custody_db(tmp_path / "other-owner.db", OWNER_B)
    for name, target in (("garbage", garbage), ("other-owner", other)):
        base = tmp_path / name
        live = _live(base)
        _browser_table(live)
        retained = base / ".tinyassets-restore-old.Ab12"
        sealed = _owner_a_tree(retained, target)
        with pytest.raises(rc.SourceRefused) as measured:
            rc.measure_root(retained)
        assert measured.value.code == "unreadable"
        with pytest.raises(rc.SourceRefused) as copied:
            rc._copy_wanted(retained, base / "copied")
        assert copied.value.code == "unreadable"
        with pytest.raises(rc.SourceRefused) as inspected:
            rc.inspect_legacy_tree(retained)
        assert inspected.value.code == "unreadable"
        argv = [
            "--root", str(live), "--source", retained.name,
            "--snapshot-dir", str(base / "snaps"),
            "--backup-log", str(base / "missing.log"),
            "--mounts", str(base / "mounts"),
        ]
        with pytest.raises(SystemExit) as inspect_exit:
            rc.main(["--mode", "inspect-legacy", *argv])
        assert inspect_exit.value.code == 2
        inspect_out = capsys.readouterr().out
        inspect_report = json.loads(inspect_out)
        assert inspect_report["error"] == "unreadable"
        assert inspect_report["legacy"]["custody_db"] is False
        assert inspect_report["legacy"]["centers_one_owner"] == 0
        _assert_nothing_written(live, sealed, inspect_out)
        with pytest.raises(SystemExit) as apply_exit:
            rc.main(["--mode", "apply", *argv])
        assert apply_exit.value.code == 2
        apply_out = capsys.readouterr().out
        apply_report = json.loads(apply_out)
        assert apply_report["error"] == "unreadable"
        assert "plan" not in apply_report
        chosen = next(
            item for item in apply_report["sources"] if item["name"] == retained.name
        )
        assert chosen["readable"] is False
        assert chosen["error"] == "unreadable"
        _assert_nothing_written(live, sealed, apply_out)
    assert calls == []


def test_corrupt_snapshot_reread_refuses_the_merge(tmp_path, monkeypatch, capsys):
    """Garbage between groups and the definition copy refuses every owner."""
    base = tmp_path / "reread"
    live = _live(base)
    _browser_table(live)
    retained = base / ".tinyassets-restore-old.Ab12"
    ledger = retained / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [
            _conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha"),
            _conn_universe(OWNER_B, "c-beta", DEST_BETA, "beta"),
        ],
        [
            _grant_universe(OWNER_A, "ga", "c-alpha", "alpha"),
            _grant_universe(OWNER_B, "gb", "c-beta", "beta"),
        ],
    ))
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(ledger) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.executemany(
            "INSERT INTO connection_capabilities (connection_id, capability_kind) "
            "VALUES (?, 'model_use')",
            (("c-alpha",), ("c-beta",)),
        )
        db.executemany(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES (?, ?, ?)",
            (("b-a", OWNER_A, sealed), ("b-b", OWNER_B, sealed)),
        )
    _custody_db(retained / ".broker" / "credential-custody.db", OWNER_A)
    with sqlite3.connect(retained / ".broker" / "credential-custody.db") as db:
        db.execute(
            "INSERT INTO llm_credential_deposit_owners VALUES ('beta', ?)",
            (OWNER_B,),
        )
    for center, dest, token in (
        ("alpha", DEST_OK, TOKEN_OK), ("beta", DEST_BETA, TOKEN_BETA),
    ):
        directory = retained / center
        directory.mkdir()
        (directory / ".credential-vault.json").write_bytes(
            _vault_bytes([_http(dest, token)])
        )
    real_groups = rc._groups

    def groups(root):
        loaded = real_groups(root)
        snap = rc._SNAPSHOTS[rc._source_key(root)]
        snap.paths["outbound.db"].write_bytes(b"not a database")
        return loaded

    monkeypatch.setattr(rc, "_groups", groups)
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    monkeypatch.delenv("BACKUP_DEST", raising=False)
    calls = []

    def run(argv, timeout=0):
        calls.append(tuple(argv))
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setitem(rc.apply_tree.__kwdefaults__, "run", run)
    with pytest.raises(SystemExit) as raised:
        rc.main([
            "--mode", "apply", "--root", str(live), "--source", retained.name,
            "--snapshot-dir", str(base / "snaps"),
            "--backup-log", str(base / "missing.log"),
            "--mounts", str(base / "mounts"),
        ])
    assert raised.value.code == 2
    assert calls == [
        ("docker", "stop", "tinyassets-broker"),
        ("docker", "start", "tinyassets-broker"),
    ]
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["error"] == "unreadable"
    plan = report["plan"]
    assert plan["error"] == "unreadable"
    assert plan["legacy"]["http_kept"] == 0
    assert plan["legacy"]["definitions"] == 0
    assert plan["accounts"] == 0
    for table in (
        "outbound_connections", "outbound_connection_grants",
        "connection_capabilities", "browser_vault",
    ):
        assert plan["tables"][table]["insert"] == 0
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_A).exists()
    assert not _owner_vault(live, OWNER_B).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        for table in (
            "outbound_connections", "outbound_connection_grants",
            "connection_capabilities", "browser_vault",
        ):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert sealed.decode() not in out
    _assert_hidden(out)


_WANTED_LINK = (
    "center", "platform", "provider", "account-vault", "account-subscription", "marker",
)
_ACCOUNT_HASH = "a" * 64


def _wanted_link(retained, kind, target):
    if kind == "center":
        path = retained / "alpha" / ".credential-vault.json"
    elif kind == "platform":
        path = (
            retained / ".platform" / "credential-vaults" / "alpha" / ".credential-vault.json"
        )
    elif kind == "provider":
        path = retained / "alpha" / "provider_definitions.json"
    elif kind == "account-vault":
        path = (
            retained / ".broker" / "accounts" / _ACCOUNT_HASH / "credentials"
            / ".credential-vault.json"
        )
    elif kind == "account-subscription":
        path = (
            retained / ".broker" / "accounts" / _ACCOUNT_HASH / "credentials"
            / ".subscription-state.json"
        )
    else:
        path = retained / "300001" / ".ta-cli" / "box" / "connection"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    return path


def _readable_measure(_root):
    return {
        "accounts": 0,
        "accounts_with_vault": 0,
        "accounts_with_subscription": 0,
        **{table: 0 for table in rc.TABLES},
        "connection_markers": 0,
    }


def _cli_retained(live, retained, base, mode):
    return [
        "--mode", mode, "--root", str(live), "--source", retained.name,
        "--snapshot-dir", str(base / "snaps"),
        "--backup-log", str(base / "missing.log"),
        "--mounts", str(base / "mounts"),
    ]


def _assert_symlink_refused(live, target, capsys, before):
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["error"] == "unreadable"
    assert "plan" not in report
    assert str(target) not in out
    assert target.name not in out
    assert TOKEN_OK not in out
    _assert_hidden(out)
    assert _ids(live) == set()
    assert not _owner_vault(live, OWNER_A).exists()
    assert not _owner_vault(live, OWNER_B).exists()
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        assert db.execute("SELECT COUNT(*) FROM browser_vault").fetchone()[0] == 0
    after = set(Path(tempfile.gettempdir()).glob("ta-cred-src-*"))
    assert after - before == set()
    return report


def test_wanted_symlink_is_a_counts_only_refusal(tmp_path, monkeypatch, capsys):
    """Catalog and main both refuse a wanted symlink with exit 2 and no temp copy."""
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    monkeypatch.delenv("BACKUP_DEST", raising=False)
    calls = []

    def run(argv, timeout=0):
        calls.append(tuple(argv))
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setitem(rc.apply_tree.__kwdefaults__, "run", run)
    real_measure = rc.measure_root
    for kind in _WANTED_LINK:
        base = tmp_path / kind
        live = _live(base)
        _browser_table(live)
        retained = base / ".tinyassets-restore-old.Ab12"
        ledger = retained / ".broker" / "outbound.db"
        ledger.parent.mkdir(parents=True)
        ledger.write_bytes(_universe_ledger(
            [_conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha")],
            [_grant_universe(OWNER_A, "ga", "c-alpha", "alpha")],
        ))
        target = base / "secret-target-vault"
        target.write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
        _wanted_link(retained, kind, target)
        with pytest.raises(rc.SourceRefused) as measured:
            rc.measure_root(retained)
        assert measured.value.code == "unreadable"
        with pytest.raises(rc.SourceRefused) as copied:
            rc._copy_wanted(retained, base / "copied")
        assert copied.value.code == "unreadable"
        for mode in ("dry-run", "apply"):
            before = set(Path(tempfile.gettempdir()).glob("ta-cred-src-*"))
            with pytest.raises(SystemExit) as raised:
                rc.main(_cli_retained(live, retained, base, mode))
            assert raised.value.code == 2
            report = _assert_symlink_refused(live, target, capsys, before)
            chosen = next(
                item for item in report["sources"] if item["name"] == retained.name
            )
            assert chosen["readable"] is False
            assert chosen["error"] == "unreadable"
        monkeypatch.setattr(rc, "measure_root", _readable_measure)
        for mode in ("dry-run", "apply"):
            before = set(Path(tempfile.gettempdir()).glob("ta-cred-src-*"))
            with pytest.raises(SystemExit) as raised:
                rc.main(_cli_retained(live, retained, base, mode))
            assert raised.value.code == 2
            report = _assert_symlink_refused(live, target, capsys, before)
            chosen = next(
                item for item in report["sources"] if item["name"] == retained.name
            )
            assert chosen["readable"] is True
        monkeypatch.setattr(rc, "measure_root", real_measure)
    assert calls == []


def test_unwanted_symlink_still_restores(tmp_path, monkeypatch, capsys):
    """A link that is not a credential member does not block the restore."""
    base = tmp_path / "keep"
    live = _live(base)
    _browser_table(live)
    retained = base / ".tinyassets-restore-old.Ab12"
    ledger = retained / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [_conn_universe(OWNER_A, "c-alpha", DEST_OK, "alpha")],
        [_grant_universe(OWNER_A, "ga", "c-alpha", "alpha")],
    ))
    sealed = b"sealed-browser-secret"
    with sqlite3.connect(ledger) as db:
        db.execute(
            "CREATE TABLE browser_vault (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "sealed BLOB)"
        )
        db.execute(
            "INSERT INTO browser_vault (id, owner, sealed) VALUES ('b1', ?, ?)",
            (OWNER_B, sealed),
        )
    _custody_db(retained / ".broker" / "credential-custody.db", OWNER_A)
    alpha = retained / "alpha"
    alpha.mkdir()
    (alpha / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_OK, TOKEN_OK)]))
    outside = base / "outside-link-target"
    outside.write_text("OUTSIDE-LINK-SECRET-0123456789", encoding="utf-8")
    (retained / "notes.txt").symlink_to(outside)
    mirror = base / "outside-dir"
    mirror.mkdir()
    (mirror / ".credential-vault.json").write_bytes(_vault_bytes([_http(DEST_BETA, TOKEN_BETA)]))
    (retained / "mirror").symlink_to(mirror, target_is_directory=True)
    rc.measure_root(retained)
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    monkeypatch.delenv("BACKUP_DEST", raising=False)
    calls = []

    def run(argv, timeout=0):
        calls.append(tuple(argv))
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setitem(rc.apply_tree.__kwdefaults__, "run", run)
    before = set(Path(tempfile.gettempdir()).glob("ta-cred-src-*"))
    assert rc.main(_cli_retained(live, retained, base, "dry-run")) == 0
    dry = capsys.readouterr().out
    dry_report = json.loads(dry)
    assert "error" not in dry_report
    assert dry_report["plan"]["legacy"]["http_kept"] == 1
    assert not _owner_vault(live, OWNER_A).exists()
    assert "OUTSIDE-LINK-SECRET-0123456789" not in dry
    assert str(outside) not in dry
    assert TOKEN_BETA not in dry
    _assert_hidden(dry)
    assert rc.main(_cli_retained(live, retained, base, "apply")) == 0
    applied = capsys.readouterr().out
    report = json.loads(applied)
    assert "error" not in report
    _assert_one_owner_restore(live, report["plan"], sealed)
    assert "OUTSIDE-LINK-SECRET-0123456789" not in applied
    assert str(outside) not in applied
    assert str(mirror) not in applied
    assert TOKEN_BETA.encode() not in _owner_vault(live, OWNER_A).read_bytes()
    _assert_hidden(applied)
    assert calls == [
        ("docker", "stop", "tinyassets-broker"),
        ("docker", "start", "tinyassets-broker"),
    ]
    after = set(Path(tempfile.gettempdir()).glob("ta-cred-src-*"))
    assert after - before == set()


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


def _owned_definition(owner, center, ref):
    fields = {
        "universe_id": center,
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


def _identities(root, owners):
    path = root / ".broker" / "state" / "owner-identities.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE owner_volumes (uid INTEGER, principal TEXT)")
        db.executemany(
            "INSERT INTO owner_volumes VALUES (?, ?)",
            [(300000 + index, owner) for index, owner in enumerate(owners)],
        )


def _production_shape(tmp_path):
    """The dry-run that refused: old definition columns, and the reported counts."""
    owners = [OWNER_A, OWNER_B, "owner-c", OWNER_D, "owner-e", "owner-f", "owner-g"]
    centers = ("alpha", "beta", "gamma")
    mapped = owners[:3]
    dests = [f"dest-{index}.example" for index in range(10)]
    center_dests = {"alpha": dests[:4], "beta": dests[4:7], "gamma": dests[7:]}
    source = tmp_path / "source"
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    db = sqlite3.connect(ledger)
    db.executescript(SCHEMA + "ALTER TABLE outbound_connection_grants ADD COLUMN universe_id TEXT;")
    db.execute("DROP TABLE box_model_sources")
    db.execute(
        "CREATE TABLE box_model_sources ("
        "center TEXT NOT NULL, id TEXT NOT NULL, definition TEXT NOT NULL, "
        "PRIMARY KEY (center, id))"
    )
    db.execute("PRAGMA journal_mode=WAL")
    connections = []
    for center, owner in zip(centers, mapped, strict=True):
        for dest in center_dests[center][:2]:
            connections.append((owner, dest, center, False))
        connections.append((owner, center_dests[center][0], center, True))
    while len(connections) < 14:
        connections.append(("owner-extra", "other.example", "no-center", False))
    for index, (owner, dest, center, revoked) in enumerate(connections, start=1):
        db.execute(
            "INSERT INTO outbound_connections ("
            "connection_id, owner_user_id, revoked_at, credential_ref, label, "
            "granted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"c-{index}", owner, "2026-10-01" if revoked else None,
             f"vault://http/{dest}", "source-label", "2026-10-22T04:41:03+00:00"),
        )
    # Twelve grants: the nine center rows, then three that name no vault center.
    for index in range(1, 13):
        owner, _dest, center, revoked = connections[index - 1]
        db.execute(
            "INSERT INTO outbound_connection_grants ("
            "grant_id, connection_id, owner_user_id, universe_id, revoked_at, "
            "granted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"g-{index}", f"c-{index}", owner, center,
             "2026-10-01" if revoked else None, "2026-10-22T04:41:03+00:00"),
        )
    for index in range(1, 4):
        db.execute(
            "INSERT INTO connection_capabilities (connection_id, capability_kind) "
            "VALUES (?, ?)",
            (f"c-{index}", "model_use"),
        )
    for center, owner in zip(centers, mapped, strict=True):
        ref = next(
            f"g-{index}" for index, (row_owner, _dest, row_center, revoked)
            in enumerate(connections, start=1)
            if row_owner == owner and row_center == center and not revoked
        )
        document = _owned_definition(owner, center, ref)
        db.execute(
            "INSERT INTO box_model_sources (center, id, definition) VALUES (?, ?, ?)",
            (center, document["id"], json.dumps(document)),
        )
        directory = source / center
        directory.mkdir(parents=True)
        (directory / "provider_definitions.json").write_text(
            json.dumps([document]), encoding="utf-8",
        )
    db.execute(
        "INSERT INTO box_model_sources (center, id, definition) VALUES (?, ?, ?)",
        ("alpha", "not-a-definition", "not-json"),
    )
    records = {
        "alpha": [_http(dest, f"tok-{dest}") for dest in dests[:4]]
        + [{"credential_type": "llm_subscription", "service": "cli"}],
        "beta": [_http(dest, f"tok-{dest}") for dest in dests[4:7]],
        "gamma": [_http(dest, f"tok-{dest}") for dest in dests[7:]]
        + [{"credential_type": "llm_subscription", "service": "cli"}],
    }
    for center, vault_records in records.items():
        (source / center / ".credential-vault.json").write_bytes(_vault_bytes(vault_records))
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    for extra in ledger.parent.glob("outbound.db-*"):
        extra.unlink()

    live = _live(tmp_path)
    _identities(live, owners)
    digest = hashlib.sha256(OWNER_A.encode()).hexdigest()
    credentials = live / ".broker" / "accounts" / digest / "credentials"
    credentials.mkdir(parents=True)
    (credentials / ".credential-vault.json").write_bytes(
        _vault_bytes([_http(dests[0], "live-tok")])
    )
    (credentials / ".subscription-state.json").write_text("{}", encoding="utf-8")
    with sqlite3.connect(live / ".broker" / "outbound.db") as db:
        db.execute(
            "INSERT INTO outbound_connections ("
            "connection_id, owner_user_id, credential_ref, label, granted_at"
            ") VALUES ('c-1', ?, ?, 'changed', '2026-10-22T04:41:03+00:00')",
            (OWNER_A, f"vault://http/{dests[0]}"),
        )
        db.execute(
            "INSERT INTO outbound_connections ("
            "connection_id, owner_user_id, credential_ref, label, granted_at"
            ") VALUES ('live-2', ?, ?, 'live', '2026-10-22T04:41:03+00:00')",
            (OWNER_A, "vault://http/live.example"),
        )
        db.execute(
            "INSERT INTO outbound_connection_grants ("
            "grant_id, connection_id, owner_user_id, granted_at"
            ") VALUES ('live-g1', 'c-1', ?, '2026-10-22T04:41:03+00:00')",
            (OWNER_A,),
        )
        db.execute(
            "INSERT INTO outbound_connection_grants ("
            "grant_id, connection_id, owner_user_id, granted_at"
            ") VALUES ('live-g2', 'live-2', ?, '2026-10-22T04:41:03+00:00')",
            (OWNER_A,),
        )
        db.execute(
            "INSERT INTO connection_capabilities (connection_id, capability_kind) "
            "VALUES ('live-2', 'model_use')"
        )
    (live / ".layout.json").write_text(
        json.dumps({"preparation": {"credential_roots_reset": False}}),
        encoding="utf-8",
    )
    return source, live


def test_precutover_definition_table_does_not_refuse_the_dry_run(tmp_path):
    source, live = _production_shape(tmp_path)
    inspected = rc.inspect_legacy_tree(source)
    assert inspected["center_vault_files"] == 3
    assert inspected["http"] == 10
    assert inspected["llm_subscription"] == 2
    assert inspected["custody_db"] is False
    assert inspected["centers_one_owner"] == 3
    assert inspected["centers_ledger_one_owner"] == 3
    assert inspected["centers_several_owners"] == 0
    assert inspected["centers_zero_owners"] == 0
    measured = rc.measure_root(source)
    assert measured["outbound_connections"] == 14
    assert measured["outbound_connection_grants"] == 12
    assert measured["connection_capabilities"] == 3
    assert measured["accounts_with_vault"] == 0
    assert measured["accounts_with_subscription"] == 0
    assert measured["browser_vault"] == 0
    assert measured["connection_markers"] == 0
    live_measured = rc.measure_root(live)
    assert live_measured["accounts"] == 7
    assert live_measured["accounts_with_vault"] == 1
    assert live_measured["accounts_with_subscription"] == 1
    assert live_measured["outbound_connections"] == 2
    assert live_measured["outbound_connection_grants"] == 2
    assert live_measured["connection_capabilities"] == 1
    assert live_measured["browser_vault"] == 0
    assert rc.layout_facts(live)["layout_readable"] is True
    plan = rc._plan_only(live, source)
    assert plan.get("error") is None
    assert "stage" not in plan["legacy"]
    assert plan["legacy"]["centers_one_owner"] == 3
    assert plan["legacy"]["centers_several_owners"] == 0
    assert plan["legacy"]["centers_ledger_one_owner"] == 3
    assert plan["legacy"]["http_kept"] > 0
    assert plan["accounts"] > 0
    assert plan["legacy"]["definitions"] >= 1
    assert plan["tables"]["outbound_connections"]["conflict"] >= 1


def test_refusal_names_the_failing_stage(tmp_path, monkeypatch):
    source = tmp_path / "source"
    live = _live(tmp_path)
    alpha = source / "alpha"
    alpha.mkdir(parents=True)
    (alpha / ".credential-vault.json").write_bytes(
        _vault_bytes([_http("alpha.example", TOKEN_OK)])
    )
    ledger = source / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"not a database")
    plan = rc.merge(live, source)
    assert plan["error"] == "unreadable"
    assert plan["legacy"]["stage"] == "snapshot"
    assert plan["legacy"]["centers_several_owners"] == 1
    assert plan["accounts"] == 0

    grouped = tmp_path / "grouped"
    beta = grouped / "beta"
    beta.mkdir(parents=True)
    (beta / ".credential-vault.json").write_bytes(
        _vault_bytes([_http("beta.example", TOKEN_BETA)])
    )
    ledger = grouped / ".broker" / "outbound.db"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(_universe_ledger(
        [_conn_universe(OWNER_B, "c1", "beta.example", "beta")],
        [_grant_universe(OWNER_B, "g1", "c1", "beta")],
    ))

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("definition columns")

    monkeypatch.setattr(rc, "_source_definition_rows", boom)
    refused = rc.merge(live, grouped)
    assert refused["error"] == "unreadable"
    assert refused["legacy"]["stage"] == "definitions"
    assert refused["legacy"]["centers_several_owners"] == 1
    assert refused["accounts"] == 0

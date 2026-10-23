"""N1 ingress proof and installation identity guards."""

import hashlib
import hmac
import json
import time

import pytest

from tinyassets.broker.inbound_installations import InstallationAuthority
from tinyassets.broker.inbound_verification import (
    InvalidProfile,
    extract_single_installation,
    verify,
)
from tinyassets.owner_router import _headers
from tinyassets.storage.inbound_events import accept, pending


def _slack(body, *, timestamp=None, secret=b"signing-secret"):
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = "v0=" + hmac.new(
        secret, b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256
    ).hexdigest()
    headers = {"x-slack-request-timestamp": timestamp, "x-slack-signature": signature}
    profile = {
        "method": "hmac-sha256", "signature_header": "x-slack-signature",
        "timestamp_header": "x-slack-request-timestamp", "version": "v0",
        "encoding": "hex", "prefix": "v0=", "max_skew_seconds": 300,
    }
    return raw, headers, profile, secret


def _envelope(*, enterprise=False, shared=False, authorizations=None):
    return {
        "type": "event_callback", "is_ext_shared_channel": shared,
        "authorizations": authorizations if authorizations is not None else [{
            "is_enterprise_install": enterprise,
            "enterprise_id": "E-one", "team_id": "T-one",
        }],
        "event": {"type": "message", "text": "hello"},
    }


def test_slack_hmac_precedes_fixed_workspace_or_org_extraction():
    for enterprise, expected in ((False, ("workspace", "T-one")),
                                 (True, ("enterprise", "E-one"))):
        raw, headers, profile, secret = _slack(_envelope(enterprise=enterprise))
        assert verify(profile, headers, raw, secret=secret)
        assert extract_single_installation(raw, {"content-type": "application/json"}) == expected
        assert not verify(profile, headers, raw + b" ", secret=secret)


def test_slack_ambiguous_and_unsupported_envelopes_are_no_ops():
    examples = [
        _envelope(shared=True),
        _envelope(authorizations=[]),
        _envelope(authorizations=[{"is_enterprise_install": False, "team_id": "T"}] * 2),
        _envelope(authorizations=[{"is_enterprise_install": "false", "team_id": "T"}]),
        _envelope(authorizations=[{"is_enterprise_install": True, "team_id": "T"}]),
    ]
    for body in examples:
        raw, headers, profile, secret = _slack(body)
        assert verify(profile, headers, raw, secret=secret)
        assert extract_single_installation(raw, {"content-type": "application/json"}) is None
    assert extract_single_installation(
        b'{"is_ext_shared_channel":false,"authorizations":[],"x":{"a":1,"a":2}}',
        {"content-type": "application/json"},
    ) is None
    raw, _, _, _ = _slack(_envelope())
    assert extract_single_installation(
        raw, {"content-type": "application/x-www-form-urlencoded"}) is None


def test_slack_rejects_stale_or_oversized_proof_without_parsing():
    raw, headers, profile, secret = _slack(_envelope(), timestamp=int(time.time()) - 301)
    assert not verify(profile, headers, raw, secret=secret)
    assert extract_single_installation(b" " * (1024 * 1024 + 1),
                                       {"content-type": "application/json"}) is None


def test_generic_capability_and_profile_schema():
    token = "capability"
    profile = {"method": "capability", "digest": hashlib.sha256(token.encode()).hexdigest()}
    assert verify(profile, {}, b"any", token=token)
    assert not verify(profile, {}, b"any", token="wrong")
    with pytest.raises(InvalidProfile):
        verify({"method": "expression", "source": "body.owner"}, {}, b"{}")
    with pytest.raises(InvalidProfile):
        verify({"method": "rs256", "key_url": "https://attacker.invalid/key"}, {}, b"{}")


def test_generic_raw_hmac_base64_recipe():
    import base64

    raw = b"{\"message\":\"hello\"}"
    signature = base64.b64encode(hmac.new(b"key", raw, hashlib.sha256).digest()).decode()
    profile = {"method": "hmac-sha256", "signature_header": "x-signature",
               "encoding": "base64", "prefix": "sha256="}
    assert verify(profile, {"X-Signature": "sha256=" + signature}, raw, secret=b"key")
    assert not verify(profile, {"X-Signature": "sha256=" + signature}, raw + b" ", secret=b"key")


def test_install_uses_same_session_and_server_inspected_identity(tmp_path):
    authority = InstallationAuthority(tmp_path / "installations.db")
    state = authority.begin("alice", "browser-a")
    with pytest.raises(PermissionError):
        authority.complete(state, "alice", "browser-b", lambda: ("workspace", "T", "cred"),
                           lambda _: "revoked")
    record = authority.complete(state, "alice", "browser-a",
                                lambda: ("workspace", "T", "cred"), lambda _: "revoked")
    assert record["owner"] == "alice"
    assert record["installation_id"] == "T"
    with pytest.raises(PermissionError):
        authority.complete(state, "alice", "browser-a", lambda: ("workspace", "T", "cred"),
                           lambda _: "revoked")


def test_install_conflicts_preserve_live_and_defer_unknown(tmp_path):
    authority = InstallationAuthority(tmp_path / "installations.db")

    def install(owner, status):
        state = authority.begin(owner, owner + "-browser")
        return authority.complete(state, owner, owner + "-browser",
                                  lambda: ("workspace", "T", owner + "-credential"),
                                  lambda _: status)

    first = install("alice", "revoked")
    with pytest.raises(PermissionError):
        install("bob", "live")
    # The refused callback consumes state; it cannot be replayed after a
    # provider-side credential status change.
    refused_state = authority.begin("bob", "browser")
    with pytest.raises(PermissionError):
        authority.complete(refused_state, "bob", "browser",
                           lambda: ("workspace", "T", "new-ref"), lambda _: "live")
    with pytest.raises(PermissionError):
        authority.complete(refused_state, "bob", "browser",
                           lambda: ("workspace", "T", "new-ref"), lambda _: "revoked")
    with pytest.raises(TimeoutError):
        install("bob", "unknown")
    assert authority.lookup("workspace", "T") == first
    replacement = install("bob", "revoked")
    assert replacement["revision"] > first["revision"]
    assert authority.lookup("workspace", "T") == replacement
    assert authority.for_owner("alice") == []


def test_retirement_is_owner_scoped_and_requires_provider_revocation(tmp_path):
    authority = InstallationAuthority(tmp_path / "installations.db")
    state = authority.begin("alice", "browser")
    record = authority.complete(state, "alice", "browser",
                                lambda: ("workspace", "T", "credential"),
                                lambda _: "revoked")
    with pytest.raises(PermissionError):
        authority.retire("bob", record["binding"], lambda _: "revoked")
    with pytest.raises(PermissionError):
        authority.retire("alice", record["binding"], lambda _: "live")
    authority.retire("alice", record["binding"], lambda _: "revoked")
    assert authority.lookup("workspace", "T") is None


def test_forward_strips_proof_and_caller_identity_case_insensitively():
    incoming = [(b"X-Slack-Signature", b"secret"), (b"x-Hub-Signature-256", b"secret"),
                (b"PROXY-Authorization", b"secret"), (b"Cookie", b"secret"),
                (b"X-Custom-Proof", b"secret"), (b"X-Owner", b"bob"),
                (b"Content-Type", b"application/json")]
    result = _headers(incoming, private=True,
                      proof_headers={b"x-custom-proof", b"x-slack-signature",
                                     b"x-hub-signature-256"})
    assert result == [(b"Content-Type", b"application/json")]


def test_box_inbox_atomic_receipt_replay_and_freshness(tmp_path):
    now = 1_800_000_000
    first = accept(tmp_path, binding="binding", revision=3, event_id="event-1",
                   event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now)
    assert first["accepted"] and first["receipt"]
    replay = accept(tmp_path, binding="binding", revision=3, event_id="event-1",
                    event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now)
    assert replay == {**first, "accepted": False}
    assert len(pending(tmp_path)) == 1
    with pytest.raises(ValueError, match="event_id_conflict"):
        accept(tmp_path, binding="binding", revision=3, event_id="event-1",
               event_at=now, body=b"different", owner="alice", agent_ref="main", now=now)
    with pytest.raises(ValueError, match="stale_event"):
        accept(tmp_path, binding="binding", revision=3, event_id="event-1",
               event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now + 86401)
    second = accept(tmp_path, binding="binding", revision=4, event_id="event-1",
                    event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now)
    assert second["receipt"] != first["receipt"]

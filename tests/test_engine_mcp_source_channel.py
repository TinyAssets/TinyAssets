"""Served-surface source_channel consent verb (channel-add parity, §2.2).

Locks in the confinement for the CONSENT half of "add a channel via the
channel-agnostic node": current-owner admission, graph-pin, least-privilege
caps, sink-consent-only (source_code approval refused to preserve the create-only
write_graph RCE closure), and action=approve only. A regression turns a gate red
instead of silently widening the served effect-consent surface.
"""
from __future__ import annotations

import json

_AEC = '{"channel_type":"authenticated_external_call","destination":"d"}'


def _bind(monkeypatch, *, actor="sub-9", graph="u-9", allow=("u-9",)):
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", actor)
    monkeypatch.setattr(s, "_GRAPH_ID", graph)
    # Admission is isolated here; downstream operation/consent guards stay real.
    from tests.engine_authority_helpers import mock_engine_admission
    mock_engine_admission(monkeypatch, allow)
    return s


def _patch_impl(monkeypatch):
    """Capture the underlying source_channel impl call; return the capture dict."""
    import tinyassets.api.source_channel as sc
    from tinyassets.auth import middleware

    captured: dict = {}

    def _fake(*, action, universe_id="", branch_id="", payload=None):
        captured["action"] = action
        captured["universe_id"] = universe_id
        captured["branch_id"] = branch_id
        captured["payload"] = payload
        captured["caps"] = set(middleware.current_identity().capabilities)
        return json.dumps({"ok": True})

    monkeypatch.setattr(sc, "source_channel", _fake)
    return captured


def test_source_channel_fails_closed_unbound(monkeypatch):
    s = _bind(monkeypatch, actor="", graph="u-9")  # _ACTOR_ID unbound
    cap = _patch_impl(monkeypatch)
    out = json.loads(s.source_channel(action="approve", payload=_AEC))
    assert "error" in out
    assert cap == {}  # impl never reached


def test_source_channel_refused_without_serving_authority(monkeypatch):
    s = _bind(monkeypatch, graph="u-9", allow=("u-other",))
    cap = _patch_impl(monkeypatch)
    out = json.loads(s.source_channel(action="approve", payload=_AEC))
    assert "current serving owner" in out["error"]
    assert cap == {}  # never reached the impl


def test_source_channel_only_approve_and_revoke_actions(monkeypatch):
    """approve and revoke are served (change agent-access-controls); the policy
    verbs are not, because the policy store has no reader (design D3)."""
    s = _bind(monkeypatch)
    cap = _patch_impl(monkeypatch)
    for bad in ("set_policy", "get_policy", "delete", ""):
        out = json.loads(s.source_channel(action=bad, payload=_AEC))
        assert "error" in out
    assert cap == {}  # impl never reached for any unserved action


def test_source_channel_revoke_pins_universe_and_least_privilege(monkeypatch):
    s = _bind(monkeypatch, graph="u-pinned", allow=("u-pinned",))
    cap = _patch_impl(monkeypatch)
    out = json.loads(s.source_channel(action="revoke", payload=_AEC))
    assert out == {"ok": True}
    assert cap["universe_id"] == "u-pinned"
    assert cap["action"] == "revoke"
    assert cap["caps"] == {"write"}


def test_source_channel_refuses_source_code_rce_closure(monkeypatch):
    """Approving source_code is not a served verb. Since change
    `sandboxed-code-node` (2026-08-30) approval gates nothing - a code node runs
    in the OS sandbox, in the universe that authored it - so the verb refuses
    with that fact rather than pointing at a browser approval; the impl (which
    would stamp provenance) is never reached either way."""
    s = _bind(monkeypatch)
    cap = _patch_impl(monkeypatch)
    out = json.loads(s.source_channel(
        action="approve",
        branch_id="b-1",
        payload='{"channel_type":"source_code","node_id":"n1"}',
    ))
    assert "source_code needs no approval" in out["error"]
    assert "browser" not in out["error"]
    assert cap == {}  # the impl (which would approve the node) is never reached


def test_source_channel_rejects_malformed_payload(monkeypatch):
    s = _bind(monkeypatch)
    cap = _patch_impl(monkeypatch)
    for bad in ("", "not json", "[1,2]", '"a string"'):
        out = json.loads(s.source_channel(action="approve", payload=bad))
        assert "error" in out
    assert cap == {}


def test_source_channel_rejects_non_string_members(monkeypatch):
    """A non-string payload member returns a structured error, never crashes the impl
    with AttributeError, and can't smuggle a list-wrapped 'source_code' past the refusal
    (Codex #2)."""
    s = _bind(monkeypatch)
    cap = _patch_impl(monkeypatch)
    for bad in (
        '{"channel_type":["source_code"],"destination":"d"}',
        '{"channel_type":"authenticated_external_call","destination":123}',
        '{"channel_type":{"x":1},"destination":"d"}',
    ):
        out = json.loads(s.source_channel(action="approve", payload=bad))
        assert "must be a string" in out["error"], bad
    assert cap == {}  # impl never reached — no crash, no mutation


def test_source_channel_approve_pins_universe_and_least_privilege(monkeypatch):
    s = _bind(monkeypatch, graph="u-pinned", allow=("u-pinned",))
    cap = _patch_impl(monkeypatch)
    out = json.loads(s.source_channel(
        action="approve",
        payload='{"channel_type":"authenticated_external_call","destination":"hooks.slack.com"}',
    ))
    assert out == {"ok": True}
    # graph is PINNED — the impl always gets the bound universe, never a caller value.
    assert cap["universe_id"] == "u-pinned"
    assert cap["action"] == "approve"
    assert cap["payload"] == {
        "channel_type": "authenticated_external_call",
        "destination": "hooks.slack.com",
    }
    # least-privilege: a pure WRITE, never submit_request.
    assert cap["caps"] == {"write"}


def test_source_channel_is_in_served_tool_surface():
    """Served = in a default turn's backend grant, reached through ``ta`` in bash;
    the model's own inventory is the four tools."""
    from types import SimpleNamespace

    from tinyassets.served_tools import granted_tools, model_tools

    default_turn = SimpleNamespace(engine_tool_grant=None)
    assert "source_channel" in granted_tools(default_turn)
    assert "bash" in model_tools(default_turn)

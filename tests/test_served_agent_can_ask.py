"""The served agent may ASK its user for something — and never answer itself.

Live test 2026-08-27: the founder asked the universe to ship a change, and it
answered honestly that `write_graph target="connection" operation="request_from_user"`
*"is not exposed to me in this runtime"*. It was right. The served `write_graph`
is branch-only by design, so the ask primitive built FOR the agent was
unreachable BY the agent.

This pins the narrow carve-out that fixes it, and the asymmetry that makes the
carve-out safe: asking writes no credential and grants nothing, while answering
would let the agent satisfy its own ask — it runs as the user's own principal,
so the only real boundary is not exposing it here.
"""

from __future__ import annotations

import inspect
import json


def _write_graph_source() -> str:
    from tinyassets import engine_mcp_server as e

    fn = getattr(e.write_graph, "fn", e.write_graph)
    return inspect.getsource(fn)


def test_the_agent_can_read_what_it_asked_for():
    from tinyassets import engine_mcp_server as e

    assert "pending_requests" in e._PINNED_READ_TARGETS


def test_the_agent_can_raise_an_ask():
    src = _write_graph_source()
    assert 'if t == "pending_request":' in src
    assert "from tinyassets.api.pending_requests import request_from_user" in src


def test_the_agent_cannot_answer_its_own_ask_or_lift_a_mute():
    """The asymmetry IS the security property.

    The agent authenticates as the user's own principal, so an exposed
    answer_request would let it satisfy its own credential ask, and an exposed
    unmute_request would let it lift a mute the user set. Neither is importable
    on this surface, and the refusal names why.
    """
    src = _write_graph_source()
    # Naming them in a comment that explains WHY they are absent is fine; what
    # must not exist on this surface is an import or a call.
    for banned in ("answer_request", "unmute_request"):
        assert f"import {banned}" not in src, banned
        assert f"{banned}(" not in src, banned
    assert "operation='ask' or" in src
    assert "'withdraw' (your own stale ask)" in src


def test_an_unknown_pending_request_operation_is_refused(monkeypatch):
    from tinyassets import engine_mcp_server as e

    monkeypatch.setattr(e, "_binding_error", lambda: None)
    monkeypatch.setattr(e, "_GRAPH_ID", "u-1", raising=False)

    fn = getattr(e.write_graph, "fn", e.write_graph)
    out = json.loads(fn(target="pending_request", operation="answer_request",
                        payload_json="{}"))
    assert "error" in out
    assert "not to you" in out["error"]

"""A single tool result can no longer end a turn by not fitting in the model.

Live 2026-09-26, turn ``8dc8ada56b8e4d1cbfd2e4f37a111e7d`` (free-account
universe): the owner asked their universe to build a custom UI, the agent called
``read_graph target="model_options"``, and the server returned **1,274,067
bytes**. The selected model could not fit it, the turn was abandoned after five
rounds, and the notice said "we could not identify why" about a cause the router
had measured itself.

These tests drive the REAL dispatch path -- ``mcp.call_tool``, the middleware
included -- because calling the handler function directly is exactly the route
that has no ceiling on it, and a test that took that route would pass while the
live surface stayed broken.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tests.engine_authority_helpers import mock_engine_admission


def _bind(monkeypatch, payload: str):
    """Pin the engine server to a founder + universe and fix what a read returns."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", "sub-1")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-pinned")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    monkeypatch.setattr(us, "read_graph", lambda **kw: payload)
    # These tests measure the ceiling against the handler's exact bytes; the
    # owner_unread field is tested in test_engine_conversation_attention.
    monkeypatch.setattr("tinyassets.engine_conversation_attention._observe",
                        lambda text, reading: None)
    return s


def _text(result) -> str:
    assert result.content, "a tool result with no content block reaches no model"
    return result.content[0].text


# ── the ceiling ─────────────────────────────────────────────────────────────

def test_oversized_tool_result_is_capped_and_says_so(monkeypatch):
    """Over the ceiling: a marker, the original size, and how to ask for less."""
    from tinyassets import engine_result_bounds as bounds

    huge = json.dumps({"options": ["provider/model-" + str(i) for i in range(60_000)]})
    assert len(huge.encode()) > 1_000_000, "the regression needs a live-sized payload"
    s = _bind(monkeypatch, huge)
    monkeypatch.delenv(bounds.CEILING_ENV, raising=False)
    monkeypatch.delenv(bounds.CONTEXT_TOKENS_ENV, raising=False)

    result = asyncio.run(s.mcp.call_tool("read_graph", {"target": "graph"}))
    text = _text(result)

    assert len(text.encode()) <= bounds.DEFAULT_CEILING_BYTES
    marker = json.loads(text)
    assert marker["truncated"] is True
    assert marker["tool"] == "read_graph"
    assert marker["original_bytes"] == len(huge.encode())
    assert marker["ceiling_bytes"] == bounds.DEFAULT_CEILING_BYTES
    assert 0 < marker["returned_bytes"] < marker["original_bytes"]
    # The hint has to name THIS tool's own narrowing parameters, or the agent
    # learns only that it failed.
    assert "query=" in marker["hint"] and "output_offset" in marker["hint"]
    assert huge.startswith(marker["content"]), "the surviving head must be verbatim"


def test_the_cap_comes_from_the_dispatch_layer_not_the_handler(monkeypatch):
    """Mutation check: without the middleware the same read is unbounded.

    This is the assertion that can go red. If the ceiling ever moves into a
    handler, or the middleware stops being registered, the direct call below
    starts agreeing with the dispatched one and this fails.
    """
    huge = json.dumps({"rows": ["x" * 64 for _ in range(2_000)]})
    s = _bind(monkeypatch, huge)

    direct = s.read_graph(target="graph")
    dispatched = _text(asyncio.run(s.mcp.call_tool("read_graph", {"target": "graph"})))

    assert direct == huge, "the handler itself must stay a faithful read"
    assert dispatched != direct
    assert json.loads(dispatched)["truncated"] is True


def test_structured_content_is_capped_alongside_the_text(monkeypatch):
    """A client reading the structured half must not get the megabyte back."""
    huge = json.dumps({"rows": ["y" * 64 for _ in range(2_000)]})
    s = _bind(monkeypatch, huge)

    result = asyncio.run(s.mcp.call_tool("read_graph", {"target": "graph"}))
    structured = result.structured_content

    assert isinstance(structured, dict)
    rendered = json.dumps(structured)
    assert len(rendered.encode()) < len(huge.encode())
    assert huge not in rendered


def test_the_ceiling_holds_over_the_real_loopback_http_route(monkeypatch):
    """The transport production actually uses, not just the in-process object.

    ``engine_mcp_http`` serves this server over loopback streamable-HTTP, and a
    middleware that only fires for an in-process ``call_tool`` would leave the
    live surface exactly as broken as it was. This drives ``mcp.http_app()``
    through a real MCP client over the real protocol -- ASGI rather than a
    subprocess and a port, so it stays a unit test -- with the Starlette lifespan
    running, which the session manager requires.
    """
    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from tinyassets import engine_result_bounds as bounds

    huge = json.dumps({"rows": ["x" * 64 for _ in range(4_000)]})
    s = _bind(monkeypatch, huge)
    monkeypatch.delenv(bounds.CEILING_ENV, raising=False)
    monkeypatch.delenv(bounds.CONTEXT_TOKENS_ENV, raising=False)
    app = s.mcp.http_app()

    def asgi_client(**_ignored):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://engine", timeout=30.0,
        )

    async def drive() -> str:
        async with app.router.lifespan_context(app):
            transport = StreamableHttpTransport(
                "http://engine/mcp", httpx_client_factory=asgi_client,
            )
            async with Client(transport) as client:
                result = await client.call_tool("read_graph", {"target": "graph"})
                return result.content[0].text

    text = asyncio.run(drive())
    marker = json.loads(text)
    assert marker["truncated"] is True
    assert marker["original_bytes"] == len(huge.encode())
    assert len(text.encode()) <= bounds.DEFAULT_CEILING_BYTES


def test_the_capped_result_uses_the_budget_it_was_given(monkeypatch):
    """A regression guard on the fitting loop, not on the ceiling.

    The first cut of this returned 12,875 of an allowed 24,576 bytes, because
    ``returned_bytes`` was absent when the marker overhead was measured, so the
    first render overshot and the fallback halved the head. A ceiling that
    silently gives back half of what it allows throws away context the model had.
    """
    from tinyassets import engine_result_bounds as bounds

    limit = bounds.DEFAULT_CEILING_BYTES
    rendered = bound = bounds.bound_tool_text("x" * 500_000, tool="read", limit=limit)
    assert bound is not None
    size = len(rendered.encode())
    assert size <= limit
    # Within a hair of the ceiling: overhead is the marker fields, nothing else.
    assert size > limit - 600, f"used only {size} of {limit}"


def test_an_exact_byte_read_is_not_truncated(monkeypatch):
    """A ceiling is the wrong tool for a read whose contract is exact bytes.

    REGRESSION, measured 2026-09-26 on the real dispatch path before this guard
    existed: a 256 KB owned run-file chunk (a 349,681-byte reply) came back as a
    24,576-byte marker carrying neither `bytes_base64` nor `next_offset`. The agent
    lost the bytes AND the cursor that would have let it page, so a file its owner
    uploaded in the app became unreadable to their own universe -- worse, for that
    read, than the overflow this module exists to prevent.

    `file_max_bytes` (default 524288, maximum 1048576) is the parameter that bounds
    this read. A byte ceiling layered over it only breaks it.
    """
    import base64

    payload = json.dumps({
        "file_id": "f-1",
        "bytes_base64": base64.b64encode(b"\x00\xff" * 131_072).decode(),
        "next_offset": 262_144,
        "eof": False,
    })
    s = _bind(monkeypatch, payload)
    assert len(payload.encode()) > 300_000, "the regression needs a real chunk size"

    delivered = _text(asyncio.run(s.mcp.call_tool(
        "read_graph", {"target": "run_file", "run_id": "r-1", "file_id": "f-1"},
    )))

    # A run's bytes are generated content, so they ride inside the untrusted
    # envelope; what matters is that the envelope carries the payload WHOLE.
    envelope = json.loads(delivered)
    assert envelope["untrusted"] is True
    assert "truncated" not in envelope
    parsed = envelope["content"]
    assert parsed == json.loads(payload), "exact bytes must survive dispatch whole"
    # The cursor is the part whose loss made this unrecoverable rather than merely
    # partial: without it the agent cannot ask for the next chunk.
    assert parsed["next_offset"] == 262_144
    assert len(parsed["bytes_base64"]) == len(json.loads(payload)["bytes_base64"])


def test_the_exemption_is_a_contract_not_a_size_escape(monkeypatch):
    """Only the named (tool, target) pair is exempt; nothing else inherits it."""
    from tinyassets.engine_result_bounds import EXACT_BYTE_READS, ceiling_exempt

    assert EXACT_BYTE_READS == {("read_graph", "run_file")}
    assert ceiling_exempt("read_graph", {"target": "run_file"})
    assert ceiling_exempt("read_graph", {"target": "  RUN_FILE  "})
    # A sibling target of the same tool is NOT exempt.
    assert not ceiling_exempt("read_graph", {"target": "run_file_limits"})
    assert not ceiling_exempt("read_graph", {"target": "model_options"})
    # Another tool cannot borrow it, and an unreadable call never escapes the
    # ceiling -- defaulting to exempt would let anything unparseable through.
    assert not ceiling_exempt("bash", {"target": "run_file"})
    for unreadable in (None, "run_file", {}, {"target": None}, {"target": 7}):
        assert not ceiling_exempt("read_graph", unreadable), unreadable


def test_a_sibling_of_an_exempt_target_is_still_bounded(monkeypatch):
    """Proves the exemption is keyed on the target, through the real dispatch."""
    huge = json.dumps({"limits": ["x" * 64 for _ in range(2_000)]})
    s = _bind(monkeypatch, huge)

    delivered = _text(asyncio.run(s.mcp.call_tool(
        "read_graph", {"target": "run_file_limits"},
    )))
    assert json.loads(delivered)["truncated"] is True


def test_a_result_within_the_ceiling_is_returned_byte_for_byte(monkeypatch):
    """Bounding is not reformatting: a result that fits is never rewritten."""
    payload = json.dumps({"command_center_id": "u-pinned", "phase": "running"})
    s = _bind(monkeypatch, payload)

    assert _text(asyncio.run(s.mcp.call_tool("read_graph", {"target": "graph"}))) == payload


def test_truncation_is_never_silent_even_when_no_content_survives():
    """A ceiling too small for any head still reports that it truncated."""
    from tinyassets.engine_result_bounds import bound_tool_text

    rendered = bound_tool_text("z" * 5_000, tool="read", limit=32)
    marker = json.loads(rendered)
    assert marker["truncated"] is True
    assert marker["returned_bytes"] == 0
    assert marker["original_bytes"] == 5_000


def test_the_envelope_itself_respects_the_ceiling_with_escape_heavy_text():
    """JSON escaping must not push the marker back over the limit."""
    from tinyassets.engine_result_bounds import bound_tool_text

    nasty = ('{"a":"' + "\\\"\n\t" * 4_000 + '"}')
    limit = 4_096
    rendered = bound_tool_text(nasty, tool="bash", limit=limit)
    assert len(rendered.encode()) <= limit
    assert json.loads(rendered)["truncated"] is True


def test_bound_tool_text_leaves_a_fitting_result_alone():
    from tinyassets.engine_result_bounds import bound_tool_text

    assert bound_tool_text("small", tool="read", limit=1_000) is None
    assert bound_tool_text(None, tool="read", limit=1_000) is None


@pytest.mark.parametrize(
    ("context_tokens", "expected"),
    [
        (None, "default"),
        (0, "default"),
        (True, "default"),          # a bool is not a token count
        ("200000", "default"),      # nor is a string
        (1_000, "floor"),           # tiny window clamps up to the floor
        (200_000, "scaled"),
        (10_000_000, "cap"),        # a huge window still cannot claim a megabyte
    ],
)
def test_the_ceiling_scales_with_the_selected_model_or_falls_back(context_tokens, expected):
    from tinyassets import engine_result_bounds as bounds

    got = bounds.ceiling_for_context(context_tokens)
    if expected == "default":
        assert got == bounds.DEFAULT_CEILING_BYTES
    elif expected == "floor":
        assert got == bounds.MIN_CEILING_BYTES
    elif expected == "cap":
        assert got == bounds.MAX_CEILING_BYTES
    else:
        assert bounds.MIN_CEILING_BYTES < got < bounds.MAX_CEILING_BYTES
        assert got == int(200_000 * bounds.BYTES_PER_TOKEN * bounds.CONTEXT_SHARE)


def test_resolve_ceiling_prefers_the_override_then_the_window_then_the_default():
    from tinyassets import engine_result_bounds as bounds

    env = {bounds.CEILING_ENV: "8192", bounds.CONTEXT_TOKENS_ENV: "200000"}
    assert bounds.resolve_ceiling(env) == 8_192
    assert bounds.resolve_ceiling({bounds.CONTEXT_TOKENS_ENV: "200000"}) == (
        bounds.ceiling_for_context(200_000)
    )
    assert bounds.resolve_ceiling({}) == bounds.DEFAULT_CEILING_BYTES
    # Junk is not authority to be unbounded.
    for junk in ("", "  ", "lots", "-5", "0"):
        assert bounds.resolve_ceiling({bounds.CEILING_ENV: junk}) == (
            bounds.DEFAULT_CEILING_BYTES
        )
        assert bounds.resolve_ceiling({bounds.CONTEXT_TOKENS_ENV: junk}) == (
            bounds.DEFAULT_CEILING_BYTES
        )


def test_the_engine_turn_passes_the_selected_window_to_its_server(monkeypatch, tmp_path):
    """The scaling input has to actually reach the server, or it is decoration."""
    from types import SimpleNamespace

    from tinyassets.engine_result_bounds import CONTEXT_TOKENS_ENV
    from tinyassets.providers import claude_provider

    captured: dict = {}
    monkeypatch.setattr(
        claude_provider, "read_engine_mcp_route", lambda **kw: None, raising=False,
    )
    monkeypatch.setattr(claude_provider, "data_dir", lambda: tmp_path, raising=False)

    config = SimpleNamespace(
        engine_mcp_actor_id="sub-1", engine_mcp_graph_id="u-pinned",
        selected_model=SimpleNamespace(context_tokens=128_000),
    )
    claude_provider._engine_mcp_flags(config, tmp_path)
    written = json.loads(
        (tmp_path / ".runtime" / "engine-mcp-config.json").read_text(encoding="utf-8")
    )
    captured = written["mcpServers"]["tinyassets"]["env"]
    assert captured[CONTEXT_TOKENS_ENV] == "128000"

    config.selected_model = None
    claude_provider._engine_mcp_flags(config, tmp_path)
    written = json.loads(
        (tmp_path / ".runtime" / "engine-mcp-config.json").read_text(encoding="utf-8")
    )
    assert CONTEXT_TOKENS_ENV not in written["mcpServers"]["tinyassets"]["env"]

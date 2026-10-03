"""Regression tests for direct wrappers vs MCP structured tool results."""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Isolate the data dir so a blank-scope ``get_status`` resolves the
    first-contact path deterministically instead of the developer's ambient
    ``TINYASSETS_DATA_DIR`` (which, under the universe-visibility contract, holds
    an undeclared universe that now fails closed). These tests assert the MCP
    result CONTRACT, not visibility — an empty data dir gives them a clean,
    reproducible first-contact payload."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


def test_direct_wrappers_keep_json_string_contract() -> None:
    """Local callers still import wrappers directly and json.loads the result."""
    from tinyassets import universe_server as us

    status_raw = us.get_status()
    wiki_raw = us.wiki(action="list")

    assert isinstance(status_raw, str)
    assert isinstance(wiki_raw, str)
    assert json.loads(status_raw)["schema_version"] == 3
    assert "promoted" in json.loads(wiki_raw)


def test_mcp_tool_result_has_structured_content_and_text_content() -> None:
    """ChatGPT/Apps SDK needs structuredContent without losing text content.

    The text block must carry the real payload. When the payload fits the
    budget it is faithful JSON (parseable); when oversized it is real truncated
    data with a pointer to structuredContent. Either way the payload's data is
    present in text, never replaced by a placeholder stub.
    """
    from tinyassets import universe_server as us

    async def _call_status():
        return await us.mcp.call_tool("get_status", {"command_center_id": ""})

    result = asyncio.run(_call_status())

    assert isinstance(result.structured_content, dict)
    assert result.structured_content["schema_version"] == 3
    assert result.content
    assert result.content[0].type == "text"
    text = result.content[0].text
    # Real payload data is present in the text channel (not a placeholder).
    assert "schema_version" in text
    if "[truncated:" not in text:
        # Small payloads stay fully faithful and parseable.
        assert json.loads(text)["schema_version"] == 3
    else:
        # Oversized payloads carry real leading data + a pointer to the rest.
        assert "structuredContent" in text
        assert len(text) <= us._MCP_TEXT_CONTENT_MAX_CHARS


def test_mcp_tool_large_result_keeps_full_structured_content_with_bounded_text(
    monkeypatch,
) -> None:
    """Large results stay bounded in text but must carry REAL data, not a stub.

    Text-only MCP clients read only the ``content`` text block. The prior
    contract replaced oversized payloads with a <500-char key-count stub, which
    made reads silently look empty to those clients. New contract: full payload
    in ``structuredContent``, and the text block carries real leading data
    bounded to the text budget with an explicit pointer to ``structuredContent``
    for the elided remainder.
    """
    from tinyassets import universe_server as us

    claims = [
        {
            "claim_id": f"c-{idx}",
            "branch_def_id": f"b-{idx}",
            "goal_id": "4ff5862cc26d",
            "rung_key": "learned_failure",
            "evidence_note": "x" * 1000,
        }
        for idx in range(12)
    ]

    def _large_goals_result(**_kwargs):
        return json.dumps({
            "status": "ok",
            "goal_id": "4ff5862cc26d",
            "claims": claims,
            "count": len(claims),
        })

    # Any canonical handle whose result is large exercises the bounding; the
    # retired `gates` tool this used to call is no longer registered.
    from tinyassets.api import graph_reads

    monkeypatch.setattr(graph_reads, "_goals_impl", _large_goals_result)

    async def _call_read_graph():
        return await us.mcp.call_tool("read_graph", {"target": "goals"})

    result = asyncio.run(_call_read_graph())

    assert result.structured_content["claims"] == claims
    assert result.structured_content["count"] == 12
    text = result.content[0].text
    # structuredContent keeps the full payload (above). The text block must
    # now carry REAL leading data + an explicit pointer to the remainder,
    # bounded to the budget — not a lossy placeholder.
    assert "structuredContent" in text  # pointer to the full payload
    assert "truncated" in text  # explicit elision marker
    assert "c-0" in text  # real leading data is present, not a stub
    assert len(text) <= us._MCP_TEXT_CONTENT_MAX_CHARS

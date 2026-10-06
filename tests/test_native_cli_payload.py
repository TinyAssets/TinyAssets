"""Payload accounting cannot hide wrapped or additional native tools."""

import json

from scripts.native_cli_payload import EXPECTED_MCP_TOOLS, exposure_check, payload_metrics


def test_http_function_names_and_full_wire_size():
    body = {"tools": [{"type": "function", "function": {
        "name": "read", "parameters": {"type": "object"},
    }}], "messages": [{"role": "system", "content": "same instructions"},
                       {"role": "user", "content": "café"}]}
    measured = payload_metrics(body)
    compact = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    assert measured["tool_names"] == ["read"]
    assert measured["wire_chars"] == len(compact)
    assert measured["wire_utf8_bytes"] == len(compact.encode())
    assert measured["wire_utf8_bytes"] > measured["wire_chars"]


def test_native_additional_tools_are_included():
    body = {"tools": [{"name": "mcp__tinyassets__read"}], "input": [
        {"type": "additional_tools", "tools": [
            {"type": "namespace", "name": "functions", "tools": [
                {"name": "apply_patch", "parameters": {"type": "object"}},
            ]},
        ]},
        {"role": "developer", "content": "native additions"},
    ]}
    measured = payload_metrics(body)
    assert measured["tool_names"] == ["mcp__tinyassets__read", "functions.apply_patch"]
    assert measured["instruction_chars"] == len(json.dumps("native additions"))
    assert measured["resident_chars"] == (
        measured["instruction_chars"] + measured["tool_schema_chars"])


def test_no_tools_is_not_a_four_tool_inventory():
    measured = payload_metrics({"system": "mcp__tinyassets__read", "messages": []})
    assert measured["tool_names"] == []
    assert measured["tool_schema_chars"] == 2


def test_budget_overflow_is_reported_without_truncation():
    measured = payload_metrics({"system": "x" * 5000})
    assert measured["instruction_chars"] == 5000
    assert measured["resident_chars"] == 5002
    assert measured["over_budget_chars"] == 1002
    assert measured["estimated_tokens_chars_div_4"] == 1250.5


def test_superset_and_overflow_never_pass_capture_check():
    tools = [{"name": name} for name in EXPECTED_MCP_TOOLS]
    superset = exposure_check(payload_metrics({"tools": tools + [{"name": "apply_patch"}]}))
    assert superset["complete_agent_payload"] is False
    assert superset["missing_mcp_definitions"] == []
    assert superset["extra_tools"] == ["apply_patch"]
    oversized = exposure_check(payload_metrics({"tools": tools, "system": "x" * 5000}))
    assert oversized["complete_agent_payload"] is False
    assert oversized["extra_tools"] == []


def test_capture_check_is_not_production_parity():
    exact = exposure_check(payload_metrics({"tools": [{"name": n} for n in EXPECTED_MCP_TOOLS]}))
    assert exact["complete_agent_payload"] is True
    assert exact["production_parity_proven"] is False
    missing = exposure_check(payload_metrics({"system": " ".join(EXPECTED_MCP_TOOLS)}))
    assert missing["complete_agent_payload"] is False
    assert set(missing["missing_mcp_definitions"]) == EXPECTED_MCP_TOOLS

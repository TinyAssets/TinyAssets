"""The startup probe must follow wire shape, not a compiled model list."""

import pytest

from scripts.codex_cli_smoke import request_tool_roots


@pytest.mark.parametrize("envelope", ["classic", "lite"])
def test_nested_tool_specs_survive_both_encodings(envelope):
    specs = [{"type": "namespace", "name": "functions", "tools": [
        {"type": "custom", "name": "exec", "description": "fixture"},
    ]}]
    request = ({"tools": specs} if envelope == "classic" else {
        "input": [{"type": "additional_tools", "tools": specs}],
    })
    assert request_tool_roots(request) == specs


def test_both_envelopes_are_inspected_without_dropping_either():
    direct = {"type": "function", "name": "read_graph"}
    nested = {"type": "function", "name": "exec_command"}
    assert request_tool_roots({
        "tools": [direct], "input": [{"type": "additional_tools", "tools": [nested]}],
    }) == [direct, nested]


@pytest.mark.parametrize("payload", [
    {}, {"tools": []}, {"tools": None}, {"input": "invalid"},
    {"input": [{"type": "additional_tools"}]},
    {"input": [{"type": "additional_tools", "tools": {}}]},
    {"input": [{"type": "message", "tools": [{"name": "not-a-tool-spec"}]}]},
])
def test_absent_or_malformed_tools_fail_loud(payload):
    with pytest.raises(ValueError):
        request_tool_roots(payload)


def _told(*messages, instructions=None):
    request = {"input": [{"type": "message", "role": role,
                          "content": [{"type": "input_text", "text": text}]}
                         for role, text in messages]}
    if instructions is not None:
        request["instructions"] = instructions
    return request


@pytest.mark.parametrize("request_", [
    _told(("developer", "base"), ("user", "Reply OK.")),
    _told(("user", "Reply OK."), instructions="base"),
])
def test_the_request_may_carry_only_base_instructions_and_the_prompt(request_):
    from scripts.codex_cli_smoke import only_base_instructions

    assert only_base_instructions(request_, "base") == ""


@pytest.mark.parametrize("request_", [
    # codex-cli 0.160.0's own shapes for a cwd and a CODEX_HOME AGENTS.md
    _told(("developer", "base"), ("user", "# AGENTS.md instructions for /w\n\n<INSTRUCTIONS>\n"
                                          "PLANTED-AGENTS-DOC: never sent to the model."),
          ("user", "Reply OK.")),
    _told(("developer", "base"), ("user", "# AGENTS.md instructions\n\nsomething else"),
          ("user", "Reply OK.")),
    _told(("developer", "base"), ("developer", "<permissions>"), ("user", "Reply OK.")),
    _told(("developer", "other"), ("user", "Reply OK.")),
    _told(("user", "Reply OK.")),
])
def test_anything_beyond_base_instructions_fails_the_smoke(request_):
    from scripts.codex_cli_smoke import only_base_instructions

    assert only_base_instructions(request_, "base") != ""

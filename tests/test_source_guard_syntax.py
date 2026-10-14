"""Source pre-checks retain raw substring rules outside literals and comments."""
import tokenize

import pytest

from tinyassets.graph_compiler import (
    _source_without_literals,
    source_code_problems,
)
from tinyassets.node_sandbox import NodeSandbox


def test_wrapper_keeps_its_narrower_policy():
    assert source_code_problems("open('file')", "n") == []
    assert source_code_problems("os.system('cmd')", "n")




@pytest.mark.parametrize("source", [
    'text = "é open(path)"; open(path) # eval(code)\r\nexec(code)',
    'text = """open(path)\nsubprocess\n"""; eval(code)\n',
    'text = "open(path)\vsubprocess"\nopen(path)',
    'text = f"open(path) {value:open(}"; eval(code)',
])
def test_mask_preserves_offsets_and_line_endings(source):
    masked = _source_without_literals(source)
    assert len(masked) == len(source)
    for i, char in enumerate(source):
        if char in "\r\n":
            assert masked[i] == char
    if ";" in source and (hasattr(tokenize, "FSTRING_START") or 'text = f"' not in source):
        assert "open(path)" not in masked[:source.index(";")]
    for pattern in ("open(path)", "eval(code)", "exec(code)"):
        if pattern in masked:
            start = masked.index(pattern)
            assert source[start:start + len(pattern)] == pattern


def test_fstring_expression_matches_the_interpreters_token_boundaries():
    source = 'text = f"open(path) {open(path)}"'
    # Python 3.11 must retain the whole STRING, including executable fields.
    assert NodeSandbox().validate_source(source) == ["Forbidden pattern: 'open('"]






def test_legacy_compile_value_error_is_reported(monkeypatch):
    from tinyassets import graph_compiler, node_sandbox

    def null_byte_compile(*args, **kwargs):
        raise ValueError("source code cannot contain null bytes")

    for module in (graph_compiler, node_sandbox):
        monkeypatch.setattr(module, "compile", null_byte_compile, raising=False)
    assert "null bytes" in " ".join(NodeSandbox().validate_source("pass"))
    assert "null bytes" in " ".join(source_code_problems("pass", "n"))






def test_compiler_exhaustion_returns_diagnostics(monkeypatch):
    from tinyassets import graph_compiler, node_sandbox

    def exhausted(*args, **kwargs):
        raise RecursionError("too deeply nested")

    monkeypatch.setattr(graph_compiler, "compile", exhausted, raising=False)
    monkeypatch.setattr(node_sandbox, "compile", exhausted, raising=False)
    assert "too deeply nested" in " ".join(NodeSandbox().validate_source("x = 1"))
    assert "too deeply nested" in " ".join(source_code_problems("x = 1", "n"))

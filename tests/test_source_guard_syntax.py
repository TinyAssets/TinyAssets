"""Source pre-checks retain raw substring rules outside literals and comments."""
import tokenize
from types import SimpleNamespace

import pytest

from tinyassets.executors.node_bid import _scan_dangerous_patterns
from tinyassets.graph_compiler import (
    _BID_DANGEROUS_PATTERNS,
    _DANGEROUS_PATTERNS,
    _source_without_literals,
    dangerous_source_patterns,
    source_code_problems,
)
from tinyassets.node_sandbox import FORBIDDEN_PATTERNS, NodeSandbox
from tinyassets.producers.node_bid import _producer_sandbox_reject


@pytest.mark.parametrize("source", [
    'def run(s): return {"text": "open(path)"}',
    '# open(path)\ndef run(s): return {}',
    'def run(s):\n    return {"text": "open(path), eval(code), subprocess, pickle"}',
    '# open(path) exec(code) os.system(cmd) importlib\ndef run(s): return {}',
    'def run(s):\n    """compile(code), __import__, marshal"""\n    return {}',
    'print("open(path), vars(), timeit.timeit(code), getattr(obj, name)")',
    'text = f"open(path) {value}"',
    'text = f"open(path) {value:open(}"',
    'text = f"open(path) {f\'open(path) {value}\'}"',
    'text = "open(path)"\ndef run(s): return {"text": text}',
])
def test_prose_is_allowed_at_every_source_boundary(source):
    if not hasattr(tokenize, "FSTRING_START") and 'text = f"' in source:
        assert_original_scan_results(source)
        return
    node = SimpleNamespace(approved=True, source_code=source)
    assert NodeSandbox().validate_source(source) == []
    assert source_code_problems(source, "n") == []
    assert _scan_dangerous_patterns(source) == ""
    assert _producer_sandbox_reject("n", lambda _: node) == ""


def assert_original_scan_results(source):
    """For code without literal/comment matches, preserve each caller's policy."""
    node = SimpleNamespace(approved=True, source_code=source)
    expected = [p for p in _BID_DANGEROUS_PATTERNS if p in source]
    assert _scan_dangerous_patterns(source) == (expected[0] if expected else "")
    assert _producer_sandbox_reject("n", lambda _: node) == (
        f"dangerous_pattern:{expected[0]}" if expected else ""
    )
    assert source_code_problems(source, "n") == [
        f"Node 'n' source_code contains disallowed pattern: '{p}'"
        for p in _DANGEROUS_PATTERNS if p in source
    ]
    assert NodeSandbox().validate_source(source) == [
        f"Forbidden pattern: '{p}'" for p in FORBIDDEN_PATTERNS if p in source
    ]


@pytest.mark.parametrize("pattern", sorted(set(FORBIDDEN_PATTERNS) | set(_BID_DANGEROUS_PATTERNS)))
def test_every_original_pattern_at_every_boundary(pattern):
    source = pattern + ")" if pattern.endswith("(") else pattern
    assert_original_scan_results(source)


@pytest.mark.parametrize("source", [
    "is_open = True", "retrieval = []", "super().__init__()", "x.__class__",
    "code = s['code']; code.strip()", "profile.get('name')", "trace.append(1)",
    "from types import SimpleNamespace", "self.modules",
    "d.get('k')", "obj.method()",
])
def test_round_two_ordinary_code_passes_original_scan(source):
    assert not any(p in source for p in (*FORBIDDEN_PATTERNS, *_BID_DANGEROUS_PATTERNS))
    assert_original_scan_results(source)


@pytest.mark.parametrize("source", [
    "open('secret')", "open ('secret')", "(open)('secret')",
    "obj.open('secret')", "obj.open ('secret')", "compile('x', 'x', 'exec')",
    "eval ('1')", "exec ('pass')", "__import__('os')", "os.system ('cmd')",
    "import subprocess as sp", "from pickle import loads", "import marshal",
    "import importlib", "is_open()", "retrieval()", "subprocess_result = 1",
    "os . system('cmd')", "from os import system", "reader = open",
])
def test_original_substring_boundaries_and_policy_differences(source):
    assert_original_scan_results(source)


def test_wrapper_keeps_its_narrower_policy():
    assert source_code_problems("open('file')", "n") == []
    assert source_code_problems("os.system('cmd')", "n")


@pytest.mark.parametrize("source", [
    'text = "open(path)"\n( # subprocess',
    'text = "open(path)"\n"""unfinished subprocess',
    "text = 'open(path)",
    'if True:\n    text = "open(path)"\n  pass',
])
def test_invalid_tokenization_falls_back_to_unmodified_raw_scan(source):
    assert _source_without_literals(source) == source
    for patterns in (_BID_DANGEROUS_PATTERNS, _DANGEROUS_PATTERNS, tuple(FORBIDDEN_PATTERNS)):
        assert dangerous_source_patterns(source, patterns) == [p for p in patterns if p in source]
    node = SimpleNamespace(approved=True, source_code=source)
    expected = next(p for p in _BID_DANGEROUS_PATTERNS if p in source)
    assert _scan_dangerous_patterns(source) == expected
    assert _producer_sandbox_reject("n", lambda _: node) == f"dangerous_pattern:{expected}"
    assert any("Forbidden pattern" in p for p in NodeSandbox().validate_source(source))
    assert any("does not parse" in p for p in source_code_problems(source, "n"))


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
    assert _scan_dangerous_patterns(source) == "open("
    assert NodeSandbox().validate_source(source) == ["Forbidden pattern: 'open('"]


@pytest.mark.parametrize("source", [
    'x = f"{os.system(\'id\')}"',
    'f"{eval(\'1+1\')}"',
    'f"{1:{exec(\'pass\')}}"',
    'rf"{__import__(\'os\')}"',
    'x = F"{os.system(\'id\')}"',
    'Fr"{__import__(\'os\')}"',
    '# c\ros.system("id")\n',
    '# c\r\nos.system("id")\n',
    'text = "safe"\r# c\ros.system("id")\r\n',
])
def test_executable_patterns_cannot_be_hidden_by_literal_or_comment_tokens(source):
    compile(source, "<source-guard-regression>", "exec")
    assert_original_scan_results(source)
    assert _scan_dangerous_patterns(source)
    assert NodeSandbox().validate_source(source)


def test_null_bytes_return_rejections_at_every_boundary():
    source = 'def run(s): return {"text": "\x00"}'
    node = SimpleNamespace(approved=True, source_code=source)
    assert "null bytes" in " ".join(NodeSandbox().validate_source(source))
    assert "null bytes" in " ".join(source_code_problems(source, "n"))
    assert _scan_dangerous_patterns(source) == "invalid_syntax"
    assert _producer_sandbox_reject("n", lambda _: node) == "invalid_syntax"


def test_legacy_compile_value_error_is_reported(monkeypatch):
    from tinyassets import graph_compiler, node_sandbox

    def null_byte_compile(*args, **kwargs):
        raise ValueError("source code cannot contain null bytes")

    for module in (graph_compiler, node_sandbox):
        monkeypatch.setattr(module, "compile", null_byte_compile, raising=False)
    assert "null bytes" in " ".join(NodeSandbox().validate_source("pass"))
    assert "null bytes" in " ".join(source_code_problems("pass", "n"))


@pytest.mark.parametrize("source", [
    "profile = state['profile']; profile.get('name')",
    "code = 'text'; code.strip()", "trace = []; trace.append(1)",
    "types = 'a'; types.count('a')", "course.modules",
    "retrieval = 1", "is_open = True", "recompile = 1",
    "class Child(Base):\n    def __init__(self): super().__init__()",
])
def test_ordinary_names_and_constructors_remain_allowed(source):
    test_prose_is_allowed_at_every_source_boundary(source)


def test_deep_attribute_source_does_not_escape_validation():
    source = "x = a" + ".a" * 3000
    node = SimpleNamespace(approved=True, source_code=source)
    # Interpreter versions differ in whether this valid expression exceeds
    # their parser/compiler depth. Either validation result must be returned.
    assert isinstance(NodeSandbox().validate_source(source), list)
    assert isinstance(source_code_problems(source, "n"), list)
    assert isinstance(_scan_dangerous_patterns(source), str)
    assert isinstance(_producer_sandbox_reject("n", lambda _: node), str)


def test_compiler_exhaustion_returns_diagnostics(monkeypatch):
    from tinyassets import graph_compiler, node_sandbox

    def exhausted(*args, **kwargs):
        raise RecursionError("too deeply nested")

    monkeypatch.setattr(graph_compiler, "compile", exhausted, raising=False)
    monkeypatch.setattr(node_sandbox, "compile", exhausted, raising=False)
    assert "too deeply nested" in " ".join(NodeSandbox().validate_source("x = 1"))
    assert "too deeply nested" in " ".join(source_code_problems("x = 1", "n"))

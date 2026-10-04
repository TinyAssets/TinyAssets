"""Source guards judge Python operations, not prose embedded in a workflow."""
from types import SimpleNamespace

import pytest

from tinyassets.executors.node_bid import _scan_dangerous_patterns
from tinyassets.graph_compiler import source_code_problems
from tinyassets.node_sandbox import NodeSandbox
from tinyassets.producers.node_bid import _producer_sandbox_reject


@pytest.mark.parametrize("source", [
    'def run(s): return {"text": "open(path)"}',
    '# open(path)\ndef run(s): return {}',
    'def run(s):\n    return {"text": "open(path), eval(code), subprocess, pickle"}',
    '# open(path) exec(code) os.system(cmd) importlib\ndef run(s): return {}',
    'def run(s):\n    """compile(code), __import__, marshal"""\n    return {}',
])
def test_prose_is_allowed_at_every_source_boundary(source):
    node = SimpleNamespace(approved=True, source_code=source)
    assert NodeSandbox().validate_source(source) == []
    assert source_code_problems(source, "n") == []
    assert _scan_dangerous_patterns(source) == ""
    assert _producer_sandbox_reject("n", lambda _: node) == ""


@pytest.mark.parametrize("source", [
    "open('secret')", "open ('secret')", "(open)('secret')",
    "obj.open('secret')", "obj.open ('secret')", "compile('x', 'x', 'exec')",
    "eval ('1')", "exec ('pass')", "__import__('os')", "os.system ('cmd')",
    "import subprocess as sp", "from pickle import loads", "import marshal",
    "import importlib", "f'{open(\"secret\")}'",
])
def test_real_operations_stay_blocked_at_both_bid_boundaries(source):
    node = SimpleNamespace(approved=True, source_code=source)
    if source != "import marshal":  # Only the bid policy bans this module statically.
        assert NodeSandbox().validate_source(source)
    assert _scan_dangerous_patterns(source)
    assert _producer_sandbox_reject("n", lambda _: node)


def test_wrapper_keeps_its_narrower_policy():
    assert source_code_problems("open('file')", "n") == []
    assert source_code_problems("os.system('cmd')", "n")

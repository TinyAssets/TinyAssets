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
    'print("open(path), vars(), timeit.timeit(code), getattr(obj, name)")',
    'text = "open(path)"\ndef run(s): return {"text": text}',
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


@pytest.mark.parametrize("source", [
    # Demonstrated bypasses with the required runtime imports.
    'import timeit; timeit.timeit("open(\'/etc/passwd\').read()")',
    'import timeit; timeit.timeit("import os; os.system(\'id\')")',
    'import cProfile; cProfile.run("__import__(\'os\').system(\'id\')")',
    'import pdb; pdb.run("import subprocess")',
    "vars()['__builtins__']['open']('x')",
    # Aliases, nonliteral code and namespace keys must fail closed too.
    'import timeit as timer; timer.timeit("open(\'x\')")',
    'from timeit import timeit as timer; timer("open(\'x\')")',
    'from timeit import Timer; Timer(state["code"]).timeit()',
    'import cProfile as profiler; profiler.run(state["code"])',
    'import profile; profile.run("open(\'x\')")',
    'import pdb as debugger; debugger.run(state["code"])',
    'import bdb; bdb.Bdb().run(state["code"])',
    'import code; code.InteractiveInterpreter().runsource(state["code"])',
    'import codeop; codeop.compile_command(state["code"])',
    'import trace; trace.Trace().run(state["code"])',
    'import doctest; doctest.run_docstring_examples(fn, {})',
    'import runpy; runpy.run_path(state["path"])',
    'namespace = vars; namespace()[state["key"]]["open"]("x")',
    'globals()["__builtins__"]["open"]("x")',
    'locals()["__builtins__"]["open"]("x")',
    'getattr(__builtins__, "open")("x")',
    'lookup = getattr; lookup(obj, state["key"])("open(\'x\')")',
    'obj.__dict__["open"]("x")',
    'fn.__globals__["__builtins__"]["open"]("x")',
    'obj.__getattribute__(state["key"])("open(\'x\')")',
    'from operator import attrgetter as lookup; lookup("open")(obj)("x")',
    'import builtins as b; b.open("x")',
    'import inspect; inspect.currentframe().f_builtins["open"]("x")',
    'import types; types.FunctionType(state["code"], {})()',
    'import sys; sys.modules["cProfile"].run("open(\'x\')")',
    'import sys as s; s.modules["cProfile"].run("open(\'x\')")',
    'import sys; s = sys; s.modules["pdb"].run("import subprocess")',
    'from sys import modules as registry; registry["pdb"].run("import subprocess")',
    'from sys import *; modules["cProfile"].run("open(\'x\')")',
    'import sys; sys._getframe().f_builtins["open"]("x")',
    'frame.f_builtins["open"]("x")',
])
def test_dynamic_execution_and_reflection_fail_closed_at_every_boundary(source):
    node = SimpleNamespace(approved=True, source_code=source)
    assert NodeSandbox().validate_source(source)
    assert source_code_problems(source, "n")
    assert _scan_dangerous_patterns(source)
    assert _producer_sandbox_reject("n", lambda _: node)


def test_dynamic_execution_refusal_explains_the_unsupported_construct():
    source = 'import timeit; timeit.timeit(state["code"])'
    assert "dynamic execution/reflection is not supported: timeit" in (
        _scan_dangerous_patterns(source)
    )


@pytest.mark.parametrize("source", [
    'reader = open; reader("x")',
    'import io; reader = io.open; reader("x")',
    'from io import open as reader; reader("x")',
    'list(map(open, ["x"]))',
])
def test_forbidden_callable_references_cannot_be_aliased(source):
    node = SimpleNamespace(approved=True, source_code=source)
    assert NodeSandbox().validate_source(source)
    assert _scan_dangerous_patterns(source)
    assert _producer_sandbox_reject("n", lambda _: node)


@pytest.mark.parametrize("legacy_value_error", [False, True])
def test_null_bytes_return_rejections_at_every_boundary(monkeypatch, legacy_value_error):
    source = 'def run(s): return {"text": "\x00"}'
    if legacy_value_error:
        # Exercise ast.parse's Python 3.11 ValueError on newer interpreters too.
        from tinyassets import graph_compiler

        def null_byte_parse(*args, **kwargs):
            raise ValueError("source code cannot contain null bytes")

        monkeypatch.setattr(graph_compiler.ast, "parse", null_byte_parse)
    node = SimpleNamespace(approved=True, source_code=source)
    assert "null bytes" in " ".join(NodeSandbox().validate_source(source))
    assert "null bytes" in " ".join(source_code_problems(source, "n"))
    assert _scan_dangerous_patterns(source) == "invalid_syntax"
    assert _producer_sandbox_reject("n", lambda _: node) == "invalid_syntax"


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


def test_parser_exhaustion_returns_rejections_at_every_boundary(monkeypatch):
    from tinyassets import graph_compiler

    def exhausted(*args, **kwargs):
        raise RecursionError("too deeply nested")

    monkeypatch.setattr(graph_compiler.ast, "parse", exhausted)
    source = "x = 1"
    node = SimpleNamespace(approved=True, source_code=source)
    assert "too deeply nested" in " ".join(NodeSandbox().validate_source(source))
    assert "too deeply nested" in " ".join(source_code_problems(source, "n"))
    assert _scan_dangerous_patterns(source) == "invalid_syntax"
    assert _producer_sandbox_reject("n", lambda _: node) == "invalid_syntax"


def test_compiler_exhaustion_returns_diagnostics(monkeypatch):
    from tinyassets import graph_compiler, node_sandbox

    def exhausted(*args, **kwargs):
        raise RecursionError("too deeply nested")

    monkeypatch.setattr(graph_compiler, "compile", exhausted, raising=False)
    monkeypatch.setattr(node_sandbox, "compile", exhausted, raising=False)
    assert "too deeply nested" in " ".join(NodeSandbox().validate_source("x = 1"))
    assert "too deeply nested" in " ".join(source_code_problems("x = 1", "n"))

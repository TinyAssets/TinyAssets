"""Security invariants for the user-node sandbox.

`tinyassets/node_sandbox.py` is the production executor for `source_code`
nodes (OpenSpec change `sandboxed-code-node`, design D2 as revised by the
round-1 cross-family review). These tests pin:

  - the launcher is chosen by injection, never by an environment variable,
    and no launcher + no bwrap is a loud refusal;
  - the bwrap argv shape (no network, no /data, no inherited env, cwd /tmp)
    and the parent's launch flags (close_fds, pipes, private cwd);
  - stdout/stderr caps enforced *while reading*, so a flooding child is
    killed rather than buffered into the daemon's memory;
  - the result protocol cannot be forged by what the node prints;
  - the two-argument `run(state, effects)` contract, with the return handed
    back unfiltered plus `undeclared`;
  - import allowlisting, source denylisting, timeout kill, size limits.

Tests use the real subprocess runner (not mocks) so the assertions exercise
the actual isolation shape. This host may have no bwrap, so an autouse
fixture substitutes `DEFAULT_LAUNCHER_FACTORY` with the tests-only
`PlainSubprocessLauncher`; the launch-policy tests restore the real factory
in their own body. The adversarial jail tests skip unless the host has
bwrap and are the live proof on the production host.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from tinyassets import node_sandbox
from tinyassets.node_sandbox import (
    ALLOWED_IMPORTS,
    FORBIDDEN_PATTERNS,
    MAX_INPUT_BYTES,
    MAX_STDERR_BYTES,
    BwrapLauncher,
    NodeSandbox,
    PlainSubprocessLauncher,
    SandboxResult,
    SandboxUnavailableError,
)
from tinyassets.providers.base import probe_sandbox_available

_PROBE = probe_sandbox_available()
# The functional probe decides; which("bwrap") is its precondition, spelled out
# so linux-jail-proof's guard can see this file skips without bubblewrap.
requires_bwrap = pytest.mark.skipif(
    shutil.which("bwrap") is None or not _PROBE.get("bwrap_available"),
    reason=f"needs a host with bwrap: {_PROBE.get('reason')}",
)


@pytest.fixture(autouse=True)
def _plain_launcher_by_default(monkeypatch):
    """Resolve the tests-only launcher when a test injects none.

    Production resolves `BwrapLauncher` or raises; nothing reads an env var.
    Tests that assert the launch policy restore the real factory in-body.
    """
    monkeypatch.setattr(
        node_sandbox, "DEFAULT_LAUNCHER_FACTORY", PlainSubprocessLauncher
    )


def _run(coro):
    return asyncio.run(coro)


# -------------------------------------------------------------------
# Happy path
# -------------------------------------------------------------------


def test_happy_path_returns_output_state():
    """Snippet returning a dict round-trips cleanly."""
    sandbox = NodeSandbox(timeout=10.0)
    source = "def run(state):\n    return {'greeting': 'hello ' + state['name']}\n"

    result = _run(
        sandbox.execute(
            node_id="happy",
            source_code=source,
            input_state={"name": "world", "ignored": "secret"},
            input_keys=["name"],
            output_keys=["greeting"],
        )
    )

    assert isinstance(result, SandboxResult)
    assert result.success is True
    assert result.error == ""
    assert result.output_state == {"greeting": "hello world"}
    assert result.undeclared == []
    assert result.duration_seconds > 0


def test_input_keys_filter_strips_undeclared_state():
    """State keys not in input_keys must not reach the node function."""
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state):\n"
        "    return {'seen_keys': sorted(list(state.keys()))}\n"
    )

    result = _run(
        sandbox.execute(
            node_id="scope",
            source_code=source,
            input_state={"allowed": 1, "forbidden": 2, "also_forbidden": 3},
            input_keys=["allowed"],
            output_keys=["seen_keys"],
        )
    )

    assert result.success is True
    assert result.output_state == {"seen_keys": ["allowed"]}


def test_undeclared_return_keys_are_named_not_dropped():
    """Design D2 (R1 P1): the sandbox hands the FULL dict back.

    Filtering moved to the compiler so its single-merge-writer guard sees an
    undeclared merge field before anything is dropped. The sandbox's job is
    to name the undeclared keys, not to hide them.
    """
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state):\n"
        "    return {'allowed': 'yes', 'smuggled': 'exfil'}\n"
    )

    result = _run(
        sandbox.execute(
            node_id="undeclared",
            source_code=source,
            input_state={},
            input_keys=[],
            output_keys=["allowed"],
        )
    )

    assert result.success is True
    assert result.output_state == {"allowed": "yes", "smuggled": "exfil"}
    assert result.undeclared == ["smuggled"]
    assert "smuggled" in result.warning
    assert "smuggled" in result.to_dict()["undeclared"]


# -------------------------------------------------------------------
# Timeout enforcement
# -------------------------------------------------------------------


def test_timeout_kills_infinite_loop():
    """An infinite loop must be killed within ~timeout seconds."""
    sandbox = NodeSandbox(timeout=1.0)
    source = (
        "def run(state):\n"
        "    while True:\n"
        "        pass\n"
    )

    result = _run(
        sandbox.execute(
            node_id="loop",
            source_code=source,
            input_state={},
            input_keys=[],
            output_keys=[],
            timeout=1.0,
        )
    )

    assert result.success is False
    assert "timed out" in result.error.lower()
    assert result.duration_seconds < 5.0


def test_run_sync_timeout_kills_busy_loop_promptly(monkeypatch):
    """A busy loop is killed near the timeout, not left running.

    Asserts the kill actually happens: a timeout that only *returns* an
    error leaves a process spinning on the host forever.
    """
    killed: list[int] = []
    real_kill = node_sandbox._kill_process_tree

    def _spy(proc):
        killed.append(proc.pid)
        return real_kill(proc)

    monkeypatch.setattr(node_sandbox, "_kill_process_tree", _spy)

    sandbox = NodeSandbox(timeout=1.0)
    started = time.monotonic()

    result = sandbox.run_sync(
        node_id="spin",
        source_code="def run(state):\n    while True:\n        pass\n",
        input_state={},
        input_keys=[],
        output_keys=[],
        timeout=1.0,
    )

    elapsed = time.monotonic() - started
    assert result.success is False
    assert result.error == "Execution timed out after 1.0s"
    assert elapsed < 3.0, f"kill took {elapsed:.2f}s"
    assert killed, "the timed-out child must be killed, not just reported"


def test_endless_printing_node_times_out_without_growing_memory():
    """`while True: print()` must not accumulate anywhere.

    The node's stdout is captured into a bounded in-child buffer, so the
    parent never sees the flood and the wall clock ends the run.
    """
    sandbox = NodeSandbox(timeout=1.0)
    source = (
        "def run(state):\n"
        "    while True:\n"
        "        print('x' * 1000)\n"
    )

    result = sandbox.run_sync(
        node_id="chatterbox",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=[],
        timeout=1.0,
    )

    assert result.success is False
    assert "timed out" in result.error.lower()
    assert len(result.stdout_tail) <= 2048


# -------------------------------------------------------------------
# Forbidden-pattern pre-validation
# -------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern_source",
    [
        "import os\ndef run(state):\n    os.system('whoami')\n    return {}\n",
        "import subprocess\ndef run(state):\n    return {}\n",
        "def run(state):\n    eval('1+1')\n    return {}\n",
        "def run(state):\n    exec('print(1)')\n    return {}\n",
        "def run(state):\n    open('secrets.txt').read()\n    return {}\n",
        "def run(state):\n    __import__('socket')\n    return {}\n",
        "import pickle\ndef run(state):\n    return {}\n",
        "import ctypes\ndef run(state):\n    return {}\n",
    ],
)
def test_forbidden_pattern_rejected_pre_execution(pattern_source):
    """validate_source rejects forbidden patterns before any subprocess starts."""
    sandbox = NodeSandbox(timeout=5.0)

    errors = sandbox.validate_source(pattern_source)

    assert errors, (
        f"validate_source should reject source containing forbidden pattern:\n"
        f"{pattern_source!r}"
    )
    assert any("Forbidden pattern" in e for e in errors)


def test_oversized_source_rejected():
    """Source over 50KB is rejected."""
    sandbox = NodeSandbox()
    giant = "def run(state):\n    return {}\n" + ("# pad\n" * 10_000)

    errors = sandbox.validate_source(giant)

    assert any("50KB" in e for e in errors)


def test_syntax_error_rejected():
    """Unparseable source yields a syntax-error validation entry."""
    sandbox = NodeSandbox()

    errors = sandbox.validate_source("def run(state):\n    return {broken\n")

    assert any("Syntax error" in e for e in errors)


def test_execute_short_circuits_on_validation_failure():
    """Validation failure returns success=False without launching a subprocess."""
    sandbox = NodeSandbox(timeout=30.0)

    result = _run(
        sandbox.execute(
            node_id="bad",
            source_code="import subprocess\ndef run(state):\n    return {}\n",
            input_state={},
            input_keys=[],
            output_keys=[],
        )
    )

    assert result.success is False
    assert "Validation failed" in result.error
    assert result.duration_seconds < 1.0


# -------------------------------------------------------------------
# Import allowlist enforcement (runtime, in subprocess)
# -------------------------------------------------------------------


def test_import_not_in_allowlist_fails_at_runtime():
    """A non-forbidden but non-allowlisted import is blocked at runtime.

    `sys` is neither in ALLOWED_IMPORTS nor in FORBIDDEN_PATTERNS, so it
    passes pre-validation and the in-subprocess restricted __import__ must
    reject it.
    """
    sandbox = NodeSandbox(timeout=10.0)
    source = "import sys\ndef run(state):\n    return {'argv0': sys.argv[0]}\n"

    assert sandbox.validate_source(source) == []

    result = sandbox.run_sync(
        node_id="sneaky",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["argv0"],
    )

    assert result.success is False
    assert "not allowed" in result.error.lower()


def test_allowlisted_import_succeeds():
    """An ALLOWED_IMPORTS module (e.g., json) works inside the sandbox."""
    assert "json" in ALLOWED_IMPORTS

    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "import json\n"
        "def run(state):\n"
        "    return {'payload': json.dumps({'n': len(state.get('items', []))})}\n"
    )

    result = sandbox.run_sync(
        node_id="allowed",
        source_code=source,
        input_state={"items": [1, 2, 3]},
        input_keys=["items"],
        output_keys=["payload"],
    )

    assert result.success is True
    assert result.output_state == {"payload": '{"n": 3}'}


# -------------------------------------------------------------------
# Error capture: runtime exceptions surface structured, not silent
# -------------------------------------------------------------------


def test_runtime_exception_surfaces_as_structured_error():
    """A snippet that raises returns success=False with the exception detail."""
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state):\n"
        "    raise ValueError('deliberate failure')\n"
    )

    result = sandbox.run_sync(
        node_id="raiser",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=[],
    )

    assert result.success is False
    assert "ValueError" in result.error
    assert "deliberate failure" in result.error
    assert result.output_state == {}


def test_no_callable_found_returns_structured_error():
    """Source with no function yields a structured error, not a crash.

    Also pins that the runner's own injected names (`invoke_mcp_action`) are
    never mistaken for the node's function by the last-callable fallback —
    which would call the action surface with the graph state as its action
    name.
    """
    seen: list[str] = []
    sandbox = NodeSandbox(timeout=10.0)

    result = sandbox.run_sync(
        node_id="empty",
        source_code="x = 1\n",
        input_state={},
        input_keys=[],
        output_keys=[],
        invoke=lambda action, kwargs: seen.append(action) or "never",
    )

    assert result.success is False
    assert "no callable" in result.error.lower()
    assert seen == [], "the injected invoker must not be run as the node"


def test_non_dict_return_surfaces_as_error():
    """Node must return a dict; scalar/list returns surface as structured error."""
    sandbox = NodeSandbox(timeout=10.0)
    source = "def run(state):\n    return 42\n"

    result = sandbox.run_sync(
        node_id="bad-return",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=[],
    )

    assert result.success is False
    assert "must return a dict" in result.error


def test_unserializable_return_fails_structurally():
    """A dict the protocol cannot encode fails loudly, not silently."""
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state):\n"
        "    return {'obj': object()}\n"
    )

    result = sandbox.run_sync(
        node_id="unserializable",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["obj"],
    )

    assert result.success is False
    assert "not JSON-serializable" in result.error


# -------------------------------------------------------------------
# Allowlist / Forbidden-list structural invariants
# -------------------------------------------------------------------


def test_allowlist_and_forbidden_are_disjoint_for_top_level_names():
    """No module is both allowlisted AND forbidden by top-level name."""
    forbidden_top_names = set()
    for pat in FORBIDDEN_PATTERNS:
        if "(" not in pat and "." not in pat and "__" not in pat:
            forbidden_top_names.add(pat)
    overlap = ALLOWED_IMPORTS & forbidden_top_names
    assert overlap == set(), (
        f"Allowlist and forbidden list must not overlap; got: {overlap}"
    )


def test_critical_forbidden_patterns_are_present():
    """Regression guard: a few must-block patterns have to stay forbidden."""
    must_block = {"subprocess", "pickle", "ctypes", "eval(", "exec(", "open("}
    present = set(FORBIDDEN_PATTERNS)
    missing = must_block - present
    assert not missing, (
        f"Critical forbidden patterns missing from FORBIDDEN_PATTERNS: {missing}"
    )


# ===================================================================
# Design D2: production executor for `source_code` nodes
# (openspec/changes/sandboxed-code-node/design.md)
# ===================================================================


# -------------------------------------------------------------------
# The two-argument contract: run(state, effects)
# -------------------------------------------------------------------


def test_two_arg_run_receives_effects():
    """`def run(state, effects)` sees its ancestors' status and body."""
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state, effects):\n"
        "    fetched = effects['fetch']\n"
        "    return {\n"
        "        'content': fetched['body']['text'].upper() + state['suffix'],\n"
        "        'status': fetched['status'],\n"
        "    }\n"
    )

    result = sandbox.run_sync(
        node_id="edit",
        source_code=source,
        input_state={"suffix": "!", "secret": "not-declared"},
        input_keys=["suffix"],
        output_keys=["content", "status"],
        timeout=10.0,
        effects={"fetch": {"status": 200, "body": {"text": "hello"}}},
    )

    assert result.success is True, result.error
    assert result.output_state == {"content": "HELLO!", "status": 200}
    assert result.undeclared == []


def test_effects_carry_no_headers():
    """Design D2 (R1 P0): a Set-Cookie in headers is a credential.

    The sandbox passes the dict through untouched — it must not invent a
    headers key, and what the compiler sends is status/body only.
    """
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state, effects):\n"
        "    return {'keys': sorted(effects['fetch'].keys())}\n"
    )

    result = sandbox.run_sync(
        node_id="no-headers",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["keys"],
        effects={"fetch": {"status": 200, "body": "x"}},
    )

    assert result.success is True, result.error
    assert result.output_state == {"keys": ["body", "status"]}
    # The child never synthesizes a headers key of its own: the effects map
    # arrives and is handed to run() untouched.
    assert '"headers"' not in node_sandbox._RUNNER_SCRIPT
    assert "'headers'" not in node_sandbox._RUNNER_SCRIPT


def test_two_arg_run_with_defaulted_effects_param():
    """`def run(state, effects=None)` is the documented shape — it gets effects."""
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state, effects=None):\n"
        "    return {'seen': sorted((effects or {}).keys())}\n"
    )

    result = sandbox.run_sync(
        node_id="defaulted",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["seen"],
        effects={"fetch": {"status": 200, "body": None}},
    )

    assert result.success is True, result.error
    assert result.output_state == {"seen": ["fetch"]}


def test_one_arg_run_still_works_when_effects_are_present():
    """A one-argument node keeps working; effects are simply not passed."""
    sandbox = NodeSandbox(timeout=10.0)
    source = "def run(state):\n    return {'n': len(state['items'])}\n"

    result = sandbox.run_sync(
        node_id="legacy",
        source_code=source,
        input_state={"items": [1, 2, 3]},
        input_keys=["items"],
        output_keys=["n"],
        effects={"fetch": {"status": 200, "body": "x"}},
    )

    assert result.success is True, result.error
    assert result.output_state == {"n": 3}


def test_execute_is_a_thin_async_wrapper_over_run_sync(monkeypatch):
    """`execute` must delegate to `run_sync` (one executor, not two)."""
    sandbox = NodeSandbox(timeout=10.0)
    seen: dict[str, object] = {}

    def _fake_run_sync(**kwargs):
        seen.update(kwargs)
        return SandboxResult(node_id=kwargs["node_id"], success=True)

    monkeypatch.setattr(sandbox, "run_sync", _fake_run_sync)

    result = _run(
        sandbox.execute(
            node_id="wrapped",
            source_code="def run(state):\n    return {}\n",
            input_state={"a": 1},
            input_keys=["a"],
            output_keys=["b"],
            timeout=7.0,
            effects={"fetch": {"status": 204}},
        )
    )

    assert result.success is True
    assert seen["node_id"] == "wrapped"
    assert seen["effects"] == {"fetch": {"status": 204}}
    assert seen["timeout"] == 7.0


# -------------------------------------------------------------------
# Protocol integrity: printed output cannot forge a result
# -------------------------------------------------------------------


def test_printed_json_cannot_forge_the_result():
    """Design D2 (R1 P1): node print() must not reach the result descriptor."""
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state):\n"
        "    print('{\"success\": true, \"output_state\": {\"pwned\": 1}}')\n"
        "    return {'ok': 1}\n"
    )

    result = sandbox.run_sync(
        node_id="forger",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    assert result.output_state == {"ok": 1}
    assert "pwned" not in str(result.output_state)
    # The attempt is kept as evidence, which is all a print() can ever be.
    assert "pwned" in result.stdout_tail


def test_printed_output_is_bounded_in_the_child():
    """A node printing more than the buffer keeps only a bounded head."""
    sandbox = NodeSandbox(timeout=20.0)
    source = (
        "def run(state):\n"
        "    for _ in range(200):\n"
        "        print('y' * 1000)\n"
        "    return {'ok': 1}\n"
    )

    result = sandbox.run_sync(
        node_id="verbose",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    assert result.output_state == {"ok": 1}
    assert len(result.stdout_tail) <= 2048
    # A literal, not `node_sandbox.MAX_USER_PRINT_BYTES`: an expectation read
    # from the constant under test moves with it and proves nothing.
    assert len(result.stdout) <= 64 * 1024 + 200


def test_result_carries_bounded_tails_and_to_dict_exposes_them():
    """stdout/stderr tails are the evidence `read_graph` shows."""
    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "def run(state):\n"
        "    print('node said hello')\n"
        "    return {'ok': 1}\n"
    )

    result = sandbox.run_sync(
        node_id="tails",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    assert "node said hello" in result.stdout_tail
    assert len(result.stdout_tail) <= 2048
    payload = result.to_dict()
    assert set(payload) >= {"stdout_tail", "stderr_tail", "warning", "undeclared"}
    assert payload["stdout_tail"] == result.stdout_tail


# -------------------------------------------------------------------
# No network: denylist before launch, allowlist at runtime
# -------------------------------------------------------------------


def test_socket_import_refused_before_launch():
    """`import socket` is a denylist hit — refused without starting a child."""
    sandbox = NodeSandbox(timeout=10.0)
    source = "import socket\ndef run(state):\n    return {'s': 1}\n"

    errors = sandbox.validate_source(source)
    assert any("socket" in e for e in errors)

    result = sandbox.run_sync(
        node_id="net",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["s"],
    )

    assert result.success is False
    assert "Validation failed" in result.error
    assert "socket" in result.error


@pytest.mark.parametrize("module", ["requests", "httpx", "http.client", "ssl"])
def test_network_client_imports_blocked_by_allowlist(module):
    """Design D2: the child has no network, so HTTP clients left the allowlist.

    These names are not denylisted (no `FORBIDDEN_PATTERNS` hit), so they
    pass pre-validation and must be stopped by the in-child allowlist.
    """
    assert module not in ALLOWED_IMPORTS

    sandbox = NodeSandbox(timeout=10.0)
    source = f"import {module}\ndef run(state):\n    return {{'x': 1}}\n"
    assert sandbox.validate_source(source) == []

    result = sandbox.run_sync(
        node_id="client",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["x"],
    )

    assert result.success is False
    assert "not allowed in sandboxed nodes" in result.error


def test_requests_and_httpx_left_the_allowlist():
    """Regression guard on the D2 allowlist change."""
    assert "requests" not in ALLOWED_IMPORTS
    assert "httpx" not in ALLOWED_IMPORTS


# -------------------------------------------------------------------
# The widened allowlist actually works inside the child
# -------------------------------------------------------------------


def test_base64_is_importable_inside_run():
    """`base64` joined the allowlist for the fetch → edit → write shape."""
    assert "base64" in ALLOWED_IMPORTS

    sandbox = NodeSandbox(timeout=10.0)
    source = (
        "import base64\n"
        "def run(state):\n"
        "    raw = base64.b64decode(state['blob']).decode()\n"
        "    return {'text': raw, 'roundtrip': "
        "base64.b64encode(raw.encode()).decode()}\n"
    )

    result = sandbox.run_sync(
        node_id="b64",
        source_code=source,
        input_state={"blob": "aGVsbG8="},
        input_keys=["blob"],
        output_keys=["text", "roundtrip"],
    )

    assert result.success is True, result.error
    assert result.output_state == {"text": "hello", "roundtrip": "aGVsbG8="}


@pytest.mark.parametrize(
    "module", ["io", "csv", "html", "unicodedata", "zlib", "struct",
               "operator", "heapq", "bisect", "time", "statistics"]
)
def test_new_allowlist_entries_import_inside_the_child(module):
    """Every allowlisted module must be importable — including its own deps."""
    assert module in ALLOWED_IMPORTS

    sandbox = NodeSandbox(timeout=10.0)
    source = f"import {module}\ndef run(state):\n    return {{'ok': True}}\n"

    result = sandbox.run_sync(
        node_id=f"import-{module}",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    assert result.output_state == {"ok": True}


def test_transitive_dependency_is_not_importable_directly():
    """`binascii` loads *for* base64 but stays blocked as a direct import.

    Guards the depth-unwinding in the restricted importer: if the counter
    leaked, this would start passing.
    """
    assert "binascii" not in ALLOWED_IMPORTS

    sandbox = NodeSandbox(timeout=10.0)
    result = sandbox.run_sync(
        node_id="direct-transitive",
        source_code="import binascii\ndef run(state):\n    return {'x': 1}\n",
        input_state={},
        input_keys=[],
        output_keys=["x"],
    )

    assert result.success is False
    assert "not allowed in sandboxed nodes" in result.error


# -------------------------------------------------------------------
# Size limits
# -------------------------------------------------------------------


def _no_launch(monkeypatch):
    """Make any child launch an immediate test failure."""
    def _boom(self):
        raise AssertionError("subprocess must not be launched")

    monkeypatch.setattr(NodeSandbox, "resolve_launcher", _boom)


def test_oversized_input_refused_before_launch(monkeypatch):
    """A message over 16 MiB is refused without starting a child."""
    _no_launch(monkeypatch)
    sandbox = NodeSandbox(timeout=10.0)

    result = sandbox.run_sync(
        node_id="huge-in",
        source_code="def run(state):\n    return {'ok': 1}\n",
        input_state={"blob": "x" * (17 * 1024 * 1024)},
        input_keys=["blob"],
        output_keys=["ok"],
    )

    assert result.success is False
    assert "input too large" in result.error
    assert str(MAX_INPUT_BYTES) in result.error


def test_forbidden_pattern_refused_before_launch(monkeypatch):
    """Denylist refusal happens before any child is started."""
    _no_launch(monkeypatch)
    sandbox = NodeSandbox(timeout=10.0)

    result = sandbox.run_sync(
        node_id="denied",
        source_code="import pickle\ndef run(state):\n    return {}\n",
        input_state={},
        input_keys=[],
        output_keys=[],
    )

    assert result.success is False
    assert "Validation failed" in result.error
    assert "pickle" in result.error


def test_oversized_output_refused():
    """Stdout over max_output_bytes fails the node instead of being parsed."""
    sandbox = NodeSandbox(timeout=30.0)
    source = "def run(state):\n    return {'k': 'x' * 9_000_000}\n"

    result = sandbox.run_sync(
        node_id="huge-out",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["k"],
        timeout=30.0,
    )

    assert result.success is False
    assert "output too large" in result.error
    assert result.output_state == {}
    assert len(result.stdout_tail) <= 2048


def test_the_d2_limits_are_the_documented_numbers():
    """Pinned as literals so a widened cap cannot pass its own test."""
    assert NodeSandbox().max_output_bytes == 8 * 1024 * 1024
    assert node_sandbox.MAX_OUTPUT_BYTES == 8 * 1024 * 1024
    assert node_sandbox.MAX_INPUT_BYTES == 16 * 1024 * 1024
    assert node_sandbox.MAX_STDERR_BYTES == 64 * 1024
    assert node_sandbox.MAX_USER_PRINT_BYTES == 64 * 1024
    assert node_sandbox.TAIL_CHARS == 2048


# -------------------------------------------------------------------
# Caps are enforced WHILE reading, not after
# -------------------------------------------------------------------


class _SpewLauncher:
    """Test double: a child that floods one stream and ignores the protocol.

    Used to prove the parent kills a flooding child instead of buffering it,
    which no node-level source can express (the runner bounds node print()).
    """

    name = "spew"

    def __init__(self, stream: str, total_bytes: int) -> None:
        self.stream = stream
        self.total_bytes = total_bytes

    def build_argv(self, runner_script: str, args: list[str]) -> list[str]:
        script = (
            "import sys\n"
            f"target = sys.{self.stream}\n"
            "chunk = 'x' * 65536\n"
            "written = 0\n"
            f"while written < {self.total_bytes}:\n"
            "    target.write(chunk)\n"
            "    target.flush()\n"
            "    written += len(chunk)\n"
        )
        return [sys.executable, "-c", script]

    def env(self, home_dir: str) -> dict[str, str]:
        return PlainSubprocessLauncher().env(home_dir)


def test_stdout_flood_is_killed_while_reading():
    """40 MiB of stdout must not be buffered — the child dies at the cap."""
    sandbox = NodeSandbox(
        timeout=60.0, launcher=_SpewLauncher("stdout", 40 * 1024 * 1024)
    )
    started = time.monotonic()

    result = sandbox.run_sync(
        node_id="flood-out",
        source_code="def run(state):\n    return {}\n",
        input_state={},
        input_keys=[],
        output_keys=[],
        timeout=60.0,
    )

    elapsed = time.monotonic() - started
    assert result.success is False
    assert "output too large" in result.error
    assert "stdout" in result.error
    assert elapsed < 30.0, f"cap was not enforced while reading ({elapsed:.1f}s)"
    assert len(result.stdout_tail) <= 2048


def test_stderr_flood_is_killed_while_reading():
    """stderr has its own, much smaller cap."""
    sandbox = NodeSandbox(
        timeout=60.0, launcher=_SpewLauncher("stderr", 8 * 1024 * 1024)
    )

    result = sandbox.run_sync(
        node_id="flood-err",
        source_code="def run(state):\n    return {}\n",
        input_state={},
        input_keys=[],
        output_keys=[],
        timeout=60.0,
    )

    assert result.success is False
    assert "output too large" in result.error
    assert "stderr" in result.error
    assert str(MAX_STDERR_BYTES) in result.error


def test_bounded_drain_stops_at_the_cap():
    """The reader keeps at most `cap` bytes and flags the breach."""
    breaches: list[str] = []
    stream = io.BytesIO(b"z" * (1024 * 1024))
    drain = node_sandbox._BoundedDrain(stream, 4096, "stdout", breaches)

    drain.start()
    drain.join(timeout=5.0)

    assert breaches == ["stdout"]
    assert drain.kept == 4096
    assert len(drain.data) == 4096
    # It stopped reading rather than draining the whole megabyte.
    assert drain.total <= 4096 + 65536


# -------------------------------------------------------------------
# Resource limits are applied or the node says so (Codex round 2, P1)
# -------------------------------------------------------------------


class _FakeResource:
    """Stand-in for the `resource` module with a scriptable setrlimit."""

    RLIM_INFINITY = -1
    RLIMIT_AS = 1
    RLIMIT_CPU = 2
    RLIMIT_FSIZE = 3
    RLIMIT_NOFILE = 4

    def __init__(self, mode: str = "ok", hard: int = -1) -> None:
        self.mode = mode
        self.hard = hard
        self.stored: dict[int, tuple[int, int]] = {}
        self.calls: list[tuple[int, tuple[int, int]]] = []

    def getrlimit(self, res):
        return self.stored.get(res, (self.hard, self.hard))

    def setrlimit(self, res, pair):
        self.calls.append((res, pair))
        if self.mode == "raise":
            raise OSError("denied by policy")
        if self.mode == "ignore":
            return None  # accepted, then not applied — the silent case
        self.stored[res] = pair


def _rlimit_helper():
    """Compile the exact helper text the child runs."""
    namespace: dict[str, object] = {}
    exec(node_sandbox._RLIMIT_HELPER, namespace)
    return namespace["_apply_rlimits"]


def test_rlimit_helper_reports_nothing_when_every_limit_applies():
    fake = _FakeResource(mode="ok")

    failures = _rlimit_helper()(fake, 30.0)

    assert failures == []
    assert {res for res, _ in fake.calls} == {1, 2, 3, 4}


def test_rlimit_helper_names_a_limit_that_raises():
    fake = _FakeResource(mode="raise")

    failures = _rlimit_helper()(fake, 30.0)

    assert len(failures) == 4
    assert any(f.startswith("RLIMIT_AS (OSError: denied by policy)") for f in failures)
    assert any("RLIMIT_NOFILE" in f for f in failures)


def test_rlimit_helper_catches_a_silently_ignored_setrlimit():
    """A call that returns cleanly but does not take effect is a failure."""
    fake = _FakeResource(mode="ignore")

    failures = _rlimit_helper()(fake, 30.0)

    assert len(failures) == 4
    assert all("asked for" in f and "reads -1" in f for f in failures), failures


def test_rlimit_helper_reports_a_platform_without_the_module():
    failures = _rlimit_helper()(None, 30.0)

    assert failures == ["all limits (this platform has no 'resource' module)"]


@pytest.mark.parametrize("timeout, expected_cpu", [(30.0, 31), (2.5, 4), (3.0, 4)])
def test_rlimit_helper_derives_cpu_seconds_from_the_timeout(timeout, expected_cpu):
    fake = _FakeResource(mode="ok")

    _rlimit_helper()(fake, timeout)

    cpu_calls = [pair for res, pair in fake.calls if res == _FakeResource.RLIMIT_CPU]
    assert cpu_calls == [(expected_cpu, expected_cpu)]


def test_rlimit_helper_respects_a_lower_hard_ceiling():
    """Never ask for more than the hard limit allows — that would just fail."""
    fake = _FakeResource(mode="ok", hard=32)

    failures = _rlimit_helper()(fake, 30.0)

    assert failures == []
    assert all(soft <= 32 for _, (soft, _hard) in fake.calls), fake.calls


def test_only_the_os_jail_makes_rlimits_mandatory():
    assert node_sandbox._requires_rlimits(BwrapLauncher()) is True
    assert BwrapLauncher.requires_rlimits is True
    assert node_sandbox._requires_rlimits(PlainSubprocessLauncher()) is False
    assert node_sandbox._requires_rlimits(object()) is False


class _FakeResourceLauncher:
    """Test double: a child whose `resource` module refuses every setrlimit.

    Deterministic on any OS — the fake is installed in `sys.modules` by a
    prelude ahead of the real runner script, so the limit gate sees a module
    that is present and uncooperative rather than absent.
    """

    name = "fake-resource"

    def __init__(self, requires_rlimits: bool) -> None:
        self.requires_rlimits = requires_rlimits

    def build_argv(self, runner_script: str, args: list[str]) -> list[str]:
        prelude = (
            "import sys, types\n"
            "fake = types.ModuleType('resource')\n"
            "fake.RLIM_INFINITY = -1\n"
            "fake.RLIMIT_AS = 1\n"
            "fake.RLIMIT_CPU = 2\n"
            "fake.RLIMIT_FSIZE = 3\n"
            "fake.RLIMIT_NOFILE = 4\n"
            "fake.getrlimit = lambda res: (-1, -1)\n"
            "def _refuse(res, limits):\n"
            "    raise OSError('denied by policy')\n"
            "fake.setrlimit = _refuse\n"
            "sys.modules['resource'] = fake\n"
        )
        # Delivered as a FILE, like PlainSubprocessLauncher: `python -c` is
        # capped near 32 KiB of command line on Windows and the runner is
        # larger than that. Same text, different delivery.
        handle, path = tempfile.mkstemp(prefix="ta-test-runner-", suffix=".py")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(prelude + runner_script)
        self._script_path = path
        return [sys.executable, path, *args]

    def cleanup(self) -> None:
        path = getattr(self, "_script_path", "")
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
            self._script_path = ""

    def env(self, home_dir: str) -> dict[str, str]:
        return PlainSubprocessLauncher().env(home_dir)


def test_unappliable_limits_fail_the_node_when_the_sandbox_requires_them():
    """Codex round 2 P1: a swallowed setrlimit could run a node unbounded."""
    reached: list[str] = []
    sandbox = NodeSandbox(timeout=10.0, launcher=_FakeResourceLauncher(True))

    result = sandbox.run_sync(
        node_id="rlimits-required",
        source_code="def run(state):\n    return {'r': invoke_mcp_action('ping')}\n",
        input_state={},
        input_keys=[],
        output_keys=["r"],
        invoke=lambda action, kwargs: reached.append(action) or "pong",
    )

    assert result.success is False
    assert result.error.startswith("rlimits not applied:")
    assert "RLIMIT_AS (OSError: denied by policy)" in result.error
    assert "RLIMIT_NOFILE" in result.error
    assert result.output_state == {}
    assert reached == [], "the node ran despite unappliable limits"


def test_unappliable_limits_only_warn_when_the_sandbox_does_not_require_them():
    """The tests-only launcher still runs, but never in silence."""
    sandbox = NodeSandbox(timeout=10.0, launcher=_FakeResourceLauncher(False))

    result = sandbox.run_sync(
        node_id="rlimits-optional",
        source_code="def run(state):\n    return {'ok': 1}\n",
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    assert result.output_state == {"ok": 1}
    assert result.warning.startswith("resource limits not applied:")
    assert "denied by policy" in result.warning


def test_rlimit_warning_does_not_hide_the_undeclared_key_warning():
    """Two warnings must both survive — neither overwrites the other."""
    sandbox = NodeSandbox(timeout=10.0, launcher=_FakeResourceLauncher(False))

    result = sandbox.run_sync(
        node_id="two-warnings",
        source_code="def run(state):\n    return {'ok': 1, 'extra': 2}\n",
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    assert "resource limits not applied" in result.warning
    assert "Undeclared output keys" in result.warning
    assert result.undeclared == ["extra"]


def test_the_real_plain_launcher_reports_its_limit_situation_honestly():
    """On this host, whatever `resource` does, the result must say so."""
    sandbox = NodeSandbox(timeout=10.0, launcher=PlainSubprocessLauncher())

    result = sandbox.run_sync(
        node_id="rlimits-real",
        source_code="def run(state):\n    return {'ok': 1}\n",
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    if sys.platform == "win32":
        assert "no 'resource' module" in result.warning
    else:
        assert result.warning == "", (
            f"POSIX should apply every limit; got {result.warning!r}"
        )


# -------------------------------------------------------------------
# invoke_mcp_action: synchronous RPC, answered by the parent
# -------------------------------------------------------------------


_RPC_SOURCE = (
    "def run(state):\n"
    "    page = invoke_mcp_action('wiki_read', page='Home')\n"
    "    queued = invoke_mcp_action('enqueue_branch_run', branch_def_id='b1')\n"
    "    return {'page': page, 'queued': queued}\n"
)


def test_invoke_mcp_action_round_trips_through_the_parent():
    """The child gets the parent's answer inside run(), in call order."""
    seen: list[tuple[str, dict]] = []

    def invoke(action, kwargs):
        seen.append((action, kwargs))
        return {"status": "ok", "echo": kwargs}

    sandbox = NodeSandbox(timeout=20.0)

    result = sandbox.run_sync(
        node_id="rpc",
        source_code=_RPC_SOURCE,
        input_state={},
        input_keys=[],
        output_keys=["page", "queued"],
        invoke=invoke,
    )

    assert result.success is True, f"{result.error} | {result.stderr_tail}"
    assert [action for action, _ in seen] == ["wiki_read", "enqueue_branch_run"]
    assert seen[0][1] == {"page": "Home"}
    assert seen[1][1] == {"branch_def_id": "b1"}
    assert result.output_state == {
        "page": {"status": "ok", "echo": {"page": "Home"}},
        "queued": {"status": "ok", "echo": {"branch_def_id": "b1"}},
    }
    # RPC traffic must not leak into the result frame or the evidence tails.
    assert "rpc" not in result.stdout_tail
    assert result.undeclared == []


def test_a_raising_invoke_fails_the_node_with_its_message():
    """The parent's refusal reaches run() as a RuntimeError, then the node."""
    def invoke(action, kwargs):
        raise ValueError("no grant for that action")

    sandbox = NodeSandbox(timeout=20.0)

    result = sandbox.run_sync(
        node_id="rpc-raise",
        source_code=_RPC_SOURCE,
        input_state={},
        input_keys=[],
        output_keys=["page", "queued"],
        invoke=invoke,
    )

    assert result.success is False
    assert "no grant for that action" in result.error
    assert "ValueError" in result.error


def test_invoke_none_answers_not_available():
    """A node with no action surface gets a refusal, not a hang."""
    sandbox = NodeSandbox(timeout=20.0)
    started = time.monotonic()

    result = sandbox.run_sync(
        node_id="rpc-none",
        source_code=_RPC_SOURCE,
        input_state={},
        input_keys=[],
        output_keys=["page", "queued"],
        invoke=None,
    )

    assert result.success is False
    assert "not available" in result.error
    assert time.monotonic() - started < 15.0, "an unanswered call would hang"


def test_rpc_calls_are_not_capped():
    """``MAX_RPC_CALLS = 500`` is gone: every call a node makes is answered.

    It failed the node at call 501 with "too many rpc calls" -- a structural
    bound on what one node may be. An account has exactly two limits, cloud
    bytes and concurrent agent seats (founder, 2026-09-30). Each individual
    reply is still bounded by ``MAX_RPC_REPLY_BYTES``, which is payload
    validation of one message.
    """
    seen: list[str] = []
    sandbox = NodeSandbox(timeout=60.0)
    assert not hasattr(node_sandbox, "MAX_RPC_CALLS"), "the per-node cap came back"
    calls = 520  # past the old 500
    source = (
        "def run(state):\n"
        f"    for i in range({calls}):\n"
        "        invoke_mcp_action('ping', i=i)\n"
        "    return {'done': True}\n"
    )

    result = sandbox.run_sync(
        node_id="rpc-uncapped",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["done"],
        invoke=lambda action, kwargs: seen.append(action) or "pong",
        timeout=60.0,
    )

    assert result.success is True, result.error
    assert result.output_state["done"] is True
    assert len(seen) == calls, f"every call must be answered, saw {len(seen)}"


def test_rpc_reply_is_capped_at_one_mib():
    """An oversized action result is refused rather than shipped."""
    sandbox = NodeSandbox(timeout=30.0)
    source = (
        "def run(state):\n"
        "    return {'r': invoke_mcp_action('wiki_read', page='Big')}\n"
    )

    result = sandbox.run_sync(
        node_id="rpc-big",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["r"],
        invoke=lambda action, kwargs: "x" * (2 * 1024 * 1024),
        timeout=30.0,
    )

    assert result.success is False
    assert "reply limit" in result.error
    assert str(1024 * 1024) in result.error


def test_timeout_covers_time_blocked_in_an_rpc():
    """A slow invoker cannot outlive the node's wall clock."""
    def slow(action, kwargs):
        time.sleep(5.0)
        return "late"

    sandbox = NodeSandbox(timeout=1.0)
    started = time.monotonic()

    result = sandbox.run_sync(
        node_id="rpc-slow",
        source_code="def run(state):\n    return {'r': invoke_mcp_action('x')}\n",
        input_state={},
        input_keys=[],
        output_keys=["r"],
        invoke=slow,
        timeout=1.0,
    )

    elapsed = time.monotonic() - started
    assert result.success is False
    assert "timed out" in result.error.lower()
    assert elapsed < 10.0, f"the run outlived its timeout ({elapsed:.1f}s)"


def test_invoke_mcp_action_validates_its_arguments():
    """An empty action name is the node's error, not a parent round-trip."""
    seen: list[str] = []
    sandbox = NodeSandbox(timeout=20.0)

    result = sandbox.run_sync(
        node_id="rpc-bad-name",
        source_code="def run(state):\n    return {'r': invoke_mcp_action('  ')}\n",
        input_state={},
        input_keys=[],
        output_keys=["r"],
        invoke=lambda action, kwargs: seen.append(action) or "never",
    )

    assert result.success is False
    assert "non-empty action name" in result.error
    assert seen == [], "a malformed call must not reach the parent"


def _raising_invoke(action, kwargs):
    raise KeyError("nope")


def _circular_invoke(action, kwargs):
    loop: dict = {}
    loop["self"] = loop
    return loop


@pytest.mark.parametrize(
    "invoke, expect",
    [
        (None, "not available"),
        (_raising_invoke, "KeyError"),
        (_circular_invoke, "not JSON-serializable"),
        (lambda a, k: "z" * (2 * 1024 * 1024), "reply limit"),
    ],
)
def test_rpc_reply_line_always_answers_with_an_error(invoke, expect):
    """Every failing path still produces a reply: the child is blocked on it."""
    reply = node_sandbox._rpc_reply_line(7, "some_action", {"k": 1}, invoke)

    parsed = json.loads(reply)
    assert parsed["id"] == 7
    assert expect in parsed["error"], parsed


def test_rpc_reply_line_encodes_a_plain_result():
    """The happy path is a `result` envelope carrying the value."""
    reply = node_sandbox._rpc_reply_line(
        3, "wiki_read", {"page": "Home"}, lambda a, k: {"body": "hi", "asked": k}
    )

    assert json.loads(reply) == {
        "id": 3, "result": {"body": "hi", "asked": {"page": "Home"}}
    }


# -------------------------------------------------------------------
# Launch policy: injection only, never an environment variable
# -------------------------------------------------------------------


def test_module_reads_no_environment_variable():
    """Design D2 (R1 P0): no env flag can select the unsandboxed launcher.

    Four env_file sources reach production, so an env switch was a reachable
    escape hatch. The module must not read the environment at all.
    """
    src = inspect.getsource(node_sandbox)

    assert "os.environ" not in src
    assert "getenv" not in src
    assert "environb" not in src


def test_default_factory_returns_bwrap_when_the_probe_says_available(monkeypatch):
    """A host with bwrap gets the jail launcher."""
    monkeypatch.setattr(
        node_sandbox, "_probe", lambda: {"bwrap_available": True, "reason": None}
    )

    launcher = node_sandbox._default_launcher()

    assert isinstance(launcher, BwrapLauncher)
    assert launcher.name == "bwrap"


def test_no_sandbox_and_no_injected_launcher_raises(monkeypatch):
    """No bwrap and no injection: fail loudly, never run unsandboxed."""
    monkeypatch.setattr(
        node_sandbox, "DEFAULT_LAUNCHER_FACTORY", node_sandbox._default_launcher
    )
    monkeypatch.setattr(
        node_sandbox,
        "_probe",
        lambda: {"bwrap_available": False, "reason": "bwrap not found on PATH"},
    )
    sandbox = NodeSandbox(timeout=5.0)

    with pytest.raises(SandboxUnavailableError) as excinfo:
        sandbox.run_sync(
            node_id="unsandboxed",
            source_code="def run(state):\n    return {'ok': 1}\n",
            input_state={},
            input_keys=[],
            output_keys=["ok"],
        )

    assert "code nodes need the OS sandbox" in str(excinfo.value)
    assert "bwrap not found on PATH" in str(excinfo.value)


def test_injected_launcher_wins_over_the_factory(monkeypatch):
    """An explicitly injected launcher is used even when the factory refuses."""
    def _refuse():
        raise AssertionError("the factory must not be consulted")

    monkeypatch.setattr(node_sandbox, "DEFAULT_LAUNCHER_FACTORY", _refuse)
    sandbox = NodeSandbox(timeout=10.0, launcher=PlainSubprocessLauncher())

    result = sandbox.run_sync(
        node_id="injected",
        source_code="def run(state):\n    return {'ok': 1}\n",
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    assert sandbox.resolve_launcher().name == "plain"


def test_plain_launcher_is_documented_as_tests_only():
    """The unsandboxed launcher must say so, in the file, for the next reader."""
    doc = (PlainSubprocessLauncher.__doc__ or "").upper()

    assert "TESTS ONLY" in doc


# -------------------------------------------------------------------
# The parent's launch flags and the child's environment
# -------------------------------------------------------------------


def test_launchers_build_the_child_env_from_scratch(monkeypatch):
    """Host env vars (credentials, TINYASSETS_*) must not reach the child."""
    monkeypatch.setenv("TINYASSETS_TEST_SECRET", "should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")

    plain = PlainSubprocessLauncher().env("/tmp/private-home")
    jail = BwrapLauncher().env("/tmp/private-home")

    for env in (plain, jail):
        assert "TINYASSETS_TEST_SECRET" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "should-not-leak" not in "".join(env.values())
    assert plain["HOME"] == "/tmp/private-home"
    assert plain["PYTHONPATH"] == ""
    # bwrap --clearenv wipes the child's env anyway; the parent needs only a
    # lookup PATH, and never the host's.
    assert set(jail) == {"PATH"}


def test_parent_launch_uses_close_fds_pipes_and_a_private_cwd(monkeypatch):
    """The Popen call itself carries the isolation flags."""
    monkeypatch.setenv("TINYASSETS_TEST_SECRET", "should-not-leak")
    captured: dict[str, object] = {}
    real_popen = node_sandbox.subprocess.Popen

    def _spy(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        captured["cwd_existed"] = os.path.isdir(str(kwargs.get("cwd")))
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(node_sandbox.subprocess, "Popen", _spy)
    sandbox = NodeSandbox(timeout=10.0, launcher=PlainSubprocessLauncher())

    result = sandbox.run_sync(
        node_id="flags",
        source_code="def run(state):\n    return {'ok': 1}\n",
        input_state={},
        input_keys=[],
        output_keys=["ok"],
        timeout=10.0,
    )

    assert result.success is True, result.error
    assert captured["close_fds"] is True
    assert captured["stdin"] is subprocess.PIPE
    assert captured["stdout"] is subprocess.PIPE
    assert captured["stderr"] is subprocess.PIPE
    assert captured["cwd_existed"] is True
    assert str(captured["cwd"]) != os.getcwd()
    assert "TINYASSETS_TEST_SECRET" not in captured["env"]
    # The timeout and the rlimit requirement reach the child as argv, so the
    # child can set RLIMIT_CPU and decide fatal-vs-warning before it reads
    # anything from stdin. "0" here: the plain launcher is not the OS jail.
    # Three slots since the workspace profile joined them: timeout, whether
    # rlimits are mandatory, and the JSON profile ("" = the default profile).
    assert captured["argv"][-3:] == ["10.0", "0", ""]
    assert "preexec_fn" not in captured


def test_private_working_directory_is_removed_after_the_run(monkeypatch):
    """The child's cwd is a fresh temp dir, deleted afterwards."""
    sandbox = NodeSandbox(timeout=10.0, launcher=PlainSubprocessLauncher())
    created: list[str] = []
    real_mkdtemp = node_sandbox.tempfile.mkdtemp

    def _tracking_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(node_sandbox.tempfile, "mkdtemp", _tracking_mkdtemp)

    result = sandbox.run_sync(
        node_id="home",
        source_code="def run(state):\n    return {'ok': 1}\n",
        input_state={},
        input_keys=[],
        output_keys=["ok"],
    )

    assert result.success is True, result.error
    assert created, "run_sync must create a private working directory"
    assert not os.path.exists(created[-1]), "private cwd must be removed"


# -------------------------------------------------------------------
# bwrap argv shape (pure function — asserted on any OS)
# -------------------------------------------------------------------


def _posix_argv(**kwargs):
    """bwrap argv as it would be built on a Linux host, from any OS."""
    return node_sandbox._bwrap_argv(
        exists=kwargs.pop("exists", lambda p: p in {"/usr", "/bin", "/lib", "/lib64"}),
        bwrap_path=kwargs.pop("bwrap_path", "/usr/bin/bwrap"),
        realpath=kwargs.pop("realpath", lambda p: p),
        **kwargs,
    )


def test_bwrap_argv_isolates_network_env_and_session():
    """The jail shape: no network, no inherited env, dies with the parent."""
    argv = _posix_argv()

    assert argv[0] == "/usr/bin/bwrap"
    for flag in ("--die-with-parent", "--new-session", "--unshare-all", "--clearenv"):
        assert flag in argv, f"missing {flag}"
    assert "--share-net" not in argv
    assert argv[-1] == "--", "argv must end with the command separator"


def test_bwrap_argv_pins_the_working_directory_to_the_private_tmpfs():
    """--chdir /tmp: the child starts on the tmpfs, not in a bound directory."""
    argv = _posix_argv()

    assert "--chdir" in argv
    assert argv[argv.index("--chdir") + 1] == "/tmp"
    assert "--tmpfs" in argv
    assert argv[argv.index("--tmpfs") + 1] == "/tmp"


def test_bwrap_argv_never_mounts_the_data_dir():
    """No element may reference /data — the universe data dir stays outside."""
    argv = _posix_argv()

    offenders = [part for part in argv if "/data" in part]
    assert offenders == [], f"bwrap argv leaks the data dir: {offenders}"


def test_bwrap_argv_skips_a_data_dir_interpreter(monkeypatch):
    """Even an interpreter installed under /data is not bound in."""
    monkeypatch.setattr(node_sandbox.sys, "executable", "/data/venv/bin/python3")
    monkeypatch.setattr(node_sandbox.sys, "prefix", "/data/venv")
    monkeypatch.setattr(node_sandbox.sys, "base_prefix", "/data/venv")

    argv = _posix_argv(exists=lambda p: True)

    offenders = [part for part in argv if "/data" in part]
    assert offenders == [], f"bwrap argv leaks the data dir: {offenders}"


def test_bwrap_argv_binds_system_dirs_read_only():
    """System dirs are ro-bound; nothing is bound writable."""
    argv = _posix_argv()
    joined = " ".join(argv)

    for system_path in ("/usr", "/bin", "/lib", "/lib64"):
        assert f"--ro-bind {system_path} {system_path}" in joined
    assert "--setenv HOME /tmp" in joined
    assert "--bind" not in argv


def test_bwrap_launcher_appends_the_interpreter_and_runner():
    """The jail prefix is followed by the interpreter, the script and argv."""
    argv = BwrapLauncher(bwrap_path="/usr/bin/bwrap").build_argv(
        "SCRIPT", ["12.5", "1"]
    )

    separator = argv.index("--")
    assert argv[separator + 1] == sys.executable
    assert argv[separator + 2] == "-c"
    assert argv[separator + 3] == "SCRIPT"
    assert argv[separator + 4:] == ["12.5", "1"]


def test_the_jail_launch_demands_rlimits(monkeypatch):
    """Under bwrap the child is told its limits are mandatory."""
    captured: dict[str, object] = {}

    def _spy(argv, **kwargs):
        captured["argv"] = argv
        raise OSError("not launching bwrap on this host")

    monkeypatch.setattr(node_sandbox.subprocess, "Popen", _spy)
    sandbox = NodeSandbox(timeout=8.0, launcher=BwrapLauncher())

    result = sandbox.run_sync(
        node_id="jail-argv",
        source_code="def run(state):\n    return {'ok': 1}\n",
        input_state={},
        input_keys=[],
        output_keys=["ok"],
        timeout=8.0,
    )

    assert result.success is False
    assert "Failed to start subprocess" in result.error
    assert captured["argv"][-3:] == ["8.0", "1", ""]


# -------------------------------------------------------------------
# Adversarial: the jail itself. Linux + bwrap only; the production host
# runs these as the live proof of design D2.
# -------------------------------------------------------------------


@pytest.fixture
def jail_sandbox(monkeypatch):
    """A real bwrap sandbox with the *software* guards disabled.

    The denylist and the import allowlist would refuse this code long before
    the OS boundary was reached, and refusing early is what they are for.
    These tests are about the boundary underneath them: what happens when
    hostile code does run.
    """
    monkeypatch.setattr(
        node_sandbox,
        "ALLOWED_IMPORTS",
        set(node_sandbox.ALLOWED_IMPORTS) | {"os", "socket"},
    )
    monkeypatch.setattr(NodeSandbox, "validate_source", lambda self, src: [])
    return NodeSandbox(timeout=30.0, launcher=BwrapLauncher())


@pytest.mark.real_jail
@requires_bwrap
def test_jail_runs_a_node_at_all(jail_sandbox):
    """Positive control: without this, every refusal below could be a broken jail."""
    result = jail_sandbox.run_sync(
        node_id="jail-control",
        source_code="def run(state):\n    return {'r': 2 + 2}\n",
        input_state={},
        input_keys=[],
        output_keys=["r"],
    )

    assert result.success is True, f"{result.error} | {result.stderr_tail}"
    assert result.output_state == {"r": 4}


@pytest.mark.real_jail
@requires_bwrap
def test_jail_has_no_network(jail_sandbox):
    """--unshare-all with no --share-net: an outbound connection cannot open."""
    source = (
        "import socket\n"
        "def run(state):\n"
        "    try:\n"
        "        conn = socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
        "        conn.close()\n"
        "        return {'r': 'LEAKED: connected'}\n"
        "    except Exception as exc:\n"
        "        return {'r': 'refused: ' + type(exc).__name__}\n"
    )

    result = jail_sandbox.run_sync(
        node_id="jail-net",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["r"],
    )

    assert result.success is True, f"{result.error} | {result.stderr_tail}"
    assert result.output_state["r"].startswith("refused:"), result.output_state


@pytest.mark.real_jail
@requires_bwrap
def test_jail_cannot_read_the_hosts_process_environment(jail_sandbox, monkeypatch):
    """/proc is the jail's own; the parent's secrets are not reachable."""
    canary = "ta-jail-canary-8f31c0"
    monkeypatch.setenv("TINYASSETS_JAIL_CANARY", canary)
    source = (
        "def run(state):\n"
        "    try:\n"
        "        data = open('/proc/1/environ', 'rb').read()\n"
        "    except Exception as exc:\n"
        "        return {'r': 'refused: ' + type(exc).__name__}\n"
        "    return {'r': data.decode('utf-8', 'replace')}\n"
    )

    result = jail_sandbox.run_sync(
        node_id="jail-proc",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["r"],
    )

    assert result.success is True, f"{result.error} | {result.stderr_tail}"
    body = result.output_state["r"]
    assert canary not in body, "the host process environment leaked into the jail"
    assert body.startswith("refused:") or "TINYASSETS" not in body


@pytest.mark.real_jail
@requires_bwrap
def test_jail_cannot_see_the_data_dir(jail_sandbox):
    """No /data mount: the universe data dir does not exist in the child."""
    source = (
        "import os\n"
        "def run(state):\n"
        "    try:\n"
        "        return {'r': 'LEAKED: ' + repr(sorted(os.listdir('/data'))[:5])}\n"
        "    except Exception as exc:\n"
        "        return {'r': 'refused: ' + type(exc).__name__}\n"
    )

    result = jail_sandbox.run_sync(
        node_id="jail-data",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["r"],
    )

    assert result.success is True, f"{result.error} | {result.stderr_tail}"
    assert result.output_state["r"] == "refused: FileNotFoundError"


@pytest.mark.real_jail
@requires_bwrap
def test_jail_cannot_read_unbound_host_files(jail_sandbox):
    """/etc is not bound: host identity files are not readable."""
    source = (
        "def run(state):\n"
        "    try:\n"
        "        return {'r': 'LEAKED: ' + open('/etc/hostname').read()}\n"
        "    except Exception as exc:\n"
        "        return {'r': 'refused: ' + type(exc).__name__}\n"
    )

    result = jail_sandbox.run_sync(
        node_id="jail-etc",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["r"],
    )

    assert result.success is True, f"{result.error} | {result.stderr_tail}"
    assert result.output_state["r"].startswith("refused:"), result.output_state


@pytest.mark.real_jail
@requires_bwrap
def test_jail_working_directory_is_the_private_tmpfs(jail_sandbox):
    """--chdir /tmp: the node starts on scratch space, not in a bound tree."""
    source = (
        "import os\n"
        "def run(state):\n"
        "    return {'r': os.getcwd()}\n"
    )

    result = jail_sandbox.run_sync(
        node_id="jail-cwd",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["r"],
    )

    assert result.success is True, f"{result.error} | {result.stderr_tail}"
    assert result.output_state == {"r": "/tmp"}

"""Tests for sandbox availability surface.

Spec: openspec/specs/scratch-storage/spec.md §Loud sandbox-unavailable surface.

Covers:
  * tinyassets.providers.base.check_bwrap_failure — raises SandboxUnavailableError on match.
  * tinyassets.providers.base.get_sandbox_status — probe result shape + caching.
  * tinyassets.universe_server.get_status — sandbox_status field present in return.
  * tinyassets.graph_compiler — SandboxUnavailableError propagates rather than being
    swallowed into a generic CompilerError. (Requires graph_compiler fix to pass —
    marked xfail until that lands; the fix is to re-raise before the generic
    `except Exception` catch at lines 834/848.)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tinyassets.providers.base import (
    _BWRAP_FAILURE_PATTERNS,
    SandboxUnavailableError,
    check_bwrap_failure,
    get_sandbox_status,
    probe_sandbox_available,
)

# ─── check_bwrap_failure ─────────────────────────────────────────────────────

class TestCheckBwrapFailure:
    def test_raises_on_namespace_pattern(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        with pytest.raises(SandboxUnavailableError):
            check_bwrap_failure("bwrap: No permissions to create a new namespace")

    def test_raises_on_no_such_file_pattern(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        with pytest.raises(SandboxUnavailableError):
            check_bwrap_failure("bwrap: No such file or directory")

    def test_raises_on_sandbox_init_failed(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        with pytest.raises(SandboxUnavailableError):
            check_bwrap_failure("sandbox initialization failed: something went wrong")

    def test_case_insensitive_match(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        with pytest.raises(SandboxUnavailableError):
            check_bwrap_failure("BWRAP: NO PERMISSIONS TO CREATE A NEW NAMESPACE")

    def test_no_raise_on_normal_output(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        check_bwrap_failure("Successfully compiled the node")
        check_bwrap_failure("")
        check_bwrap_failure("some other error without the magic string")

    def test_error_message_contains_guidance(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        with pytest.raises(SandboxUnavailableError, match="Fix options"):
            check_bwrap_failure("bwrap: No permissions to create a new namespace")

    def test_all_patterns_raise(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        for pattern in _BWRAP_FAILURE_PATTERNS:
            with pytest.raises(SandboxUnavailableError):
                check_bwrap_failure(pattern)

    def test_noop_on_windows(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        # Must not raise even with bwrap signature.
        check_bwrap_failure("bwrap: No permissions to create a new namespace")


# ─── probe_sandbox_available / get_sandbox_status ────────────────────────────

class TestProbeSandboxAvailable:
    def test_win32_returns_unavailable(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "win32" in str(result.get("reason", "")).lower()

    def test_bwrap_not_on_path_returns_unavailable(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        with patch("shutil.which", return_value=None):
            result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "PATH" in str(result.get("reason", ""))

    def test_bwrap_version_and_launch_succeed_returns_available(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        version_result = type("R", (), {"returncode": 0, "stderr": "", "stdout": "bwrap 0.6.0"})()
        launch_result = type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            with patch("subprocess.run", side_effect=[version_result, launch_result]) as run_mock:
                result = probe_sandbox_available()
        assert result["bwrap_available"] is True
        assert result.get("reason") is None
        assert run_mock.call_count == 2

    def test_bwrap_launch_fails_returns_unavailable(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        version_result = type("R", (), {"returncode": 0, "stderr": "", "stdout": "bwrap 0.6.0"})()
        launch_result = type("R", (), {
            "returncode": 1,
            "stderr": "bwrap: No permissions to create new namespace",
            "stdout": "",
        })()
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            with patch("subprocess.run", side_effect=[version_result, launch_result]):
                result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "functional probe" in str(result.get("reason", ""))
        assert "No permissions" in str(result.get("reason", ""))

    def test_bwrap_version_fails_returns_unavailable(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        mock_result = type("R", (), {"returncode": 1, "stderr": "permission denied"})()
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            with patch("subprocess.run", return_value=mock_result):
                result = probe_sandbox_available()
        assert result["bwrap_available"] is False

    def test_probe_exception_returns_unavailable(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            with patch("subprocess.run", side_effect=OSError("spawn failed")):
                result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "probe error" in str(result.get("reason", "")).lower()


class TestGetSandboxStatus:
    def test_returns_dict_with_bwrap_available(self):
        import tinyassets.providers.base as base_mod
        original = base_mod._sandbox_probe_cache
        try:
            base_mod._sandbox_probe_cache = None
            with patch.object(
                base_mod, "probe_sandbox_available",
                return_value={"bwrap_available": False, "reason": "test"},
            ):
                result = get_sandbox_status()
        finally:
            base_mod._sandbox_probe_cache = original
        assert "bwrap_available" in result

    def test_caches_after_first_call(self):
        import tinyassets.providers.base as base_mod
        original = base_mod._sandbox_probe_cache
        try:
            base_mod._sandbox_probe_cache = None
            call_count = 0

            def counting_probe():
                nonlocal call_count
                call_count += 1
                return {"bwrap_available": False, "reason": "test"}

            with patch.object(base_mod, "probe_sandbox_available", side_effect=counting_probe):
                get_sandbox_status()
                get_sandbox_status()
                get_sandbox_status()
        finally:
            base_mod._sandbox_probe_cache = original
        assert call_count == 1


# ─── get_status includes sandbox_status field ────────────────────────────────

class TestGetStatusSandboxField:
    @pytest.fixture(autouse=True)
    def _home(self, founder_home):
        """The operator has a home, so status reads a universe rather than
        answering with the first-contact card (no-anonymous-principal)."""
        self._universe = founder_home
        return founder_home

    def _call_get_status(self, monkeypatch, tmp_path):
        """Call get_status with a minimal universe dir + mocked sandbox probe."""
        import tinyassets.providers.base as base_mod
        original = base_mod._sandbox_probe_cache
        base_mod._sandbox_probe_cache = {"bwrap_available": False, "reason": "test-host"}
        try:
            from tinyassets.universe_server import get_status
            result_str = get_status()
        finally:
            base_mod._sandbox_probe_cache = original
        return json.loads(result_str)

    def test_sandbox_status_key_present(self, monkeypatch, tmp_path):
        result = self._call_get_status(monkeypatch, tmp_path)
        assert "sandbox_status" in result

    def test_sandbox_status_has_bwrap_available(self, monkeypatch, tmp_path):
        result = self._call_get_status(monkeypatch, tmp_path)
        assert "bwrap_available" in result["sandbox_status"]

    def test_sandbox_status_bwrap_false_on_test_host(self, monkeypatch, tmp_path):
        result = self._call_get_status(monkeypatch, tmp_path)
        assert result["sandbox_status"]["bwrap_available"] is False

    def test_sandbox_status_probe_error_handled_gracefully(self, monkeypatch, tmp_path):
        """If probe raises, get_status still returns sandbox_status with error reason."""
        import tinyassets.providers.base as base_mod
        original = base_mod._sandbox_probe_cache
        base_mod._sandbox_probe_cache = None
        try:
            with patch.object(
                base_mod, "probe_sandbox_available",
                side_effect=RuntimeError("probe exploded"),
            ):
                from tinyassets.universe_server import get_status
                monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
                result = json.loads(get_status())
        finally:
            base_mod._sandbox_probe_cache = original
        assert "sandbox_status" in result
        assert result["sandbox_status"]["bwrap_available"] is False
        assert "probe_error" in str(result["sandbox_status"].get("reason", ""))


# ─── graph_compiler: SandboxUnavailableError propagates (not swallowed) ──────

class TestGraphCompilerSandboxPropagation:
    def _make_branch(self):
        from tinyassets.branches import (
            BranchDefinition,
            EdgeDefinition,
            GraphNodeRef,
            NodeDefinition,
        )
        nd = NodeDefinition(
            node_id="n1",
            display_name="N1",
            prompt_template="write something",
        )
        return BranchDefinition(
            branch_def_id="test-sandbox",
            name="Sandbox Test",
            entry_point="n1",
            node_defs=[nd],
            graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
            edges=[EdgeDefinition(from_node="n1", to_node="END")],
            state_schema=[{"name": "output", "type": "str"}],
        )

    def test_provider_raising_sandbox_error_propagates(self, tmp_path):
        """SandboxUnavailableError raised by provider_call must NOT be
        swallowed into a generic CompilerError — it must propagate so callers
        can surface the sandbox-unavailable guidance to users."""
        import os
        import tempfile

        from tinyassets.graph_compiler import compile_branch
        from tinyassets.providers.base import SandboxUnavailableError

        branch = self._make_branch()

        def _raising_provider(prompt, system, *, role="user"):
            raise SandboxUnavailableError(
                "bwrap: No permissions to create a new namespace"
            )

        compiled = compile_branch(branch, provider_call=_raising_provider)
        from langgraph.checkpoint.sqlite import SqliteSaver

        with tempfile.TemporaryDirectory() as td:
            saver_path = os.path.join(td, "check.db")
            with SqliteSaver.from_conn_string(saver_path) as checkpointer:
                app = compiled.graph.compile(checkpointer=checkpointer)
                with pytest.raises(SandboxUnavailableError):
                    app.invoke(
                        {},
                        config={"configurable": {"thread_id": "t1"}, "recursion_limit": 10},
                    )

    def test_bwrap_text_in_response_raises(self, tmp_path, monkeypatch):
        """If provider returns bwrap-failure text as output (defense-in-depth),
        graph_compiler must raise SandboxUnavailableError rather than storing garbage."""
        import os
        import tempfile

        from tinyassets.graph_compiler import compile_branch
        from tinyassets.providers.base import SandboxUnavailableError

        monkeypatch.setattr("sys.platform", "linux")
        branch = self._make_branch()

        def _leaking_provider(prompt, system, *, role="user"):
            return "bwrap: No permissions to create a new namespace"

        compiled = compile_branch(branch, provider_call=_leaking_provider)
        from langgraph.checkpoint.sqlite import SqliteSaver

        with tempfile.TemporaryDirectory() as td:
            saver_path = os.path.join(td, "check.db")
            with SqliteSaver.from_conn_string(saver_path) as checkpointer:
                app = compiled.graph.compile(checkpointer=checkpointer)
                with pytest.raises(SandboxUnavailableError):
                    app.invoke(
                        {},
                        config={"configurable": {"thread_id": "t1"}, "recursion_limit": 10},
                    )

    def test_normal_provider_output_does_not_raise(self, tmp_path):
        """Normal provider output must pass through without raising."""
        import os
        import tempfile

        from tinyassets.graph_compiler import compile_branch

        branch = self._make_branch()

        def _normal_provider(prompt, system, *, role="user"):
            return "hello, world"

        compiled = compile_branch(branch, provider_call=_normal_provider)
        from langgraph.checkpoint.sqlite import SqliteSaver

        with tempfile.TemporaryDirectory() as td:
            saver_path = os.path.join(td, "check.db")
            with SqliteSaver.from_conn_string(saver_path) as checkpointer:
                app = compiled.graph.compile(checkpointer=checkpointer)
                # Should not raise — normal output passes through cleanly
                app.invoke(
                    {},
                    config={"configurable": {"thread_id": "t1"}, "recursion_limit": 10},
                )

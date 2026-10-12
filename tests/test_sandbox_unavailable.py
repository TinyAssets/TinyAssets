"""Tests for the loud sandbox-unavailable surface + design-only branch role.

Spec: openspec/specs/scratch-storage/spec.md.
Topic: Loud sandbox-unavailable surface for dev/checker exec nodes.

Covered:
- check_bwrap_failure raises SandboxUnavailableError on bwrap failure signature
- check_bwrap_failure no-ops on normal stderr output
- check_bwrap_failure no-ops on win32 regardless of content
- probe_sandbox_available returns available only when bwrap --version and launch succeed
- probe_sandbox_available returns correct shape when bwrap not on PATH
- probe_sandbox_available returns correct shape when bwrap --version fails
- probe_sandbox_available rejects bwrap launch/user-namespace failure
- get_sandbox_status returns cached result (probe called exactly once)
- get_status includes sandbox_status key
- _ext_branch_validate warns on bwrap-unavailable host with requires_sandbox nodes
- _ext_branch_validate clean (no warnings) on design-only branches
- _ext_branch_validate clean (no warnings) when bwrap is available
- _ext_branch_list requires_sandbox filter: "none" excludes sandbox branches
- _ext_branch_list requires_sandbox filter: "any" excludes design-only branches
- _ext_branch_list no filter returns all branches with has_sandbox_nodes field
- ClaudeProvider.complete raises SandboxUnavailableError on bwrap stderr
- CodexProvider.complete raises SandboxUnavailableError on bwrap stderr
- requires_sandbox=False is the default on NodeDefinition (back-compat)
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from tests.support.owned_spawn import fake_owned_spawn
from tinyassets.branches import BranchDefinition, NodeDefinition
from tinyassets.providers.base import (
    SandboxUnavailableError,
    check_bwrap_failure,
    probe_sandbox_available,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_branch(*, has_sandbox_node: bool = False, branch_def_id: str = "b1") -> BranchDefinition:
    nd = NodeDefinition(
        node_id="n1",
        display_name="n1",
        requires_sandbox=has_sandbox_node,
    )
    return BranchDefinition(
        branch_def_id=branch_def_id,
        name="test",
        node_defs=[nd],
    )


def _branch_as_dict(branch: BranchDefinition) -> dict:
    nd = branch.node_defs[0]
    return {
        "branch_def_id": branch.branch_def_id,
        "name": branch.name,
        "node_defs": [{"node_id": nd.node_id, "requires_sandbox": nd.requires_sandbox}],
        "published": False,
        "visibility": "public",
        "author": "",
        "domain_id": "",
        "goal_id": "",
    }


# ---------------------------------------------------------------------------
# check_bwrap_failure
# ---------------------------------------------------------------------------

class TestCheckBwrapFailure:
    def test_raises_on_bwrap_no_permissions(self):
        with patch.object(sys, "platform", "linux"):
            with pytest.raises(SandboxUnavailableError):
                check_bwrap_failure("bwrap: No permissions to create a new namespace\n")

    def test_raises_on_bwrap_no_permissions_without_article(self):
        with patch.object(sys, "platform", "linux"):
            with pytest.raises(SandboxUnavailableError):
                check_bwrap_failure("bwrap: No permissions to create new namespace\n")

    def test_raises_on_sandbox_init_failed(self):
        with patch.object(sys, "platform", "linux"):
            with pytest.raises(SandboxUnavailableError):
                check_bwrap_failure("sandbox initialization failed")

    def test_raises_on_bwrap_no_such_file(self):
        with patch.object(sys, "platform", "linux"):
            with pytest.raises(SandboxUnavailableError):
                check_bwrap_failure("bwrap: No such file or directory")

    def test_noop_on_normal_stderr(self):
        with patch.object(sys, "platform", "linux"):
            # Must not raise
            check_bwrap_failure("some normal warning output\n")

    def test_noop_on_empty_stderr(self):
        with patch.object(sys, "platform", "linux"):
            check_bwrap_failure("")

    def test_noop_on_win32_even_with_bwrap_text(self):
        # On win32 the check is always skipped; bwrap is Linux-only.
        with patch.object(sys, "platform", "win32"):
            check_bwrap_failure("bwrap: No permissions to create a new namespace")

    def test_case_insensitive_match(self):
        with patch.object(sys, "platform", "linux"):
            with pytest.raises(SandboxUnavailableError):
                check_bwrap_failure("BWRAP: NO PERMISSIONS TO CREATE A NEW NAMESPACE")

    def test_error_message_includes_excerpt(self):
        stderr = "bwrap: No permissions to create a new namespace\nmore info"
        with patch.object(sys, "platform", "linux"):
            with pytest.raises(SandboxUnavailableError, match="sandbox"):
                check_bwrap_failure(stderr)


# ---------------------------------------------------------------------------
# probe_sandbox_available / get_sandbox_status
# ---------------------------------------------------------------------------

class TestProbeSandboxAvailable:
    def test_returns_unavailable_on_win32(self):
        with patch.object(sys, "platform", "win32"):
            result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "win32" in result.get("reason", "")

    def test_returns_unavailable_when_bwrap_not_on_path(self):
        with patch.object(sys, "platform", "linux"):
            with patch("shutil.which", return_value=None):
                result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "not found" in result.get("reason", "")

    def test_returns_available_when_bwrap_version_and_launch_succeed(self):
        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stderr = ""
        launch_result = MagicMock()
        launch_result.returncode = 0
        launch_result.stderr = ""
        with patch.object(sys, "platform", "linux"):
            with patch("shutil.which", return_value="/usr/bin/bwrap"):
                with patch(
                    "subprocess.run",
                    side_effect=[version_result, launch_result],
                ) as run_mock:
                    result = probe_sandbox_available()
        assert result["bwrap_available"] is True
        assert result["reason"] is None
        assert run_mock.call_count == 2

    def test_returns_unavailable_when_bwrap_launch_nonzero(self):
        version_result = MagicMock()
        version_result.returncode = 0
        version_result.stderr = ""
        launch_result = MagicMock()
        launch_result.returncode = 1
        launch_result.stdout = ""
        launch_result.stderr = "bwrap: No permissions to create new namespace"
        with patch.object(sys, "platform", "linux"):
            with patch("shutil.which", return_value="/usr/bin/bwrap"):
                with patch("subprocess.run", side_effect=[version_result, launch_result]):
                    result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "functional probe" in result.get("reason", "")
        assert "No permissions" in result.get("reason", "")

    def test_returns_unavailable_when_bwrap_version_nonzero(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "permission denied"
        with patch.object(sys, "platform", "linux"):
            with patch("shutil.which", return_value="/usr/bin/bwrap"):
                with patch("subprocess.run", return_value=mock_result):
                    result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "permission denied" in result.get("reason", "")

    def test_returns_unavailable_on_probe_exception(self):
        with patch.object(sys, "platform", "linux"):
            with patch("shutil.which", return_value="/usr/bin/bwrap"):
                with patch("subprocess.run", side_effect=OSError("boom")):
                    result = probe_sandbox_available()
        assert result["bwrap_available"] is False
        assert "probe error" in result.get("reason", "")


class TestGetSandboxStatusCaching:
    def test_probe_called_once_across_multiple_calls(self):
        import tinyassets.providers.base as base_mod
        original_cache = base_mod._sandbox_probe_cache
        try:
            base_mod._sandbox_probe_cache = None
            call_count = 0

            def fake_probe():
                nonlocal call_count
                call_count += 1
                return {"bwrap_available": False, "reason": "test"}

            with patch.object(base_mod, "probe_sandbox_available", fake_probe):
                r1 = base_mod.get_sandbox_status()
                r2 = base_mod.get_sandbox_status()
                r3 = base_mod.get_sandbox_status()

            assert call_count == 1, "probe should only be called once"
            assert r1 is r2 is r3
        finally:
            base_mod._sandbox_probe_cache = original_cache


# ---------------------------------------------------------------------------
# NodeDefinition.requires_sandbox default
# ---------------------------------------------------------------------------

class TestNodeDefinitionRequiresSandbox:
    def test_default_is_false(self):
        nd = NodeDefinition(node_id="x", display_name="x")
        assert nd.requires_sandbox is False

    def test_can_be_set_true(self):
        nd = NodeDefinition(node_id="x", display_name="x", requires_sandbox=True)
        assert nd.requires_sandbox is True

    def test_from_dict_roundtrip(self):
        nd = NodeDefinition(node_id="x", display_name="x", requires_sandbox=True)
        d = nd.to_dict()
        nd2 = NodeDefinition.from_dict(d)
        assert nd2.requires_sandbox is True

    def test_from_dict_missing_key_defaults_false(self):
        d = {"node_id": "x", "display_name": "x"}
        nd = NodeDefinition.from_dict(d)
        assert nd.requires_sandbox is False


# ---------------------------------------------------------------------------
# _ext_branch_validate sandbox warnings
# ---------------------------------------------------------------------------

class TestExtBranchValidateSandboxWarnings:
    def _call_validate(self, branch_dict: dict, bwrap_status: dict) -> dict:
        from tinyassets.api.branches import _ext_branch_validate

        with patch("tinyassets.daemon_server.get_branch_definition", return_value=branch_dict):
            with patch("tinyassets.providers.base.get_sandbox_status", return_value=bwrap_status):
                return json.loads(_ext_branch_validate({"branch_def_id": "b1"}))

    def test_warns_when_sandbox_unavailable_and_sandbox_node(self):
        branch = _make_branch(has_sandbox_node=True)
        branch_dict = branch.to_dict()
        result = self._call_validate(
            branch_dict,
            {"bwrap_available": False, "reason": "bwrap not found on PATH"},
        )
        # sandbox_warnings is non-fatal — it appears alongside whatever validate() says
        assert len(result["sandbox_warnings"]) == 1
        assert "n1" in result["sandbox_warnings"][0]

    def test_no_warnings_for_design_only_branch_when_sandbox_unavailable(self):
        branch = _make_branch(has_sandbox_node=False)
        branch_dict = branch.to_dict()
        result = self._call_validate(
            branch_dict,
            {"bwrap_available": False, "reason": "bwrap not found on PATH"},
        )
        assert result["sandbox_warnings"] == []

    def test_no_warnings_when_sandbox_available_even_with_sandbox_node(self):
        branch = _make_branch(has_sandbox_node=True)
        branch_dict = branch.to_dict()
        result = self._call_validate(
            branch_dict,
            {"bwrap_available": True, "reason": None},
        )
        assert result["sandbox_warnings"] == []

    def test_missing_branch_returns_error(self):
        from tinyassets.api.branches import _ext_branch_validate

        # An absent ID also falls back to the caller-visible name catalogue.
        # Model both not-found reads; never rely on a prepopulated global DB.
        with (
            patch("tinyassets.daemon_server.get_branch_definition", side_effect=KeyError("b1")),
            patch("tinyassets.daemon_server.list_branch_definitions", return_value=[]),
        ):
            result = json.loads(_ext_branch_validate({"branch_def_id": "b1"}))
        assert "error" in result

    def test_missing_branch_def_id_returns_error(self):
        from tinyassets.api.branches import _ext_branch_validate

        result = json.loads(_ext_branch_validate({}))
        assert "error" in result


# ---------------------------------------------------------------------------
# _ext_branch_list requires_sandbox filter
# ---------------------------------------------------------------------------

class TestExtBranchListSandboxFilter:
    def _call_list(self, rows: list[dict], rs_filter: str = "") -> dict:
        from tinyassets.api.branches import _ext_branch_list

        with patch("tinyassets.daemon_server.list_branch_definitions", return_value=rows):
            # scope="all" is required, not incidental: the DEFAULT scope is
            # "published", which re-queries `list_branch_versions` per row and
            # drops any branch with no published version. These rows are
            # constructed in-memory and were never published, so the default
            # filtered all of them out and every count came back 0 — masking
            # what this class actually tests, the requires_sandbox filter.
            kwargs: dict = {"scope": "all"}
            if rs_filter:
                kwargs["requires_sandbox"] = rs_filter
            return json.loads(_ext_branch_list(kwargs))

    def _make_rows(self) -> list[dict]:
        sandbox_branch = _branch_as_dict(_make_branch(has_sandbox_node=True, branch_def_id="sb"))
        design_branch = _branch_as_dict(_make_branch(has_sandbox_node=False, branch_def_id="db"))
        return [sandbox_branch, design_branch]

    def test_no_filter_returns_all_with_has_sandbox_nodes(self):
        rows = self._make_rows()
        result = self._call_list(rows)
        assert result["count"] == 2
        ids = {b["branch_def_id"] for b in result["branches"]}
        assert "sb" in ids
        assert "db" in ids
        # has_sandbox_nodes field present on both
        for b in result["branches"]:
            assert "has_sandbox_nodes" in b

    def test_filter_none_excludes_sandbox_branches(self):
        rows = self._make_rows()
        result = self._call_list(rows, rs_filter="none")
        assert result["count"] == 1
        assert result["branches"][0]["branch_def_id"] == "db"
        assert result["branches"][0]["has_sandbox_nodes"] is False

    def test_filter_any_excludes_design_only_branches(self):
        rows = self._make_rows()
        result = self._call_list(rows, rs_filter="any")
        assert result["count"] == 1
        assert result["branches"][0]["branch_def_id"] == "sb"
        assert result["branches"][0]["has_sandbox_nodes"] is True

    def test_filter_none_on_empty_corpus_returns_empty(self):
        result = self._call_list([], rs_filter="none")
        assert result["count"] == 0
        assert result["branches"] == []

    def test_filter_any_on_all_design_only_returns_empty(self):
        design_branch = _branch_as_dict(_make_branch(has_sandbox_node=False))
        result = self._call_list([design_branch], rs_filter="any")
        assert result["count"] == 0

    def test_unknown_filter_value_passes_all_through(self):
        rows = self._make_rows()
        result = self._call_list(rows, rs_filter="invalid_value")
        assert result["count"] == 2


# ---------------------------------------------------------------------------
# get_status includes sandbox_status
# ---------------------------------------------------------------------------

class TestGetStatusSandboxStatus:
    """These read the FULL status shape, which needs a universe to report on.

    They used to get it by being nobody: an unauthenticated caller fell through
    to the single-tenant default universe. A signed-in founder with no home
    gets the short "no universe bound to this account yet" shape, which is
    correct and carries no sandbox block -- so the tests bind a home.
    """

    def test_sandbox_status_key_present(self, founder_home):
        from tinyassets.universe_server import get_status
        fake_status = {"bwrap_available": False, "reason": "test host"}
        with patch("tinyassets.providers.base.get_sandbox_status", return_value=fake_status):
            result = json.loads(get_status())
        assert "sandbox_status" in result, result
        assert result["sandbox_status"]["bwrap_available"] is False

    def test_sandbox_status_survives_probe_exception(self, founder_home):
        from tinyassets.universe_server import get_status
        _target = "tinyassets.providers.base.get_sandbox_status"
        with patch(_target, side_effect=RuntimeError("probe fail")):
            result = json.loads(get_status())
        assert "sandbox_status" in result, result
        assert result["sandbox_status"]["bwrap_available"] is False


# ---------------------------------------------------------------------------
# ClaudeProvider + CodexProvider raise on bwrap stderr
# ---------------------------------------------------------------------------

def _make_proc_mock(returncode: int, stdout: bytes, stderr: bytes) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()

    async def _wait():
        return None

    proc.wait = _wait

    async def communicate(input=None):  # noqa: A002
        return stdout, stderr

    proc.communicate = communicate
    return proc


def _make_stream_proc(returncode: int, stdout_lines: list[bytes], stderr: bytes):
    """A streaming-reader-compatible fake subprocess (ClaudeProvider.complete now
    reads ``proc.stdout.readline()`` NDJSON + drains ``proc.stderr.read()`` instead
    of ``communicate()``). bwrap detection still fires on the drained stderr."""

    class _Stdout:
        def __init__(self, lines):
            self._lines = list(lines)

        async def readline(self):
            return self._lines.pop(0) if self._lines else b""

    class _Stderr:
        def __init__(self, data):
            self._data = data
            self._sent = False

        async def read(self, _n):
            if self._sent:
                return b""
            self._sent = True
            return self._data

    class _Stdin:
        def write(self, _b): ...
        async def drain(self): ...
        def close(self): ...

    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.stdout = _Stdout(stdout_lines)
    proc.stderr = _Stderr(stderr)
    proc.stdin = _Stdin()

    async def _wait():
        return returncode

    proc.wait = _wait
    return proc


def _sj(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


class TestClaudeProviderBwrapDetection:
    def test_raises_sandbox_unavailable_on_bwrap_stderr(self):
        # ClaudeProvider.complete now STREAMS (stream-json reader), so use a
        # streaming-compatible fake. bwrap detection still fires on drained stderr.
        from tinyassets.providers.base import ModelConfig
        from tinyassets.providers.claude_provider import ClaudeProvider

        bwrap_stderr = b"bwrap: No permissions to create a new namespace\n"
        # stdout EOF immediately; stderr carries the bwrap failure signature.
        proc = _make_stream_proc(returncode=0, stdout_lines=[], stderr=bwrap_stderr)

        provider = ClaudeProvider()

        with patch.object(sys, "platform", "linux"):
            with fake_owned_spawn(
                "tinyassets.providers.claude_provider", return_value=proc,
            ):
                with pytest.raises(SandboxUnavailableError):
                    asyncio.run(provider.complete("hello", "", ModelConfig()))

    def test_normal_stderr_does_not_raise(self):
        from tinyassets.providers.base import ModelConfig
        from tinyassets.providers.claude_provider import ClaudeProvider

        # A well-formed stream-json turn (init + terminal result) with benign stderr.
        proc = _make_stream_proc(
            returncode=0,
            stdout_lines=[
                _sj({"type": "system", "subtype": "init", "session_id": "s"}),
                _sj({"type": "result", "subtype": "success", "result": "response text"}),
            ],
            stderr=b"normal warning",
        )

        provider = ClaudeProvider()

        with patch.object(sys, "platform", "linux"):
            with fake_owned_spawn(
                "tinyassets.providers.claude_provider", return_value=proc,
            ):
                result = asyncio.run(provider.complete("hello", "", ModelConfig()))
        assert result.text == "response text"


class TestCodexProviderBwrapDetection:
    def test_raises_sandbox_unavailable_on_bwrap_stderr(self):
        from tinyassets.providers.base import ModelConfig
        from tinyassets.providers.codex_provider import CodexProvider

        bwrap_stderr = b"bwrap: No permissions to create a new namespace\n"
        proc = _make_proc_mock(returncode=0, stdout=b"some output", stderr=bwrap_stderr)

        provider = CodexProvider()

        with patch.object(sys, "platform", "linux"):
            with fake_owned_spawn(
                "tinyassets.providers.codex_provider", return_value=proc,
            ):
                with pytest.raises(SandboxUnavailableError):
                    asyncio.run(provider.complete("hello", "", ModelConfig()))

    def test_normal_stderr_does_not_raise(self):
        from tinyassets.providers.base import ModelConfig
        from tinyassets.providers.codex_provider import CodexProvider

        proc = _make_proc_mock(returncode=0, stdout=b"codex output", stderr=b"info log")

        provider = CodexProvider()

        with patch.object(sys, "platform", "linux"):
            with fake_owned_spawn(
                "tinyassets.providers.codex_provider", return_value=proc,
            ):
                result = asyncio.run(provider.complete("hello", "", ModelConfig()))
        assert result.text == "codex output"

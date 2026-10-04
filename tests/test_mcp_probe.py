"""Tests for scripts/mcp_probe.py — stdlib MCP client over streamable-http.

Covers:
  (a) SSE parse: single-event and multi-event bodies
  (b) session-id capture from initialize response + reuse in subsequent calls
  (c) --list output: prints tool names and descriptions
  (d) --tool with --args: calls tools/call, prints text content
  (e) --raw: prints full JSON
  (f) Error paths: non-200 (urllib raises), malformed JSON in SSE body
  (g) isError result returns exit code 1
  (h) --tool not supplied and --list not set → exit 2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import mcp_probe  # noqa: E402


@pytest.fixture(autouse=True)
def _canary_token(monkeypatch):
    import _canary_common

    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "t" * 40)
    # This daemon keeps the no-anonymous contract. Stated here rather than
    # discovered over the network: the probe asks GET <url>/pulse, and a test
    # that leaves that to a stub would consume a scripted response meant for
    # `initialize`.
    monkeypatch.setattr(
        mcp_probe, "canary_bearer_for",
        lambda url, prog, timeout=10.0: _canary_common.require_canary_bearer(prog),
    )


def test_missing_token_exits_before_network(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)
    monkeypatch.setattr(
        mcp_probe.urllib.request,
        "urlopen",
        lambda *a, **k: calls.append((a, k)),
    )
    monkeypatch.setattr(sys, "argv", ["mcp_probe", "status"])
    with pytest.raises(SystemExit) as exc:
        mcp_probe.main()
    assert exc.value.code == 2
    assert not calls
    assert "TINYASSETS_WIKI_CANARY_TOKEN" in capsys.readouterr().err

# ---------------------------------------------------------------------------
# Helpers to build fake urllib responses
# ---------------------------------------------------------------------------

def _fake_resp(body: str, sid: str | None = None) -> MagicMock:
    """Return a context-manager mock that mimics urllib.request.urlopen."""
    resp = MagicMock()
    resp.read.return_value = body.encode()
    resp.headers = MagicMock()
    resp.headers.get = lambda key, default=None: (
        sid if key == "mcp-session-id" else default
    )
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=resp)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _sse(payload: dict) -> str:
    return f"event: message\ndata: {json.dumps(payload)}\n\n"


# Canned MCP responses
_INIT_RESP = _sse({
    "jsonrpc": "2.0", "id": 1,
    "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
})

_NOTIF_RESP = ""  # notifications/initialized — server typically sends no data

_TOOLS_LIST_RESP = _sse({
    "jsonrpc": "2.0", "id": 2,
    "result": {
        "tools": [
            {"name": "get_status", "description": "Return daemon status"},
            {"name": "universe", "description": "Universe tool\nline2"},
        ]
    },
})

_TOOL_CALL_RESP = _sse({
    "jsonrpc": "2.0", "id": 3,
    "result": {
        "content": [{"type": "text", "text": "hello from tool"}],
        "isError": False,
    },
})

_TOOL_CALL_ERROR_RESP = _sse({
    "jsonrpc": "2.0", "id": 3,
    "result": {
        "content": [{"type": "text", "text": "tool error"}],
        "isError": True,
    },
})


# ---------------------------------------------------------------------------
# (a) SSE parse
# ---------------------------------------------------------------------------

class TestSSEParse:
    def test_single_event_parsed(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"x": 1}}
        body = _sse(payload)
        result, _ = mcp_probe._mcp_call.__wrapped__(
            "http://fake", None, {}
        ) if hasattr(mcp_probe._mcp_call, "__wrapped__") else (None, None)
        # Test via the module-level function with urlopen mocked.
        with patch("mcp_probe.urllib.request.urlopen", return_value=_fake_resp(body, "s1")):
            result, sid = mcp_probe._mcp_call("http://fake", None, payload)
        assert result == payload
        assert sid == "s1"

    def test_multi_event_body_last_data_wins(self):
        first = {"jsonrpc": "2.0", "id": 1, "result": {"v": 1}}
        second = {"jsonrpc": "2.0", "id": 2, "result": {"v": 2}}
        body = _sse(first) + _sse(second)
        with patch("mcp_probe.urllib.request.urlopen", return_value=_fake_resp(body)):
            result, _ = mcp_probe._mcp_call("http://fake", None, {})
        assert result == second

    def test_no_data_line_returns_none(self):
        body = "event: message\n\n"
        with patch("mcp_probe.urllib.request.urlopen", return_value=_fake_resp(body)):
            result, _ = mcp_probe._mcp_call("http://fake", None, {})
        assert result is None

    def test_malformed_json_in_data_skipped(self):
        body = "data: {not-json}\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\n"
        with patch("mcp_probe.urllib.request.urlopen", return_value=_fake_resp(body)):
            result, _ = mcp_probe._mcp_call("http://fake", None, {})
        assert result == {"jsonrpc": "2.0", "id": 1, "result": {}}


# ---------------------------------------------------------------------------
# (b) Session-id capture and reuse
# ---------------------------------------------------------------------------

class TestSessionId:
    def _side_effects(self, responses):
        """Return a side_effect list for urlopen."""
        return [_fake_resp(body, sid) for body, sid in responses]

    def test_session_id_captured_from_initialize(self):
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=self._side_effects([(_INIT_RESP, "sess-abc"), (_NOTIF_RESP, None)]),
        ):
            # Simulate the initialize + notifications/initialized calls.
            _, sid = mcp_probe._mcp_call("http://fake", None, {"method": "initialize"})
            assert sid == "sess-abc"

    def test_session_id_reused_in_header(self):
        """urlopen must receive mcp-session-id header on second call."""
        calls = []

        def capturing_urlopen(req, timeout=None):
            calls.append(req.headers.get("Mcp-session-id"))
            return _fake_resp(_INIT_RESP, "sess-xyz")

        with patch("mcp_probe.urllib.request.urlopen", side_effect=capturing_urlopen):
            mcp_probe._mcp_call("http://fake", "sess-xyz", {"method": "tools/list"})

        assert calls[0] == "sess-xyz"

    def test_the_bearer_is_sent_when_given_and_omitted_when_not(self):
        """The transport sends exactly what it is handed. An omitted bearer
        means "send none" -- it must NOT reach for the environment, or the
        deliberately anonymous probes authenticate themselves and the
        assertion they exist to make becomes unfalsifiable (Codex round 2)."""
        calls = []

        def capturing_urlopen(req, timeout=None):
            calls.append(req.headers.get("Authorization"))
            return _fake_resp(_INIT_RESP, "sess-xyz")

        with patch("mcp_probe.urllib.request.urlopen", side_effect=capturing_urlopen):
            mcp_probe._mcp_call(
                "http://fake", None, {"method": "initialize"},
                bearer_token="t" * 40,
            )
            mcp_probe._mcp_call("http://fake", None, {"method": "initialize"})

        assert calls == ["Bearer " + "t" * 40, None]


# ---------------------------------------------------------------------------
# (c) --list output
# ---------------------------------------------------------------------------

class TestListOutput:
    def _urlopen_sequence(self):
        return iter([
            _fake_resp(_INIT_RESP, "s1"),
            _fake_resp(_NOTIF_RESP, None),
            _fake_resp(_TOOLS_LIST_RESP, None),
        ])

    def test_list_prints_tool_names(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["mcp_probe", "--url", "http://fake", "--list"])
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=self._urlopen_sequence(),
        ):
            rc = mcp_probe.main()
        out = capsys.readouterr().out
        assert "get_status" in out
        assert "universe" in out
        assert rc == 0

    def test_list_truncates_description_to_first_line(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["mcp_probe", "--url", "http://fake", "--list"])
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=self._urlopen_sequence(),
        ):
            mcp_probe.main()
        out = capsys.readouterr().out
        assert "line2" not in out


# ---------------------------------------------------------------------------
# (d) --tool with --args
# ---------------------------------------------------------------------------

class TestToolArgsParsing:
    def test_json_object_args_still_parse(self):
        assert mcp_probe._parse_tool_args('{"action":"list","limit":5}') == {
            "action": "list",
            "limit": 5,
        }

    def test_powershell_stripped_simple_object_parses(self):
        assert mcp_probe._parse_tool_args("{action:search,query:research-paper,limit:5}") == {
            "action": "search",
            "query": "research-paper",
            "limit": 5,
        }

    def test_args_must_be_object(self):
        with pytest.raises(ValueError):
            mcp_probe._parse_tool_args('["not", "an", "object"]')


class TestToolCall:
    def _urlopen_sequence(self, tool_resp=None):
        return iter([
            _fake_resp(_INIT_RESP, "s1"),
            _fake_resp(_NOTIF_RESP, None),
            _fake_resp(tool_resp or _TOOL_CALL_RESP, None),
        ])

    def test_tool_call_prints_text_content(self, capsys, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["mcp_probe", "--url", "http://fake", "--tool", "get_status", "--args", "{}"],
        )
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=self._urlopen_sequence(),
        ):
            rc = mcp_probe.main()
        out = capsys.readouterr().out
        assert "hello from tool" in out
        assert rc == 0

    def test_tool_call_with_args(self, capsys, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["mcp_probe", "--url", "http://fake", "--tool", "universe",
             "--args", '{"action":"list"}'],
        )
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=self._urlopen_sequence(),
        ):
            rc = mcp_probe.main()
        assert rc == 0

    def test_isError_result_returns_exit_1(self, capsys, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["mcp_probe", "--url", "http://fake", "--tool", "get_status"],
        )
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=self._urlopen_sequence(_TOOL_CALL_ERROR_RESP),
        ):
            rc = mcp_probe.main()
        assert rc == 1


# ---------------------------------------------------------------------------
# (e) --raw output
# ---------------------------------------------------------------------------

class TestRawOutput:
    def test_raw_prints_full_json(self, capsys, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["mcp_probe", "--url", "http://fake", "--tool", "get_status", "--raw"],
        )
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=iter([
                _fake_resp(_INIT_RESP, "s1"),
                _fake_resp(_NOTIF_RESP, None),
                _fake_resp(_TOOL_CALL_RESP, None),
            ]),
        ):
            rc = mcp_probe.main()
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert "result" in parsed
        assert rc == 0


# ---------------------------------------------------------------------------
# (f) Error paths
# ---------------------------------------------------------------------------

class TestErrorPaths:
    def test_urllib_error_on_initialize_exits_nonzero(self, capsys, monkeypatch):
        import urllib.error

        monkeypatch.setattr(
            sys, "argv",
            ["mcp_probe", "--url", "http://fake", "--tool", "get_status"],
        )
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises((SystemExit, Exception)):
                mcp_probe.main()

    def test_initialize_missing_result_returns_1(self, capsys, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["mcp_probe", "--url", "http://fake", "--tool", "get_status"],
        )
        bad_init = _sse({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600}})
        with patch(
            "mcp_probe.urllib.request.urlopen",
            return_value=_fake_resp(bad_init, None),
        ):
            rc = mcp_probe.main()
        assert rc == 1


# ---------------------------------------------------------------------------
# (h) --tool not supplied + --list not set → exit 2
# ---------------------------------------------------------------------------

class TestMissingToolArg:
    def test_no_tool_no_list_returns_2(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["mcp_probe", "--url", "http://fake"])
        with patch(
            "mcp_probe.urllib.request.urlopen",
            side_effect=iter([
                _fake_resp(_INIT_RESP, "s1"),
                _fake_resp(_NOTIF_RESP, None),
            ]),
        ):
            rc = mcp_probe.main()
        assert rc == 2


# ---------------------------------------------------------------------------
# Subcommand tests
# ---------------------------------------------------------------------------

def _seq(*bodies_sids):
    """Build a urlopen side_effect sequence from (body, sid) pairs."""
    return iter([_fake_resp(body, sid) for body, sid in bodies_sids])


_STATUS_RESP = _sse({
    "jsonrpc": "2.0", "id": 3,
    "result": {
        "content": [{"type": "text", "text": '{"phase":"running","daemon_running":true}'}],
        "isError": False,
    },
})

_UNIVERSES_RESP = _sse({
    "jsonrpc": "2.0", "id": 3,
    "result": {
        "content": [{"type": "text", "text": '{"universes":[{"id":"concordance"}]}'}],
        "isError": False,
    },
})

_UNIVERSE_RESP = _sse({
    "jsonrpc": "2.0", "id": 3,
    "result": {
        "content": [{"type": "text", "text": '{"id":"concordance","branch_count":2}'}],
        "isError": False,
    },
})

_WIKI_RESP = _sse({
    "jsonrpc": "2.0", "id": 3,
    "result": {
        "content": [{"type": "text", "text": '{"promoted":[],"drafts":[]}'}],
        "isError": False,
    },
})

_LATENCY_STATUS_RESP = _sse({
    "jsonrpc": "2.0", "id": 3,
    "result": {
        "content": [{"type": "text", "text": '{"phase":"running"}'}],
        "isError": False,
    },
})


def _pin_clock(monkeypatch, times):
    """Replace the clock `_cmd_latency` reads, and nothing else.

    NOT `monkeypatch.setattr(mcp_probe.time, "monotonic", ...)`, which is what
    these tests used to do. `mcp_probe` does `import time`, so `mcp_probe.time`
    IS the `time` module: that patched `time.monotonic` process-wide, against a
    sequence sized for this one function's two calls. Any other code in the
    process consulting the clock inside the patch window -- a lingering worker
    thread, a `ThreadPoolExecutor` queue wait -- stole a value and the test died
    with `StopIteration`. It was a recurring CI flake, and it got likelier as
    the repo added background work.

    An exactly-sized iterator is the right fake NOW that nothing else reads the
    seam: if `_cmd_latency` ever changes how many times it reads the clock, this
    fails loudly instead of quietly measuring the wrong interval.

    The general rule, which is the transferable part: a replacement installed on
    a SHARED module must be stateless, because you do not control who else
    calls it. `lambda: cell[0]` is fine and is what most of this suite already
    does. Anything that consumes a sequence, counts calls, appends, or raises
    needs a seam only the code under test reads -- either a module attribute
    like this one, or `setattr(module, "time", SimpleNamespace(...))`.
    """
    monkeypatch.setattr(mcp_probe, "_clock", lambda: next(times))


class TestSubcommands:
    def _run(self, monkeypatch, argv, urlopen_seq):
        monkeypatch.setattr(sys, "argv", ["tinyassets-probe"] + argv)
        with patch("mcp_probe.urllib.request.urlopen", side_effect=urlopen_seq):
            return mcp_probe.main()

    def test_status_subcommand_calls_get_status(self, capsys, monkeypatch):
        seq = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_STATUS_RESP, None),
        )
        rc = self._run(monkeypatch, ["--url", "http://fake", "status"], seq)
        out = capsys.readouterr().out
        assert rc == 0
        assert "running" in out

    def test_universes_subcommand(self, capsys, monkeypatch):
        seq = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_UNIVERSES_RESP, None),
        )
        rc = self._run(monkeypatch, ["--url", "http://fake", "universes"], seq)
        out = capsys.readouterr().out
        assert rc == 0
        assert "concordance" in out

    def test_universe_subcommand_passes_id(self, capsys, monkeypatch):
        captured_payload = {}

        def capturing_urlopen(req, timeout=None):
            try:
                body = req.data.decode()
                payload = json.loads(body)
                if payload.get("method") == "tools/call":
                    captured_payload.update(payload)
            except Exception:
                pass
            return _fake_resp(_UNIVERSE_RESP, "s1")

        monkeypatch.setattr(sys, "argv", [
            "tinyassets-probe", "--url", "http://fake", "universe", "concordance"
        ])
        with patch("mcp_probe.urllib.request.urlopen", side_effect=capturing_urlopen):
            rc = mcp_probe.main()
        assert rc == 0
        args = captured_payload.get("params", {}).get("arguments", {})
        assert args.get("universe_id") == "concordance"
        assert args.get("action") == "inspect"

    def test_wiki_subcommand(self, capsys, monkeypatch):
        seq = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_WIKI_RESP, None),
        )
        rc = self._run(monkeypatch, ["--url", "http://fake", "wiki"], seq)
        assert rc == 0

    def test_tools_subcommand(self, capsys, monkeypatch):
        seq = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_TOOLS_LIST_RESP, None),
        )
        rc = self._run(monkeypatch, ["--url", "http://fake", "tools"], seq)
        out = capsys.readouterr().out
        assert rc == 0
        assert "get_status" in out

    def test_latency_subcommand_reports_elapsed_ms(self, capsys, monkeypatch):
        seq = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_LATENCY_STATUS_RESP, None),
        )
        times = iter([10.0, 10.125])
        _pin_clock(monkeypatch, times)
        rc = self._run(monkeypatch, ["--url", "http://fake", "latency"], seq)
        out = capsys.readouterr().out
        assert rc == 0
        assert "latency_ms=125" in out
        assert "status=ok" in out
        assert "stage=get_status" in out

    def test_latency_raw_includes_response(self, capsys, monkeypatch):
        seq = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_LATENCY_STATUS_RESP, None),
        )
        times = iter([20.0, 20.05])
        _pin_clock(monkeypatch, times)
        rc = self._run(monkeypatch, ["--url", "http://fake", "--raw", "latency"], seq)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert rc == 0
        assert parsed["ok"] is True
        assert parsed["latency_ms"] == 50
        assert parsed["response"]["result"]["isError"] is False

    def test_status_raw_flag(self, capsys, monkeypatch):
        seq = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_STATUS_RESP, None),
        )
        rc = self._run(monkeypatch, ["--url", "http://fake", "--raw", "status"], seq)
        out = capsys.readouterr().out
        assert rc == 0
        parsed = json.loads(out)
        assert "result" in parsed


# ---------------------------------------------------------------------------
# Task #61 — --verbose flag
# ---------------------------------------------------------------------------

class TestVerboseFlag:
    """--verbose logs initialize + tool-call progress to stderr without
    breaking the normal stdout output or exit codes."""

    def _run(self, argv, side_effects):
        mcp_probe._VERBOSE = False
        with patch("mcp_probe.urllib.request.urlopen", side_effect=side_effects), \
             patch("sys.argv", ["mcp_probe"] + argv):
            return mcp_probe.main()

    def test_verbose_flag_accepted_without_error(self, capsys):
        effects = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_STATUS_RESP, None),
        )
        rc = self._run(["--url", "http://fake", "--verbose", "status"], effects)
        assert rc == 0

    def test_verbose_logs_to_stderr(self, capsys):
        effects = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_STATUS_RESP, None),
        )
        self._run(["--url", "http://fake/mcp", "--verbose", "status"], effects)
        err = capsys.readouterr().err
        assert "initialize" in err.lower() or "probe" in err.lower()

    def test_verbose_does_not_pollute_stdout(self, capsys):
        effects = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_STATUS_RESP, None),
        )
        self._run(["--url", "http://fake", "--verbose", "status"], effects)
        out = capsys.readouterr().out
        # stderr content must NOT appear in stdout.
        assert "[probe]" not in out

    def test_no_verbose_produces_no_stderr(self, capsys):
        effects = _seq(
            (_INIT_RESP, "s1"),
            (_NOTIF_RESP, None),
            (_STATUS_RESP, None),
        )
        self._run(["--url", "http://fake", "status"], effects)
        err = capsys.readouterr().err
        assert err == ""

    def test_verbose_flag_in_parser(self):
        p = mcp_probe._build_parser()
        args = p.parse_args(["--verbose", "status"])
        assert args.verbose is True

    def test_no_verbose_default_is_false(self):
        p = mcp_probe._build_parser()
        args = p.parse_args(["status"])
        assert args.verbose is False

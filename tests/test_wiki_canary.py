"""Tests for scripts/wiki_canary.py — wiki write-roundtrip canary.

All tests use a scripted ``post_fn`` so no network I/O occurs.
The ScriptedPost helper is patterned after test_mcp_tool_canary.py.
"""

from __future__ import annotations

import json
import sys
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import wiki_canary as wc  # noqa: E402
from mcp_tool_canary import ToolCanaryError  # noqa: E402


@pytest.fixture(autouse=True)
def _canary_boundary(monkeypatch):
    import _canary_common

    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "t" * 40)
    # This daemon keeps the no-anonymous contract. Stated here rather than
    # discovered over the network, and it still honours a deleted token, so
    # the missing-token tests below exercise the real refusal.
    monkeypatch.setattr(
        wc, "canary_bearer_for",
        lambda url, prog, timeout=10.0: _canary_common.require_canary_bearer(prog),
    )
    monkeypatch.setattr(
        wc,
        "_status_post",
        lambda url, payload, timeout, accepted_http_statuses=frozenset(): (
            401,
            {"www-authenticate": "Bearer realm=\"tinyassets\""},
            (
                b'{"error":"authentication_required",'
                b'"error_description":"Sign in to TinyAssets to continue."}'
            ),
        ),
    )

# ---- scripted post helper --------------------------------------------------


class ScriptedPost:
    """Feeds pre-scripted (response, sid) tuples back one call at a time."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def __call__(
        self,
        url,
        sid,
        payload,
        timeout,
        *,
        step_code,
        bearer_token=None,
    ):
        self.calls.append({
            "url": url, "sid": sid,
            "method": payload.get("method"),
            "payload": payload,
            "step_code": step_code,
            "bearer_token": bearer_token,
        })
        if not self._responses:
            raise AssertionError(
                f"ScriptedPost ran out of responses at call "
                f"{len(self.calls)} (method={payload.get('method')!r})"
            )
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# ---- fixture helpers -------------------------------------------------------


def _init_resp(sid: str = "sess-wiki") -> tuple[dict, str]:
    return (
        {"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "tinyassets", "version": "1.0"},
            "capabilities": {},
        }},
        sid,
    )


def _notif_resp(sid: str = "sess-wiki") -> tuple[None, str]:
    return (None, sid)


def _wiki_write_rejected_resp(
    sid: str = "sess-wiki",
    raw_text: str | None = None,
    structured_content: dict | None = None,
) -> tuple[dict, str]:
    # Post-#1441 happy path: anonymous write_page returns the rejection
    # envelope (not a tool error).
    body = json.dumps({
        "status": "rejected",
        "error": "write_page: Anonymous writes are disabled on this server.",
        "auth_required": True,
        "tool": "write_page",
    })
    result = {
        "content": [{
            "type": "text",
            "text": raw_text if raw_text is not None else body,
        }],
        "isError": False,
    }
    if structured_content is not None:
        result["structuredContent"] = structured_content
    return (
        {"jsonrpc": "2.0", "id": 2, "result": result},
        sid,
    )


def _write_oauth_challenge_error(
    *,
    code: int = 401,
    challenge: str | None = "Bearer realm=\"tinyassets\"",
) -> ToolCanaryError:
    headers = Message()
    if challenge is not None:
        headers["WWW-Authenticate"] = challenge
    cause = HTTPError(
        "https://fake/mcp",
        code,
        "Unauthorized" if code == 401 else "Service Unavailable",
        headers,
        None,
    )
    error = ToolCanaryError(6, f"HTTP {code} on tools/call: {cause.reason}")
    error.__cause__ = cause
    return error


def _tool_error_with_cause(code: int, cause: Exception) -> ToolCanaryError:
    error = ToolCanaryError(6, f"HTTP {code} on tools/call")
    error.__cause__ = cause
    return error


def _wiki_write_accepted_resp(
    sid: str = "sess-wiki",
    raw_text: str | None = None,
) -> tuple[dict, str]:
    # Pre-#1441 shape: anonymous write persisting. Now a gate REGRESSION.
    body = raw_text or json.dumps({
        "status": "drafted",
        "path": f"drafts/{wc._CANARY_CATEGORY}/{wc._CANARY_FILENAME}.md",
    })
    return (
        {"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": body}],
            "isError": False,
        }},
        sid,
    )


def _wiki_read_ok_resp(
    sid: str = "sess-wiki",
    raw_text: str | None = None,
    structured_content: dict | None = None,
) -> tuple[dict, str]:
    # Read response body must contain the canary content text.
    body = json.dumps({
        "path": f"drafts/{wc._CANARY_CATEGORY}/{wc._CANARY_FILENAME}.md",
        "is_draft": True,
        "content": f"[DRAFT] {wc._CANARY_CONTENT}",
        "truncated": False,
    })
    result = {
        "content": [{
            "type": "text",
            "text": raw_text if raw_text is not None else body,
        }],
        "isError": False,
    }
    if structured_content is not None:
        result["structuredContent"] = structured_content
    return (
        {"jsonrpc": "2.0", "id": 3, "result": result},
        sid,
    )


def _happy_scripted() -> ScriptedPost:
    return ScriptedPost([
        _init_resp(),
        _notif_resp(),
        _wiki_write_accepted_resp(),
        _wiki_read_ok_resp(),
    ])


def _credentialed_happy_scripted() -> ScriptedPost:
    return ScriptedPost([
        _init_resp(),
        _notif_resp(),
        _wiki_write_accepted_resp(),
        _wiki_read_ok_resp(),
    ])


# ---- happy path ------------------------------------------------------------


def test_happy_path_run_canary_no_raise():
    wc.run_canary("https://fake/mcp", 5.0, post_fn=_happy_scripted())


def test_credentialed_canary_writes_then_reads_reserved_page():
    token = "service-token-" + "x" * 32
    scripted = _credentialed_happy_scripted()

    wc.run_canary(
        "https://fake/mcp",
        5.0,
        post_fn=scripted,
        bearer_token=token,
    )

    assert len(scripted.calls) == 4
    assert all(call["bearer_token"] == token for call in scripted.calls)
    assert (
        scripted.calls[2]["payload"]["params"]["arguments"]
        == {
            "filename": "uptime-probe",
            "category": "notes",
            "content": wc._CANARY_CONTENT,
            "dry_run": False,
        }
    )
    assert (
        scripted.calls[3]["payload"]["params"]["arguments"]
        == {"page": "uptime-probe"}
    )


def test_credentialed_canary_rejects_non_reserved_write_response_path():
    token = "service-token-" + "x" * 32
    wrong_path = _wiki_write_accepted_resp(
        raw_text=json.dumps({
            "status": "updated",
            "path": "drafts/notes/uptime-probe-neighbor.md",
        }),
    )
    scripted = ScriptedPost([_init_resp(), _notif_resp(), wrong_path])

    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary(
            "https://fake/mcp",
            5.0,
            post_fn=scripted,
            bearer_token=token,
        )

    assert ei.value.code == 6
    assert "reserved path" in ei.value.msg


def test_credentialed_canary_rejects_non_reserved_read_response_path():
    wrong_read = _wiki_read_ok_resp(
        raw_text=json.dumps({
            "path": "pages/notes/uptime-probe.md",
            "content": wc._CANARY_CONTENT,
        }),
    )
    scripted = ScriptedPost([
        _init_resp(),
        _notif_resp(),
        _wiki_write_accepted_resp(),
        wrong_read,
    ])

    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary(
            "https://fake/mcp",
            5.0,
            post_fn=scripted,
            bearer_token="service-token-" + "x" * 32,
        )

    assert ei.value.code == 7
    assert "reserved path" in ei.value.msg


def test_run_probe_uses_environment_token_when_present(monkeypatch):
    token = "service-token-" + "x" * 32
    scripted = _credentialed_happy_scripted()
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", token)
    monkeypatch.setattr(wc, "_fresh_canary_content", lambda: wc._CANARY_CONTENT)

    with patch("wiki_canary._append_log"):
        rc = wc.run_probe("https://fake/mcp", 5.0, post_fn=scripted)

    assert rc == 0
    assert scripted.calls[2]["bearer_token"] == token


def test_run_probe_with_no_bearer_probes_the_pre_cutover_way(monkeypatch):
    """The token requirement lives in ``main()``, where the daemon kind is
    known: a pre-cutover daemon refuses this bearer, so demanding it there
    would make every probe red for the whole cutover window and after any
    rollback (Codex review, 2026-09-02, finding E). ``run_probe`` sends what
    it is handed."""
    scripted = _happy_scripted()
    monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)

    wc.run_probe("https://fake/mcp", 5.0, post_fn=scripted, bearer_token=None)

    assert scripted.calls, "the anonymous path still probes"
    assert all(
        call.get("bearer_token") in (None, "")
        for call in scripted.calls
        if isinstance(call, dict)
    )


def test_credentialed_run_probe_writes_fresh_content_each_time(monkeypatch):
    class EchoRoundtripPost:
        def __init__(self):
            self.calls = 0
            self.written_content = ""

        def __call__(
            self,
            url,
            sid,
            payload,
            timeout,
            *,
            step_code,
            bearer_token=None,
        ):
            self.calls += 1
            if self.calls == 1:
                return _init_resp()
            if self.calls == 2:
                return _notif_resp()
            if self.calls == 3:
                self.written_content = payload["params"]["arguments"]["content"]
                return _wiki_write_accepted_resp()
            read_body = json.dumps({
                "path": wc._CANARY_RELATIVE_PATH,
                "is_draft": True,
                "content": self.written_content,
            })
            return _wiki_read_ok_resp(raw_text=read_body)

    monkeypatch.setenv(
        "TINYASSETS_WIKI_CANARY_TOKEN",
        "service-token-" + "x" * 32,
    )
    first = EchoRoundtripPost()
    second = EchoRoundtripPost()

    with patch("wiki_canary._append_log"):
        assert wc.run_probe("https://fake/mcp", 5.0, post_fn=first) == 0
        assert wc.run_probe("https://fake/mcp", 5.0, post_fn=second) == 0

    assert first.written_content.startswith(wc._CANARY_CONTENT)
    assert second.written_content.startswith(wc._CANARY_CONTENT)
    assert first.written_content != second.written_content


def test_exit_6_when_old_json_rejection_envelope_is_dispatched():
    scripted = ScriptedPost([
        _init_resp(),
        _notif_resp(),
        _wiki_write_rejected_resp(
            raw_text="Tool result available in structuredContent.",
            structured_content={
                "status": "rejected",
                "error": "write_page: Anonymous writes are disabled.",
                "auth_required": True,
                "tool": "write_page",
            },
        ),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6
    assert "reserved path" in ei.value.msg
    assert len(scripted.calls) == 3


def test_gate_probe_uses_canonical_write_page_and_read_page():
    scripted = _happy_scripted()
    wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert scripted.calls[2]["payload"]["params"]["name"] == "write_page"
    assert scripted.calls[3]["payload"]["params"]["name"] == "read_page"
    # Full-page write shape: must hit the gate, never the dry-run patch
    # preview passthrough (no old_text/new_text, no kind, dry_run False).
    write_args = scripted.calls[2]["payload"]["params"]["arguments"]
    assert write_args["dry_run"] is False
    assert "old_text" not in write_args and "kind" not in write_args


def test_happy_path_run_probe_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "_fresh_canary_content", lambda: wc._CANARY_CONTENT)
    with patch("wiki_canary._append_log"):
        rc = wc.run_probe("https://fake/mcp", 5.0, post_fn=_happy_scripted())
    assert rc == 0


def test_happy_path_log_line_contains_green(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "_fresh_canary_content", lambda: wc._CANARY_CONTENT)
    logged: list[str] = []
    with patch("wiki_canary._append_log", side_effect=logged.append):
        wc.run_probe("https://fake/mcp", 5.0, post_fn=_happy_scripted())
    assert logged, "Expected at least one log line"
    assert "GREEN" in logged[0]
    assert "surface=wiki_gate" in logged[0]


def test_happy_path_log_line_not_red(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "_fresh_canary_content", lambda: wc._CANARY_CONTENT)
    logged: list[str] = []
    with patch("wiki_canary._append_log", side_effect=logged.append):
        wc.run_probe("https://fake/mcp", 5.0, post_fn=_happy_scripted())
    assert all("RED" not in line for line in logged)


# ---- handshake failures (exit 2) ------------------------------------------


def test_exit_2_on_initialize_network_error():
    scripted = ScriptedPost([ToolCanaryError(2, "unreachable")])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 2


def test_exit_2_on_initialize_missing_result():
    scripted = ScriptedPost([({"jsonrpc": "2.0", "id": 1}, "sess")])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 2


def test_exit_2_on_no_session_id():
    scripted = ScriptedPost([
        ({"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "x"},
        }}, None),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 2


# ---- write-gate probe failures (exit 6) ------------------------------------


def test_exit_6_on_wiki_write_non_401_http_error():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        _write_oauth_challenge_error(code=503),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6
    assert "HTTP 503" in ei.value.msg


def test_exit_6_on_403_even_with_oauth_challenge():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        _write_oauth_challenge_error(code=403),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6


def test_exit_6_when_401_message_is_not_chained_http_error():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        ToolCanaryError(6, "HTTP 401 on tools/call: Unauthorized"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6


def test_exit_6_when_401_has_inaccessible_headers():
    class BrokenHeaders:
        def get(self, _name):
            raise RuntimeError("header access failed")

    cause = HTTPError("https://fake/mcp", 401, "Unauthorized", BrokenHeaders(), None)
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(), _tool_error_with_cause(401, cause),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6


def test_exit_6_when_401_challenge_is_not_a_string():
    class NonStringHeaders:
        def get(self, _name):
            return 401

    cause = HTTPError("https://fake/mcp", 401, "Unauthorized", NonStringHeaders(), None)
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(), _tool_error_with_cause(401, cause),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6


def test_exit_6_on_write_url_error():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        _tool_error_with_cause(6, URLError("offline")),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6


def test_initialize_401_stays_exit_2():
    cause = HTTPError("https://fake/mcp", 401, "Unauthorized", Message(), None)
    initialize_error = ToolCanaryError(2, "HTTP 401 on initialize: Unauthorized")
    initialize_error.__cause__ = cause
    scripted = ScriptedPost([initialize_error])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 2


def test_exit_6_on_wiki_write_iserror():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        ({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": "disk full"}],
            "isError": True,
        }}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6
    assert "isError=true" in ei.value.msg


def test_exit_6_on_wiki_write_unexpected_status():
    bad_body = json.dumps({"status": "conflict", "filename": wc._CANARY_FILENAME})
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        ({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": bad_body}],
            "isError": False,
        }}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6
    assert "reserved path" in ei.value.msg


def test_exit_6_on_wiki_write_no_result():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        ({"jsonrpc": "2.0", "id": 2}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6


def test_exit_6_on_wiki_write_no_text_content():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        ({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [], "isError": False,
        }}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6


def test_exit_6_on_wiki_write_non_json_text():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        ({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": "not json"}],
            "isError": False,
        }}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 6


# ---- wiki read failures (exit 7) ------------------------------------------


def test_exit_7_on_wiki_read_network_error():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(), _wiki_write_accepted_resp(),
        ToolCanaryError(7, "HTTP 503 on wiki read"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 7


def test_exit_7_on_wiki_read_iserror():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(), _wiki_write_accepted_resp(),
        ({"jsonrpc": "2.0", "id": 3, "result": {
            "content": [{"type": "text", "text": "not found"}],
            "isError": True,
        }}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 7
    assert "isError" in ei.value.msg


def test_exit_7_on_wiki_read_roundtrip_mismatch():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(), _wiki_write_accepted_resp(),
        ({"jsonrpc": "2.0", "id": 3, "result": {
            "content": [{"type": "text", "text": "wrong content entirely"}],
            "isError": False,
        }}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 7
    assert "mismatch" in ei.value.msg


def test_exit_7_on_wiki_read_no_result():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(), _wiki_write_accepted_resp(),
        ({"jsonrpc": "2.0", "id": 3}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 7


def test_exit_7_on_wiki_read_no_text_content():
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(), _wiki_write_accepted_resp(),
        ({"jsonrpc": "2.0", "id": 3, "result": {
            "content": [], "isError": False,
        }}, "sess-wiki"),
    ])
    with pytest.raises(ToolCanaryError) as ei:
        wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert ei.value.code == 7


# ---- run_probe log line surface tag ----------------------------------------


def test_red_log_line_contains_surface_wiki_gate_on_exit_6():
    logged: list[str] = []
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(),
        ToolCanaryError(6, "disk full"),
    ])
    with patch("wiki_canary._append_log", side_effect=logged.append):
        rc = wc.run_probe("https://fake/mcp", 5.0, post_fn=scripted)
    assert rc == 6
    assert logged
    assert "surface=wiki_gate" in logged[0]
    assert "RED" in logged[0]


def test_red_log_line_contains_surface_wiki_gate_on_exit_7():
    logged: list[str] = []
    scripted = ScriptedPost([
        _init_resp(), _notif_resp(), _wiki_write_accepted_resp(),
        ToolCanaryError(7, "roundtrip mismatch"),
    ])
    with patch("wiki_canary._append_log", side_effect=logged.append):
        rc = wc.run_probe("https://fake/mcp", 5.0, post_fn=scripted)
    assert rc == 7
    assert logged
    assert "surface=wiki_gate" in logged[0]


def test_exit_99_on_unexpected_exception():
    def _explode(url, sid, payload, timeout, *, step_code):
        raise RuntimeError("surprise")

    logged: list[str] = []
    with patch("wiki_canary._append_log", side_effect=logged.append):
        rc = wc.run_probe("https://fake/mcp", 5.0, post_fn=_explode)
    assert rc == 99
    assert logged
    assert "unexpected" in logged[0]


# ---- main() propagates exit codes ------------------------------------------


@pytest.mark.parametrize("responses, expected_rc", [
    # exit 2: handshake fails
    ([ToolCanaryError(2, "boom")], 2),
    # exit 6: wiki write fails
    ([_init_resp(), _notif_resp(), ToolCanaryError(6, "write fail")], 6),
    # exit 7: wiki read roundtrip mismatch
    ([
        _init_resp(), _notif_resp(), _wiki_write_accepted_resp(),
        ({"jsonrpc": "2.0", "id": 3, "result": {
            "content": [{"type": "text", "text": "wrong"}],
            "isError": False,
        }}, "sess-wiki"),
    ], 7),
    # exit 0: all pass
    ([_init_resp(), _notif_resp(), _wiki_write_accepted_resp(), _wiki_read_ok_resp()], 0),
])
def test_main_propagates_exit_codes(monkeypatch, responses, expected_rc):
    monkeypatch.setattr(wc, "_fresh_canary_content", lambda: wc._CANARY_CONTENT)
    scripted = ScriptedPost(responses)
    # Patch _post inside wiki_canary (the name it was imported under) so
    # run_probe → run_canary picks up the scripted responses without recursion.
    monkeypatch.setattr(wc, "_post", scripted)
    with patch("wiki_canary._append_log"):
        rc = wc.main(["--url", "https://fake/mcp"])
    assert rc == expected_rc


def test_anonymous_initialize_200_is_exit_6(monkeypatch):
    monkeypatch.setattr(
        wc,
        "_status_post",
        lambda *a, **k: (200, {}, b"{}"),
    )
    with pytest.raises(ToolCanaryError) as exc:
        wc.run_canary(
            "https://fake/mcp", 5.0, post_fn=_credentialed_happy_scripted(),
        )
    assert exc.value.code == 6
    assert "admitted an anonymous initialize" in exc.value.msg


def test_every_authenticated_post_carries_canary_bearer():
    scripted = _credentialed_happy_scripted()
    wc.run_canary("https://fake/mcp", 5.0, post_fn=scripted)
    assert all(call["bearer_token"] == "t" * 40 for call in scripted.calls)


def test_main_missing_token_exits_before_any_post(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)
    monkeypatch.setattr(wc, "_post", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(wc, "_status_post", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(SystemExit) as exc:
        wc.main([])
    assert exc.value.code == 2
    assert not calls
    assert "TINYASSETS_WIKI_CANARY_TOKEN" in capsys.readouterr().err

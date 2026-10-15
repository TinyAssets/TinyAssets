"""Tests for scripts/verify_llm_binding.py — mocked MCP responses."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from verify_llm_binding import VerifyError, check_llm_binding, main  # noqa: E402


@pytest.fixture(autouse=True)
def _canary_token(monkeypatch):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "t" * 40)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INIT_OK = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}
_NOTIF_NONE = None  # notifications return None body

_STATUS_BOUND = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {
        "content": [
            {
                "type": "text",
                "text": '{"llm_endpoint_bound": "anthropic", "phase": "idle"}',
            }
        ]
    },
}
_STATUS_BOUND_NESTED = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {
        "content": [
            {
                "type": "text",
                "text": (
                    '{"active_host": {"llm_endpoint_bound": "codex"}, '
                    '"phase": "idle"}'
                ),
            }
        ]
    },
}
_STATUS_BOUND_NESTED_WITH_STALE_TOP_LEVEL = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {
        "content": [
            {
                "type": "text",
                "text": (
                    '{"llm_endpoint_bound": "unset", '
                    '"active_host": {"llm_endpoint_bound": "codex"}, '
                    '"phase": "idle"}'
                ),
            }
        ]
    },
}
_STATUS_BOUND_STRUCTURED_CONTENT = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {
        "content": [
            {
                "type": "text",
                "text": (
                    "Tool result: caveats=2. "
                    "Full payload is in structuredContent."
                ),
            }
        ],
        "structuredContent": {
            "schema_version": 1,
            "active_host": {"llm_endpoint_bound": "codex"},
            "sandbox_status": {"box_available": True, "reason": None},
        },
    },
}
_STATUS_BOUND_SANDBOX_OK = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {
        "content": [
            {
                "type": "text",
                "text": (
                    '{"active_host": {"llm_endpoint_bound": "codex"}, '
                    '"sandbox_status": {"box_available": true, "reason": null}}'
                ),
            }
        ]
    },
}
_STATUS_BOUND_SANDBOX_MISSING = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {
        "content": [
            {
                "type": "text",
                "text": (
                    '{"active_host": {"llm_endpoint_bound": "codex"}, '
                    '"sandbox_status": {"box_available": false, '
                    '"reason": "no owner box"}}'
                ),
            }
        ]
    },
}
_STATUS_UNBOUND = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {
        "content": [
            {
                "type": "text",
                "text": '{"llm_endpoint_bound": "unset", "phase": "starved"}',
            }
        ]
    },
}
_ADD_CANON_OK = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {"content": [{"type": "text", "text": "canon entry added"}]},
}
_ADD_CANON_ERROR = {
    "jsonrpc": "2.0",
    "id": 10,
    "result": {
        "isError": True,
        "content": [{"type": "text", "text": "universe not initialized"}],
    },
}


def _make_post_fn(*responses: tuple[dict | None, str | None]):
    """Return a post_fn that yields successive (resp, sid) pairs."""
    it = iter(responses)

    calls = []

    def _post(url, sid, payload, timeout, *, bearer_token=None):
        calls.append({"payload": payload, "bearer_token": bearer_token})
        return next(it)

    _post.calls = calls
    return _post


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


def test_llm_bound_returns_status():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),       # initialize
        (_NOTIF_NONE, "sid1"),    # notifications/initialized
        (_STATUS_BOUND, "sid1"),  # get_status
        (_ADD_CANON_OK, "sid1"),  # add_canon
    )
    result = check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert result.get("llm_endpoint_bound") == "anthropic"


def test_llm_bound_accepts_current_nested_status_shape():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),              # initialize
        (_NOTIF_NONE, "sid1"),           # notifications/initialized
        (_STATUS_BOUND_NESTED, "sid1"),  # get_status
        (_ADD_CANON_OK, "sid1"),         # add_canon
    )
    result = check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert result["active_host"]["llm_endpoint_bound"] == "codex"


def test_llm_bound_prefers_nested_status_over_stale_top_level():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_BOUND_NESTED_WITH_STALE_TOP_LEVEL, "sid1"),
        (_ADD_CANON_OK, "sid1"),
    )
    result = check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert result["active_host"]["llm_endpoint_bound"] == "codex"


def test_llm_bound_accepts_structured_content_status_payload():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_BOUND_STRUCTURED_CONTENT, "sid1"),
        (_ADD_CANON_OK, "sid1"),
    )
    result = check_llm_binding(
        "http://fake/mcp", 10.0, require_sandbox=True, post_fn=post_fn
    )
    assert result["active_host"]["llm_endpoint_bound"] == "codex"
    assert result["sandbox_status"]["box_available"] is True


def test_llm_bound_add_canon_non_fatal():
    """add_canon failure must not raise — binding check still passes."""
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_BOUND, "sid1"),
        (_ADD_CANON_ERROR, "sid1"),
    )
    result = check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert result.get("llm_endpoint_bound") == "anthropic"


def test_require_sandbox_accepts_box_available():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_BOUND_SANDBOX_OK, "sid1"),
        (_ADD_CANON_OK, "sid1"),
    )
    result = check_llm_binding(
        "http://fake/mcp", 10.0, require_sandbox=True, post_fn=post_fn
    )
    assert result["sandbox_status"]["box_available"] is True


def test_require_sandbox_rejects_missing_bwrap():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_BOUND_SANDBOX_MISSING, "sid1"),
    )
    with pytest.raises(VerifyError) as exc_info:
        check_llm_binding(
            "http://fake/mcp", 10.0, require_sandbox=True, post_fn=post_fn
        )
    assert exc_info.value.code == 5
    assert "bwrap not found" in exc_info.value.msg


# ---------------------------------------------------------------------------
# Unbound
# ---------------------------------------------------------------------------


def test_llm_unbound_raises_code3():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_UNBOUND, "sid1"),
    )
    with pytest.raises(VerifyError) as exc_info:
        check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert exc_info.value.code == 3


def test_llm_empty_string_unbound():
    status_empty = {
        "jsonrpc": "2.0",
        "id": 10,
        "result": {
            "content": [
                {"type": "text", "text": '{"llm_endpoint_bound": ""}'}
            ]
        },
    }
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (status_empty, "sid1"),
    )
    with pytest.raises(VerifyError) as exc_info:
        check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert exc_info.value.code == 3


def test_llm_false_string_unbound():
    status_false = {
        "jsonrpc": "2.0",
        "id": 10,
        "result": {
            "content": [
                {"type": "text", "text": '{"llm_endpoint_bound": "false"}'}
            ]
        },
    }
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (status_false, "sid1"),
    )
    with pytest.raises(VerifyError) as exc_info:
        check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert exc_info.value.code == 3


# ---------------------------------------------------------------------------
# Protocol errors
# ---------------------------------------------------------------------------


def test_initialize_no_result_raises_code1():
    bad_init = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600}}
    post_fn = _make_post_fn(
        (bad_init, None),
    )
    with pytest.raises(VerifyError) as exc_info:
        check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert exc_info.value.code == 1


def test_initialize_none_response_raises_code1():
    post_fn = _make_post_fn(
        (None, None),
    )
    with pytest.raises(VerifyError) as exc_info:
        check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert exc_info.value.code == 1


def test_get_status_no_result_raises_code1():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (None, "sid1"),  # get_status returns None
    )
    with pytest.raises(VerifyError) as exc_info:
        check_llm_binding("http://fake/mcp", 10.0, post_fn=post_fn)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Network errors
# ---------------------------------------------------------------------------


def test_network_error_raises_code2():
    def _failing_post(url, sid, payload, timeout, *, bearer_token=None):
        raise VerifyError(2, "network error")

    with pytest.raises(VerifyError) as exc_info:
        check_llm_binding("http://fake/mcp", 10.0, post_fn=_failing_post)
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


def test_main_returns_0_on_bound():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_BOUND, "sid1"),
        (_ADD_CANON_OK, "sid1"),
    )
    with patch("verify_llm_binding._post", side_effect=post_fn):
        code = main(["--url", "http://fake/mcp", "--timeout", "5"])
    assert code == 0


def test_main_returns_3_on_unbound():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_UNBOUND, "sid1"),
    )
    with patch("verify_llm_binding._post", side_effect=post_fn):
        code = main(["--url", "http://fake/mcp", "--timeout", "5"])
    assert code == 3


def test_main_returns_2_on_network_error():
    def _failing(url, sid, payload, timeout, *, bearer_token=None):
        raise VerifyError(2, "network error")

    with patch("verify_llm_binding._post", side_effect=_failing):
        code = main(["--url", "http://fake/mcp", "--timeout", "5"])
    assert code == 2


def test_main_retries_transient_sandbox_failure():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_BOUND_SANDBOX_MISSING, "sid1"),
        (_INIT_OK, "sid2"),
        (_NOTIF_NONE, "sid2"),
        (_STATUS_BOUND_SANDBOX_OK, "sid2"),
        (_ADD_CANON_OK, "sid2"),
    )
    with patch("verify_llm_binding._post", side_effect=post_fn):
        code = main([
            "--url", "http://fake/mcp",
            "--timeout", "5",
            "--require-sandbox",
            "--retries", "2",
            "--retry-delay", "0",
        ])
    assert code == 0


def test_main_returns_5_when_required_sandbox_missing():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid1"),
        (_NOTIF_NONE, "sid1"),
        (_STATUS_BOUND_SANDBOX_MISSING, "sid1"),
    )
    with patch("verify_llm_binding._post", side_effect=post_fn):
        code = main([
            "--url", "http://fake/mcp",
            "--timeout", "5",
            "--require-sandbox",
        ])
    assert code == 5


def test_every_post_carries_canary_bearer():
    post_fn = _make_post_fn(
        (_INIT_OK, "sid"),
        (_NOTIF_NONE, "sid"),
        (_STATUS_BOUND, "sid"),
    )
    check_llm_binding("http://fake/mcp", 5.0, post_fn=post_fn)
    assert all(call["bearer_token"] == "t" * 40 for call in post_fn.calls)


def test_missing_token_exits_before_post(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)
    with patch("verify_llm_binding._post", side_effect=lambda *a, **k: calls.append(1)):
        with pytest.raises(SystemExit) as exc:
            main([])
    assert exc.value.code == 2
    assert not calls
    assert "TINYASSETS_WIKI_CANARY_TOKEN" in capsys.readouterr().err

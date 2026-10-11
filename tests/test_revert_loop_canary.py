"""Revert-loop canary tests (Lane 4a per
2026-04-23-revert-loop-canary-spec.md (archived in git)).

Covers the 6 test surfaces named in the spec Test Strategy §:
0/2/3 REVERTs (OK), 3/10min (WARN), 5/20min (CRITICAL), 3/20min (below
WARN strict rate but inside CRITICAL window — confirms math), empty
tail (OK), network-path failures.

Pure classify_loop tests use injected ``now`` and scripted tails; network
path tests use stubbed ``post_fn``. No live daemon.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import revert_loop_canary as rlc  # noqa: E402


@pytest.fixture(autouse=True)
def _canary_token(monkeypatch):
    import _canary_common

    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "t" * 40)
    # This daemon keeps the no-anonymous contract. Stated here rather than
    # discovered over the network: the probe asks GET <url>/pulse, and a test
    # that leaves that to a stub would consume a scripted response meant for
    # `initialize`.
    monkeypatch.setattr(
        rlc, "canary_bearer_for",
        lambda url, prog, timeout=10.0: _canary_common.require_canary_bearer(prog),
    )


def _now() -> _dt.datetime:
    return _dt.datetime(2026, 4, 23, 21, 0, 0, tzinfo=_dt.timezone.utc)


def _stamp(minutes_ago: float) -> str:
    ts = _now() - _dt.timedelta(minutes=minutes_ago)
    ts = ts.replace(microsecond=0)
    return ts.isoformat().replace("+00:00", "Z")


def _revert_line(minutes_ago: float, style: str = "standard") -> str:
    """Build a realistic REVERT activity-log entry."""
    if style == "draft_failed":
        return (
            f"{_stamp(minutes_ago)} [commit] Commit: reverting — "
            "draft provider failed"
        )
    if style == "score":
        return f"{_stamp(minutes_ago)} [commit] score 0.00 -- REVERT"
    return (
        f"{_stamp(minutes_ago)} [commit] Commit: universe=concordance "
        "score 0.00 -- REVERT"
    )


def _classify(
    tail: list[str],
    *,
    warn_n: int = 3, warn_t: int = 10,
    crit_n: int = 5, crit_t: int = 20,
) -> tuple[int, str]:
    return rlc.classify_loop(
        tail, now=_now(),
        warn_window_min=warn_t, warn_threshold=warn_n,
        critical_window_min=crit_t, critical_threshold=crit_n,
    )


# ════════════════════════════════════════════════════════════════════
# Spec §Test Strategy #1 — classify_loop unit tests
# ════════════════════════════════════════════════════════════════════


class TestClassifyLoopOkTier:
    def test_green_on_no_reverts(self):
        tail = [
            f"{_stamp(5)} [commit] Commit: score 0.95 -- KEEP",
            f"{_stamp(3)} [commit] Commit: score 0.88 -- MERGE",
        ]
        code, msg = _classify(tail)
        assert code == 0
        assert "0 REVERTs" in msg

    def test_green_on_2_reverts(self):
        tail = [_revert_line(8), _revert_line(3)]
        code, _ = _classify(tail)
        assert code == 0

    def test_green_on_mixed_success_and_fail(self):
        tail = [
            _revert_line(8),
            f"{_stamp(6)} [commit] Commit: score 0.95 -- KEEP",
            _revert_line(3),
        ]
        code, _ = _classify(tail)
        assert code == 0


class TestClassifyLoopWarnTier:
    def test_warn_on_3_reverts_in_10min(self):
        tail = [_revert_line(9), _revert_line(6), _revert_line(3)]
        code, msg = _classify(tail)
        assert code == 2
        assert "WARN revert-loop" in msg
        assert "3 REVERTs" in msg

    def test_warn_exact_boundary(self):
        tail = [_revert_line(10), _revert_line(5), _revert_line(1)]
        code, _ = _classify(tail)
        assert code == 2

    def test_warn_with_draft_failed_style_revert(self):
        tail = [
            _revert_line(9, "draft_failed"),
            _revert_line(6, "draft_failed"),
            _revert_line(3, "draft_failed"),
        ]
        code, _ = _classify(tail)
        assert code == 2


class TestClassifyLoopCriticalTier:
    def test_critical_on_5_reverts_in_20min(self):
        tail = [
            _revert_line(18), _revert_line(14), _revert_line(10),
            _revert_line(6), _revert_line(2),
        ]
        code, msg = _classify(tail)
        assert code == 3
        assert "CRITICAL revert-loop" in msg
        assert "5 REVERTs" in msg
        assert "auto-repair" in msg

    def test_critical_precedence_over_warn_when_both_fire(self):
        # 5 reverts in 20min AND 3+ within 10min → CRITICAL wins.
        tail = [
            _revert_line(18), _revert_line(14),
            _revert_line(9), _revert_line(6), _revert_line(2),
        ]
        code, _ = _classify(tail)
        assert code == 3

    def test_critical_with_more_than_threshold(self):
        tail = [_revert_line(20 - i) for i in range(20)]
        code, _ = _classify(tail)
        assert code == 3


class TestClassifyLoopMathBoundaries:
    """Spec surface #1 explicit: '3 in 20min (below WARN at strict rate
    but within critical window — confirms math)'."""

    def test_3_reverts_outside_10min_inside_20min_stays_green(self):
        # 3 REVERTs at 12, 14, 16 min ago: none in WARN window (10min),
        # all 3 in CRITICAL window (20min). Neither threshold fires:
        # WARN count=0, CRIT count=3 < crit_threshold=5.
        tail = [_revert_line(16), _revert_line(14), _revert_line(12)]
        code, _ = _classify(tail)
        assert code == 0

    def test_untimestamped_reverts_ignored(self):
        tail = [
            "some log line without timestamp Commit: score 0.00 -- REVERT",
            "another bare REVERT line",
        ]
        code, _ = _classify(tail)
        assert code == 0


class TestClassifyLoopEmptyEvidence:
    def test_empty_tail_is_green(self):
        code, msg = _classify([])
        assert code == 0
        assert "empty" in msg
        assert "0 REVERTs" in msg

    def test_non_string_entries_skipped(self):
        tail = [None, 42, {"dict": "not a string"}]  # type: ignore[list-item]
        code, _ = _classify(tail)  # type: ignore[arg-type]
        assert code == 0


class TestRevertPatternDiscipline:
    """Spec Q2: Draft:FAILED is explicitly EXCLUDED. Only terminal
    commit verdicts count."""

    def test_draft_failed_does_not_count(self):
        tail = [
            f"{_stamp(9)} [worker] Draft: FAILED — provider empty prose",
            f"{_stamp(6)} [worker] Draft: FAILED — provider empty prose",
            f"{_stamp(3)} [worker] Draft: FAILED — provider empty prose",
        ]
        code, _ = _classify(tail)
        assert code == 0

    def test_all_providers_exhausted_does_not_count(self):
        tail = [
            f"{_stamp(9)} [provider] All providers exhausted",
            f"{_stamp(6)} [provider] All providers exhausted",
            f"{_stamp(3)} [provider] All providers exhausted",
        ]
        code, _ = _classify(tail)
        assert code == 0

    def test_keep_and_merge_verdicts_stay_green(self):
        tail = [
            f"{_stamp(9)} [commit] Commit: score 0.92 -- KEEP",
            f"{_stamp(6)} [commit] Commit: score 0.88 -- MERGE",
            f"{_stamp(3)} [commit] Commit: score 0.95 -- KEEP",
        ]
        code, _ = _classify(tail)
        assert code == 0

    def test_mixed_revert_styles_all_count(self):
        tail = [
            _revert_line(9, "standard"),
            _revert_line(6, "draft_failed"),
            _revert_line(3, "score"),
        ]
        code, _ = _classify(tail)
        assert code == 2

    def test_case_insensitive_matching(self):
        tail = [
            f"{_stamp(9)} commit: SCORE 0.00 -- revert",
            f"{_stamp(6)} Commit: Reverting — draft provider failed",
            f"{_stamp(3)} COMMIT: score 0.00 -- REVERT",
        ]
        code, _ = _classify(tail)
        assert code == 2


class TestParseLineTimestamp:
    def test_bare_iso(self):
        ts = rlc._parse_line_timestamp("2026-04-23T21:00:00Z foo")
        assert ts is not None

    def test_bracketed_iso(self):
        ts = rlc._parse_line_timestamp("[2026-04-23T21:00:00Z] foo")
        assert ts is not None

    def test_no_timestamp_returns_none(self):
        assert rlc._parse_line_timestamp("foo bar baz") is None

    def test_malformed_timestamp_returns_none(self):
        assert rlc._parse_line_timestamp("2026-99-99T99:99:99Z") is None


# ════════════════════════════════════════════════════════════════════
# Network path via injected post_fn (spec surface #3)
# ════════════════════════════════════════════════════════════════════


def _make_init_response() -> dict:
    return {
        "jsonrpc": "2.0", "id": 1,
        "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
    }


def _make_tool_response(
    tail: list[str],
    *,
    evidence_caveats: dict | None = None,
    raw_text: str | None = None,
    structured_content: dict | None = None,
) -> dict:
    import json as _json
    payload = {
        "active_host": {"host_id": "test"},
        "evidence": {"activity_log_tail": tail},
    }
    if evidence_caveats is not None:
        payload["evidence_caveats"] = evidence_caveats
    result = {
        "content": [{
            "type": "text",
            "text": raw_text if raw_text is not None else _json.dumps(payload),
        }],
    }
    if structured_content is not None:
        result["structuredContent"] = structured_content
    return {
        "jsonrpc": "2.0", "id": 2,
        "result": result,
    }


class _StubPost:
    def __init__(self, steps: list[tuple[dict | None, str | None]]):
        self._steps = list(steps)
        self.calls = []

    def __call__(
        self, url, sid, payload, timeout, *, step_code, bearer_token=None,
    ):
        self.calls.append({"payload": payload, "bearer_token": bearer_token})
        if not self._steps:
            raise AssertionError("stub ran out of responses")
        return self._steps.pop(0)


class TestFetchStatusActivityTail:
    def test_happy_path(self):
        tail = [_revert_line(5)]
        stub = _StubPost([
            (_make_init_response(), "sid-abc"),
            (None, "sid-abc"),
            (_make_tool_response(tail), "sid-abc"),
        ])
        result = rlc.fetch_status_activity_tail(
            "http://fake/mcp", 10.0, post_fn=stub,
        )
        assert result == tail

    def test_happy_path_accepts_structured_content_when_text_is_preview(self):
        tail = [_revert_line(5)]
        stub = _StubPost([
            (_make_init_response(), "sid-abc"),
            (None, "sid-abc"),
            (
                _make_tool_response(
                    [],
                    raw_text="Tool result available in structuredContent.",
                    structured_content={
                        "active_host": {"host_id": "test"},
                        "evidence": {"activity_log_tail": tail},
                    },
                ),
                "sid-abc",
            ),
        ])
        result = rlc.fetch_status_activity_tail(
            "http://fake/mcp", 10.0, post_fn=stub,
        )
        assert result == tail

    def test_initialize_error_raises_step4(self):
        stub = _StubPost([
            ({"jsonrpc": "2.0", "id": 1, "error": {"message": "x"}}, "sid"),
        ])
        with pytest.raises(rlc.RevertLoopError) as exc_info:
            rlc.fetch_status_activity_tail(
                "http://fake/mcp", 10.0, post_fn=stub,
            )
        assert exc_info.value.code == 4

    def test_initialize_missing_sid_raises_step4(self):
        stub = _StubPost([(_make_init_response(), None)])
        with pytest.raises(rlc.RevertLoopError) as exc_info:
            rlc.fetch_status_activity_tail(
                "http://fake/mcp", 10.0, post_fn=stub,
            )
        assert exc_info.value.code == 4

    def test_tool_is_error_raises_step5(self):
        stub = _StubPost([
            (_make_init_response(), "sid"),
            (None, "sid"),
            (
                {
                    "jsonrpc": "2.0", "id": 2,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "boom"}],
                    },
                },
                "sid",
            ),
        ])
        with pytest.raises(rlc.RevertLoopError) as exc_info:
            rlc.fetch_status_activity_tail(
                "http://fake/mcp", 10.0, post_fn=stub,
            )
        # Spec-bumped from 3 → 5 to avoid CRITICAL-exit collision.
        assert exc_info.value.code == 5
        assert exc_info.value.observation == "red"
        assert exc_info.value.reason == "probe_failed"

    def test_payload_missing_evidence_raises_step5(self):
        stub = _StubPost([
            (_make_init_response(), "sid"),
            (None, "sid"),
            (
                {
                    "jsonrpc": "2.0", "id": 2,
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": (
                                '{"first_contact": {"event": "no_universe_yet"}, '
                                '"daemon": {"worker_liveness": {}}, "release_state": {}}'
                            ),
                        }],
                    },
                },
                "sid",
            ),
        ])
        with pytest.raises(rlc.RevertLoopError) as exc_info:
            rlc.fetch_status_activity_tail(
                "http://fake/mcp", 10.0, post_fn=stub,
        )
        assert exc_info.value.code == 5

        # A successful confined response without the private legacy signal is
        # unobserved coverage, not a measured outage or empty healthy tail.
        assert exc_info.value.observation == "unknown"
        assert exc_info.value.reason == "legacy_evidence_unavailable"
        assert stub.calls[-1]["payload"]["params"]["arguments"] == {}

    def test_transport_step5_is_not_downgraded_with_absent_evidence(self):
        def fail_tool_transport(url, sid, payload, timeout, **kwargs):
            if payload.get("method") == "initialize":
                return _make_init_response(), "sid"
            if payload.get("method") == "notifications/initialized":
                return None, "sid"
            assert kwargs["step_code"] == 5
            raise rlc.RevertLoopError(5, "HTTP 503 from tool endpoint")

        with pytest.raises(rlc.RevertLoopError) as exc_info:
            rlc.fetch_status_activity_tail(
                "http://fake/mcp", 10.0, post_fn=fail_tool_transport,
            )

        assert exc_info.value.code == 5
        assert exc_info.value.observation == "red"
        assert exc_info.value.reason == "probe_failed"

    @pytest.mark.parametrize("payload", [{}, {"daemon": {}}, {"error": "denied"}])
    def test_unrecognized_payload_remains_red(self, payload):
        stub = _StubPost([
            (_make_init_response(), "sid"),
            (None, "sid"),
            (_make_tool_response([], structured_content=payload), "sid"),
        ])
        with pytest.raises(rlc.RevertLoopError) as exc_info:
            rlc.fetch_status_activity_tail("http://fake/mcp", 10, post_fn=stub)
        assert exc_info.value.observation == "red"
        assert exc_info.value.reason == "probe_failed"

    def test_activity_log_read_failed_caveat_raises_step5(self):
        stub = _StubPost([
            (_make_init_response(), "sid"),
            (None, "sid"),
            (
                _make_tool_response(
                    [],
                    evidence_caveats={
                        "activity_log_tail": [
                            "activity.log read failed (I/O error). Tail not available.",
                        ],
                    },
                ),
                "sid",
            ),
        ])
        with pytest.raises(rlc.RevertLoopError) as exc_info:
            rlc.fetch_status_activity_tail(
                "http://fake/mcp", 10.0, post_fn=stub,
            )
        assert exc_info.value.code == 5
        assert "read failure" in exc_info.value.msg
        assert exc_info.value.observation == "red"
        assert exc_info.value.reason == "probe_failed"


class TestRunCanaryEndToEnd:
    def _run(self, tail: list[str]):
        stub = _StubPost([
            (_make_init_response(), "sid"),
            (None, "sid"),
            (_make_tool_response(tail), "sid"),
        ])
        return rlc.run_canary(
            "http://fake/mcp", 10.0,
            warn_window_min=10, warn_threshold=3,
            critical_window_min=20, critical_threshold=5,
            post_fn=stub, now=_now(),
        )

    def test_green_end_to_end(self):
        tail = [f"{_stamp(2)} [commit] Commit: score 0.9 -- KEEP"]
        code, _ = self._run(tail)
        assert code == 0

    def test_empty_tail_end_to_end_is_green(self):
        code, msg = self._run([])
        assert code == 0
        assert "empty" in msg

    def test_warn_end_to_end(self):
        tail = [_revert_line(9), _revert_line(6), _revert_line(3)]
        code, _ = self._run(tail)
        assert code == 2

    def test_critical_end_to_end(self):
        tail = [
            _revert_line(18), _revert_line(14), _revert_line(10),
            _revert_line(6), _revert_line(2),
        ]
        code, _ = self._run(tail)
        assert code == 3


def test_every_post_carries_canary_bearer():
    stub = _StubPost([
        (_make_init_response(), "sid"),
        (None, "sid"),
        (_make_tool_response([]), "sid"),
    ])
    rlc.fetch_status_activity_tail("http://fake/mcp", 5.0, post_fn=stub)
    assert all(call["bearer_token"] == "t" * 40 for call in stub.calls)


def test_missing_token_exits_before_post(monkeypatch, capsys):
    calls = []
    monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)
    monkeypatch.setattr(rlc, "_post", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(SystemExit) as exc:
        rlc.main([])
    assert exc.value.code == 2
    assert not calls
    assert "TINYASSETS_WIKI_CANARY_TOKEN" in capsys.readouterr().err

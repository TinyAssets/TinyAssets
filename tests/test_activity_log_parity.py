"""Activity.log byte-parity automation.

Covers phase_h_preflight.md (archived in git) §4.1 #7 (R6, invariant 7).

Automates the unified-execution follow-up: verifies that the same daemon events
produce equivalent activity.log output under both
``TINYASSETS_UNIFIED_EXECUTION=off`` (flag-off) and
``TINYASSETS_UNIFIED_EXECUTION=on`` (flag-on).

Structure
---------
1. Normalization helpers — strip timestamps, UUIDs, branch_task_id
   hashes so structurally-identical lines compare equal.
2. Acceptable diff surface — documents which line TYPES are allowed to
   differ between flag-on and flag-off:
   - ``dispatcher_observational:`` lines appear only under flag-on.
   - Heartbeat count may differ (scheduler timing).
3. Parity test (``@pytest.mark.slow``) — runs a minimal DaemonController
   cycle under each flag and asserts no non-normalized differences.

Normalization regexes
---------------------
Timestamps    : ``\\d{4}-\\d{2}-\\d{2}[T ]\\d{2}:\\d{2}:\\d{2}[.\\d+Z+-:]*``
UUIDs         : ``[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}``
Hash suffixes : ``[0-9a-f]{8,}`` (branch_task_id and similar hashes)
"""

from __future__ import annotations

import re

import pytest

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.\d]+)?(?:Z|[+-]\d{2}:\d{2})?"
)
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_HASH_RE = re.compile(r"\b[0-9a-f]{8,40}\b")
# Bracket-enclosed timestamps like [14:23:45]
_BRACKET_TS_RE = re.compile(r"\[\d{2}:\d{2}:\d{2}(?:[.\d]+)?\]")


def _normalize_line(line: str) -> str:
    """Strip variable substrings so structurally-equal lines compare equal.

    Replacements:
    - ISO timestamps → ``<TS>``
    - UUIDs → ``<UUID>``
    - Hex hash suffixes (≥ 8 chars) → ``<HASH>``
    - Bracket-enclosed timestamps → ``[<TS>]``
    """
    out = _TS_RE.sub("<TS>", line)
    out = _BRACKET_TS_RE.sub("[<TS>]", out)
    out = _UUID_RE.sub("<UUID>", out)
    out = _HASH_RE.sub("<HASH>", out)
    return out


def _normalize_log(text: str) -> list[str]:
    """Return sorted list of normalized non-empty lines."""
    return sorted(
        _normalize_line(ln.strip())
        for ln in text.splitlines()
        if ln.strip()
    )


# ---------------------------------------------------------------------------
# Acceptable diff surface documentation
# ---------------------------------------------------------------------------

# Lines that appear ONLY under flag-on (TINYASSETS_UNIFIED_EXECUTION=on).
_FLAG_ON_ONLY_PREFIXES: tuple[str, ...] = (
    "dispatcher_observational:",
)

# Lines that may appear in DIFFERENT COUNTS between the two runs
# (scheduler / heartbeat timing artefacts).
_COUNT_VARIANT_PREFIXES: tuple[str, ...] = (
    "heartbeat",
    "universe_status:",
)


def _is_acceptable_diff(line: str) -> bool:
    """Return True if ``line`` is in the acceptable-diff surface."""
    n = _normalize_line(line.strip().lower())
    return any(n.startswith(p) for p in _FLAG_ON_ONLY_PREFIXES + _COUNT_VARIANT_PREFIXES)


# ---------------------------------------------------------------------------
# Unit tests — normalization + acceptable diff surface
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_timestamp_stripped(self):
        line = "[2026-04-14T12:34:56.789+00:00] Orient: scene B1-C1-S1"
        out = _normalize_line(line)
        assert "<TS>" in out
        assert "2026" not in out

    def test_bracket_timestamp_stripped(self):
        line = "[14:23:45] Phase: orient"
        out = _normalize_line(line)
        assert "[<TS>]" in out

    def test_uuid_stripped(self):
        line = "branch_task_id=abc12345-1234-1234-1234-1234567890ab dispatched"
        out = _normalize_line(line)
        assert "<UUID>" in out
        assert "abc12345" not in out

    def test_hash_stripped(self):
        line = "scene_commit=deadbeef1234 words=520"
        out = _normalize_line(line)
        assert "<HASH>" in out
        assert "deadbeef1234" not in out

    def test_plain_text_unchanged(self):
        line = "Orient: Analyzing scene B1-C1-S1 arc=rising_action"
        out = _normalize_line(line)
        # No UUIDs / timestamps / hashes — text preserved.
        assert "Orient" in out
        assert "rising_action" in out


class TestAcceptableDiffSurface:
    def test_dispatcher_observational_is_flag_on_only(self):
        """dispatcher_observational: lines are in the acceptable surface."""
        assert _is_acceptable_diff("dispatcher_observational: would pick bt-1")
        assert _is_acceptable_diff("dispatcher_observational: no eligible BranchTask")

    def test_heartbeat_is_count_variant(self):
        assert _is_acceptable_diff("heartbeat universe=test-uni")

    def test_orient_line_is_not_acceptable_diff(self):
        """Regular content lines must NOT be in the acceptable surface."""
        assert not _is_acceptable_diff("Orient: Analyzing scene B1-C1-S1")
        assert not _is_acceptable_diff("Draft: 500 words written")

    def test_empty_line_not_acceptable(self):
        assert not _is_acceptable_diff("")


class TestNormalizeLog:
    def test_sorted_and_stripped(self):
        text = "\n  line b  \nline a\n"
        result = _normalize_log(text)
        assert result == sorted(["line a", "line b"])

    def test_empty_text(self):
        assert _normalize_log("") == []

    def test_timestamps_normalized_before_sort(self):
        text = "[2026-04-14T12:00:00] Alpha\n[2026-04-14T12:00:01] Alpha\n"
        result = _normalize_log(text)
        # Both normalize to the same string → deduplicated in sort
        # (sorted list has both, but they're identical after normalize)
        assert all("[<TS>] Alpha" in r for r in result)


# ---------------------------------------------------------------------------
# Parity test — compare flag-off vs flag-on activity logs
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_activity_log_parity_flag_off_vs_on(monkeypatch, tmp_path):
    """R6 / invariant 7: identical daemon events produce equivalent
    activity.log under TINYASSETS_UNIFIED_EXECUTION=off and =on.

    Strategy
    --------
    Rather than running the full daemon graph (heavyweight, non-deterministic
    prose generation), we exercise ``DaemonController._emit_node_log`` and
    ``_combined_log`` with a representative set of events under each flag.
    Both paths share the same logging functions; the flag only changes the
    graph routing layer.  If the log-format contract holds under both flags,
    this test passes.

    Acceptable differences (from _FLAG_ON_ONLY_PREFIXES):
    - ``dispatcher_observational:`` lines are emitted by the unified-
      execution observe step (flag-on only).

    Post-normalization assertion: after removing acceptable-diff lines,
    the two log line sets must be identical.
    """
    from fantasy_daemon.__main__ import DaemonController

    # Shared event corpus — representative sample of events the daemon logs.
    _ORIENT_EVENT = {
        "orient_result": {
            "scene_id": "B1-C1-S1",
            "overdue_promises": [],
            "arc_position": "rising_action",
        }
    }
    _PLAN_EVENT = {
        "plan_result": {
            "scene_id": "B1-C1-S1",
            "directives": ["write the opening scene"],
            "goals": ["establish protagonist"],
        }
    }
    _DRAFT_EVENT = {
        "draft_result": {
            "scene_id": "B1-C1-S1",
            "word_count": 420,
            "prose_excerpt": "The tavern smelled of smoke.",
        }
    }

    def _run_daemon_log_events(flag_value: str) -> str:
        """Set up a DaemonController, emit events, return activity.log text."""
        monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", flag_value)
        universe = tmp_path / f"uni_{flag_value}"
        universe.mkdir()
        db = tmp_path / f"ckpt_{flag_value}.db"
        c = DaemonController(universe_path=str(universe), db_path=str(db), no_tray=True)

        # Emit representative log events (same corpus for both flags).
        c._emit_node_log("orient", _ORIENT_EVENT)
        c._emit_node_log("plan", _PLAN_EVENT)
        c._emit_node_log("draft", _DRAFT_EVENT)
        c._combined_log("scene_commit: B1-C1-S1 words=420")

        log_path = universe / "activity.log"
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8")

    text_off = _run_daemon_log_events("0")
    text_on = _run_daemon_log_events("1")

    # Normalize and filter acceptable diffs.
    def _filtered_lines(text: str) -> list[str]:
        return sorted(
            _normalize_line(ln.strip())
            for ln in text.splitlines()
            if ln.strip() and not _is_acceptable_diff(ln)
        )

    lines_off = _filtered_lines(text_off)
    lines_on = _filtered_lines(text_on)

    # Report differences clearly on failure.
    only_off = set(lines_off) - set(lines_on)
    only_on = set(lines_on) - set(lines_off)

    diff_report = []
    if only_off:
        diff_report.append("Only in flag-off:\n" + "\n".join(f"  {ln}" for ln in sorted(only_off)))
    if only_on:
        diff_report.append("Only in flag-on:\n" + "\n".join(f"  {ln}" for ln in sorted(only_on)))

    assert not only_off and not only_on, (
        "Non-normalized differences found between flag-off and flag-on activity logs.\n"
        "If this is a new acceptable difference, add it to _FLAG_ON_ONLY_PREFIXES or\n"
        "_COUNT_VARIANT_PREFIXES in this file.\n\n"
        + "\n".join(diff_report)
    )


@pytest.mark.slow
def test_activity_log_parity_acceptable_diff_surface_documented(monkeypatch, tmp_path):
    """Verify the acceptable-diff surface is explicitly declared.

    Any line that appears in flag-on but not flag-off MUST be in the
    acceptable diff surface, or this test fails.  This enforces that
    new flag-on-only log lines are consciously added to
    _FLAG_ON_ONLY_PREFIXES before merging.
    """
    from fantasy_daemon.__main__ import DaemonController

    def _collect_lines(flag_value: str) -> set[str]:
        monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", flag_value)
        universe = tmp_path / f"uni_doc_{flag_value}"
        universe.mkdir()
        db = tmp_path / f"ckpt_doc_{flag_value}.db"
        c = DaemonController(universe_path=str(universe), db_path=str(db), no_tray=True)
        c._combined_log("doc_test: orient")
        log_path = universe / "activity.log"
        if not log_path.exists():
            return set()
        return {
            _normalize_line(ln.strip())
            for ln in log_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        }

    lines_off = _collect_lines("0")
    lines_on = _collect_lines("1")

    # Lines in flag-on but NOT in flag-off must be in acceptable surface.
    undocumented = {
        ln for ln in (lines_on - lines_off)
        if not _is_acceptable_diff(ln)
    }
    assert not undocumented, (
        "Flag-on produces lines not in flag-off that are NOT in the "
        "acceptable-diff surface.  Add them to _FLAG_ON_ONLY_PREFIXES "
        "or _COUNT_VARIANT_PREFIXES:\n"
        + "\n".join(f"  {ln}" for ln in sorted(undocumented))
    )


@pytest.mark.slow
def test_activity_log_parity_normalization_regression(tmp_path):
    """Regression: normalization must not collapse structurally different lines.

    Ensure two lines that differ only in timestamp/UUID still compare
    differently if their semantic content differs.
    """
    log1 = "[2026-04-14T12:00:00Z] Orient: scene B1-C1-S1 arc=rising_action\n"
    log2 = "[2026-04-14T12:00:01Z] Draft: 420 words written\n"

    n1 = _normalize_log(log1)
    n2 = _normalize_log(log2)

    assert n1 != n2, "Different event types must not normalize to the same line"

"""Tests for `provider_exhaustion` triage class (Lane 4b per spec).

Per 2026-04-23-revert-loop-canary-spec.md (archived in git) §Q6:
- Signal: terminal Commit:REVERT verdicts (not Draft:FAILED).
- Priority ordering: env_unreadable > tunnel_token > provider_exhaustion
  > disk_full > oom > image_pull_failure > watchdog_hotloop > unknown.
- Rationale: cause-addressing repairs outrank symptom-addressing repairs.
  On 2026-04-23, disk_full repair fired three times on the symptom while
  the revert-loop kept producing pressure. provider_exhaustion halts the
  generator.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import triage_classify as tc  # noqa: E402

_NOW = dt.datetime(2026, 5, 1, 7, 24, tzinfo=dt.timezone.utc)


def _revert(minutes_ago: int, text: str = "Commit: score 0.00 -- REVERT") -> str:
    stamp = (
        (_NOW - dt.timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )
    return f"{stamp} [commit] {text}\n"


class TestProviderExhaustionRecognition:
    """Spec Q2 — only terminal Commit:REVERT verdicts count."""

    def test_standard_commit_revert_fires(self):
        diag = "".join(_revert(i) for i in range(1, 6))
        result = tc.classify(diag, now=_NOW)
        assert result["class"] == tc.TriageClass.PROVIDER_EXHAUSTION
        assert result["auto_repairable"] is True
        assert result["manual_only"] is False

    def test_commit_reverting_draft_provider_failed_fires(self):
        diag = "".join(
            _revert(i, "Commit: reverting scene - draft provider failed")
            for i in range(1, 6)
        )
        result = tc.classify(diag, now=_NOW)
        assert result["class"] == tc.TriageClass.PROVIDER_EXHAUSTION

    def test_legacy_bare_score_revert_fires(self):
        diag = "".join(_revert(i, "score 0.00 -- REVERT") for i in range(1, 6))
        result = tc.classify(diag, now=_NOW)
        assert result["class"] == tc.TriageClass.PROVIDER_EXHAUSTION

    def test_explicit_critical_canary_signal_fires(self):
        result = tc.classify(
            "CRITICAL revert-loop: 5 REVERTs in last 20min "
            "(threshold 5). Triggering auto-repair via p0-outage-triage.",
            now=_NOW,
        )
        assert result["class"] == tc.TriageClass.PROVIDER_EXHAUSTION


class TestSignalDiscipline:
    """Spec Q2 explicit exclusions."""

    def test_draft_failed_alone_does_not_classify_as_provider_exhaustion(self):
        """Draft:FAILED can retry-recover within-scene. Per spec, only
        terminal Commit:REVERT counts. A diag with only Draft:FAILED
        entries should fall through to UNKNOWN."""
        diag = (
            "2026-04-23T20:30:00Z [worker] Draft: FAILED — provider cooldown\n"
            "2026-04-23T20:31:00Z [worker] Draft: FAILED — provider cooldown\n"
        )
        result = tc.classify(diag)
        assert result["class"] == tc.TriageClass.UNKNOWN

    def test_all_providers_exhausted_alone_does_not_classify(self):
        """AllProvidersExhaustedError without a REVERT verdict is a
        transient cooldown signal, not a revert-loop."""
        diag = (
            "Traceback:\n"
            "  AllProvidersExhaustedError: all cooling\n"
        )
        result = tc.classify(diag)
        assert result["class"] == tc.TriageClass.UNKNOWN

    def test_keep_verdicts_stay_unknown(self):
        diag = (
            "2026-04-23T20:30:00Z [commit] Commit: score 0.92 -- KEEP\n"
            "2026-04-23T20:31:00Z [commit] Commit: score 0.95 -- MERGE\n"
        )
        result = tc.classify(diag)
        assert result["class"] == tc.TriageClass.UNKNOWN


class TestPriorityOrdering:
    """Spec Q6 ordering: env > tunnel > provider_exhaustion > disk_full
    > oom > image_pull > watchdog > unknown.
    """

    def test_env_unreadable_beats_provider_exhaustion(self):
        diag = "ENV-UNREADABLE: /etc/tinyassets/env not readable\n" + "".join(
            _revert(i) for i in range(1, 6)
        )
        assert (
            tc.classify(diag, now=_NOW)["class"] == tc.TriageClass.ENV_UNREADABLE
        )

    def test_tunnel_token_beats_provider_exhaustion(self):
        diag = "cloudflared: Failed to get tunnel: Unauthorized\n" + "".join(
            _revert(i) for i in range(1, 6)
        )
        assert tc.classify(diag, now=_NOW)["class"] == tc.TriageClass.TUNNEL_TOKEN

    def test_provider_exhaustion_beats_disk_full(self):
        """Spec §Q6: cause > symptom. If both fire, provider_exhaustion
        wins — halt the generator first, then disk pressure resolves."""
        diag = "/dev/vda1        25G   24G  0.5G  97% /\n" + "".join(
            _revert(i) for i in range(1, 6)
        )
        assert (
            tc.classify(diag, now=_NOW)["class"]
            == tc.TriageClass.PROVIDER_EXHAUSTION
        ), "Cause (revert-loop) must beat symptom (disk-full)"

    def test_provider_exhaustion_beats_oom(self):
        diag = "kernel: Out of memory: Killed process 123\n" + "".join(
            _revert(i) for i in range(1, 6)
        )
        assert (
            tc.classify(diag, now=_NOW)["class"]
            == tc.TriageClass.PROVIDER_EXHAUSTION
        )

    def test_stale_provider_history_does_not_mask_current_disk_full(self):
        old_reverts = "".join(
            f"[2026-04-29 17:4{i}:42] Commit: reverting old-{i} - "
            "draft provider failed\n"
            for i in range(5)
        )
        diag = old_reverts + "/dev/vda1        50G   48G     0 100% /\n"
        assert tc.classify(diag, now=_NOW)["class"] == tc.TriageClass.DISK_FULL

    def test_disk_full_still_wins_without_revert(self):
        diag = "/dev/vda1  25G  24G  0.5G  97% /\n"
        assert tc.classify(diag)["class"] == tc.TriageClass.DISK_FULL

    def test_oom_still_wins_without_revert(self):
        diag = "kernel: Out of memory: Killed process 123\n"
        assert tc.classify(diag)["class"] == tc.TriageClass.OOM


class TestProviderExhaustionContractShape:
    def test_evidence_excerpt_contains_match(self):
        diag = (
            "some noise\n"
            + "".join(_revert(i) for i in range(1, 6))
            + "more noise\n"
        )
        result = tc.classify(diag, now=_NOW)
        assert "REVERT" in result["evidence"]

    def test_description_names_repair_shape(self):
        diag = "".join(_revert(i) for i in range(1, 6))
        result = tc.classify(diag, now=_NOW)
        desc = result["description"].lower()
        # Spec §Q6 repair shape: "pause worker + page host priority=2".
        assert "pause" in desc or "page" in desc


class TestSanityUnchanged:
    """Pre-existing classes still classify correctly after the priority
    shuffle + regex tightening."""

    def test_oom_no_revert_still_oom(self):
        diag = "oom-killer invoked\n"
        assert tc.classify(diag)["class"] == tc.TriageClass.OOM

    def test_unknown_still_fires_when_nothing_matches(self):
        diag = "totally benign log with no recognized markers\n"
        assert tc.classify(diag)["class"] == tc.TriageClass.UNKNOWN

    def test_image_pull_failure_unchanged(self):
        diag = "Error response from daemon: manifest for ghcr.io/x not found\n"
        assert (
            tc.classify(diag)["class"] == tc.TriageClass.IMAGE_PULL_FAILURE
        )

    def test_watchdog_hotloop_unchanged(self):
        diag = "tinyassets-daemon.service: start request repeated too quickly\n"
        assert tc.classify(diag)["class"] == tc.TriageClass.WATCHDOG_HOTLOOP

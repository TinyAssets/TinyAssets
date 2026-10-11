"""Pure classification plus the exact producer-to-Actions output boundary."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import revert_loop_canary as revert  # noqa: E402
import uptime_observations as observations  # noqa: E402


def _statuses(**overrides):
    return dict.fromkeys(observations.PROBES, "0") | overrides


def test_all_required_positive_results_are_green():
    assert observations.classify_observations(_statuses())["overall"] == "green"


@pytest.mark.parametrize("name", observations.PROBES)
def test_missing_required_result_is_unknown_not_skipped_to_green(name):
    result = observations.classify_observations(_statuses(**{name: ""}))
    assert result["overall"] == "unknown"
    assert result["status"] == "unknown"


def test_known_unavailable_legacy_evidence_is_not_health_or_outage():
    result = observations.classify_observations(
        _statuses(revert="5"), revert_observation="unknown",
        revert_reason="legacy_evidence_unavailable",
    )
    assert result["overall"] == "unknown"


@pytest.mark.parametrize("name", ("handshake", "tool", "activity", "wiki"))
def test_real_failure_outranks_unavailable_legacy_evidence(name):
    result = observations.classify_observations(
        _statuses(revert="5", **{name: "2"}), revert_observation="unknown",
        revert_reason="legacy_evidence_unavailable",
    )
    assert result["overall"] == "red"
    assert result["status"] == "2"
    assert result["observations"]["revert"]["state"] == "unknown"


@pytest.mark.parametrize("producer", [{}, {"revert_observation": "unknown"}, {
    "revert_observation": "unknown", "revert_reason": "probe_failed",
}, {"revert_observation": "red", "revert_reason": "legacy_evidence_unavailable"}])
def test_exit5_without_exact_producer_pair_stays_red(producer):
    assert observations.classify_observations(_statuses(revert="5"), **producer)["overall"] == "red"


def test_upstream_red_preserved_when_dependents_did_not_execute():
    result = observations.classify_observations({"handshake": "2"})
    assert result["overall"] == "red"
    assert result["observations"]["wiki"]["state"] == "unknown"


@pytest.mark.parametrize("reason,state", [
    ("legacy_evidence_unavailable", "unknown"), ("probe_failed", "red"),
])
def test_revert_main_publishes_machine_observation_to_actions(
    tmp_path, monkeypatch, capsys, reason, state,
):
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(revert, "canary_bearer_for", lambda *args: "test")

    def fail(*args, **kwargs):
        raise revert.RevertLoopError(5, "Human wording is not parsed", reason=reason)

    monkeypatch.setattr(revert, "run_canary", fail)
    assert revert.main(["--format", "gha"]) == 5
    assert output.read_text() == f"revert_observation={state}\nrevert_reason={reason}\n"
    assert f"revert_observation={state}" in capsys.readouterr().out


def test_actual_output_path_keeps_unknown_visible_and_cannot_recover(tmp_path, monkeypatch):
    output, summary = tmp_path / "output", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    for name, code in _statuses(revert="5").items():
        monkeypatch.setenv(f"{name.upper()}_STATUS", code)
    monkeypatch.setenv("REVERT_OBSERVATION", "unknown")
    monkeypatch.setenv("REVERT_REASON", "legacy_evidence_unavailable")
    assert observations.main() == 0
    assert "overall=unknown\n" in output.read_text()
    assert "overall=green" not in output.read_text().splitlines()
    assert "Automatic incident recovery is unavailable" in summary.read_text()
    assert "current-engine execution-quality" in summary.read_text()

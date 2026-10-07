"""Regression guards for the retired standalone continue_branch action."""

from __future__ import annotations


def test_continue_branch_is_not_a_branch_action() -> None:
    from tinyassets.api.branches import _BRANCH_ACTIONS

    assert "continue_branch" not in _BRANCH_ACTIONS


def test_control_station_reports_resume_gap_after_reading_prior_run() -> None:
    from tinyassets.api.prompts import _CONTROL_STATION_PROMPT

    assert 'read_graph target="runs"' in _CONTROL_STATION_PROMPT
    assert 'read_graph target="run"' in _CONTROL_STATION_PROMPT
    assert "Resume-from-run is not exposed" in _CONTROL_STATION_PROMPT
    assert "action=continue_branch" not in _CONTROL_STATION_PROMPT

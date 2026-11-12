"""main-red's decision: a red main is re-run once, then alarmed, and never reverted."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main_red  # noqa: E402


@pytest.mark.parametrize("required,attempt,tip,expected", [
    ("failure", 1, True, "rerun"),
    ("timed_out", 1, False, "rerun"),
    ("failure", 2, True, "alarm"),
    ("failure", 2, False, "alarm"),
    ("timed_out", 3, True, "alarm"),
    ("success", 1, True, "close-alarm"),
    ("success", 2, False, "none"),
    # No verdict about the code: cancelled, skipped, or no required job at all.
    ("cancelled", 2, True, "none"),
    ("skipped", 1, True, "none"),
    (None, 2, True, "none"),
])
def test_a_red_main_is_rerun_once_then_alarmed(required, attempt, tip, expected) -> None:
    assert main_red.decide(attempt=attempt, required=required,
                           head_is_main_tip=tip) == expected


def test_nothing_reverts() -> None:
    # The decision reads no parent and no event, so no merge can be named a cause, and
    # the script holds no way to push a branch or open a pull request.
    assert set(inspect.signature(main_red.decide).parameters) == {
        "attempt", "required", "head_is_main_tip"}
    source = inspect.getsource(main_red)
    for word in ('"revert"', '"checkout"', '"pr"', "REVERT_TOKEN", "x-access-token"):
        assert word not in source, word
    body = main_red.render_body(sha="a" * 40, subject="s", run_url="u",
                                failures=["tests/test_x.py::test_y"])
    assert "nothing is reverted automatically" in body and "tests/test_x.py::test_y" in body
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows"
                / "main-red.yml").read_text(encoding="utf-8")
    assert "secrets." not in workflow

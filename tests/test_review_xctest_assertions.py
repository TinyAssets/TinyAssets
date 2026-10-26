"""The review harness must keep Xcode 26 failure lines, and only those."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "mobile" / "scripts" / "review_xctest.py"
_spec = importlib.util.spec_from_file_location("review_xctest", _PATH)
assert _spec and _spec.loader
review_xctest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_xctest)

_CASE = "-[ReviewUITests.ReviewVideoTests testGuideline21Journey]"
_ERROR = (f"/tmp/ReviewVideoTests.swift:252:17: error: {_CASE} : 02-sign-in: tinyassets "
          "callback returned to signed-in app. Hierarchy: Verify you are human")
_FAILED = f"Test Case '{_CASE}' failed (90.000 seconds)."


@pytest.mark.verifies("ios-review-video:Credential-safe bounded recording")
def test_xcode26_failure_lines_are_kept_and_passing_noise_is_not():
    log = "\n".join([
        f"Test Case '{_CASE}' started.",
        _ERROR,
        "Failing tests:",
        "\tReviewVideoTests.testGuideline21Journey()",
        "Test run with 1 test in 1 suite failed after 90.0 seconds with 1 issue.",
        "** TEST FAILED **",
        "xcodebuild: error: Unable to find a destination",
        "/tmp/ReviewVideoTests.swift:7:9: Expectation failed: signed in",
        _FAILED,
        "Can't verify the user is human",
        "Executed 1 test, with 0 failures (0 unexpected) in 0.1 seconds",
    ])
    kept = review_xctest.assertion_lines(log)
    assert kept == [
        _ERROR,
        "Failing tests:",
        "Test run with 1 test in 1 suite failed after 90.0 seconds with 1 issue.",
        "** TEST FAILED **",
        "xcodebuild: error: Unable to find a destination",
        "/tmp/ReviewVideoTests.swift:7:9: Expectation failed: signed in",
        _FAILED,
        "Can't verify the user is human",
    ]


@pytest.mark.verifies("ios-review-video:Credential-safe bounded recording")
def test_legacy_line_without_a_column_still_matches():
    line = ("ReviewVideoTests.swift:174: error: "
            "-[ReviewUITests.ReviewVideoTests testSignedOutLaunch] : XCTAssertTrue failed")
    assert review_xctest.assertion_lines(line + "\nnote: building\n") == [line]


@pytest.mark.verifies("ios-review-video:Credential-safe bounded recording")
def test_zero_issues_and_a_started_case_are_not_assertions():
    log = "\n".join([
        f"Test Case '{_CASE}' started.",
        "Test run with 1 test passed after 1.0 seconds with 0 issues.",
    ])
    assert review_xctest.assertion_lines(log) == []

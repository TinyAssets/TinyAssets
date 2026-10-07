"""Tests for the get_recent_events MCP action (task #50).

Verifies the tag-parsing helper and the action's filter/limit/
evidence-caveat shape. Uses tmp_path activity.log fixtures so tests
are independent of the host state.

Primary consumer: user-sim + chatbot observability on dispatch-guard /
overshoot / revert-gate events (concern 1 resolution path).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.api import helpers
from tinyassets.api.universe import (
    _action_get_recent_events,
    _parse_activity_line,
)

# -------------------------------------------------------------------
# _parse_activity_line — regex helper
# -------------------------------------------------------------------


def test_parse_tagged_line():
    result = _parse_activity_line(
        "[2026-04-19 20:30:00] [dispatch_guard] blocked C1-S4 overshoot"
    )
    assert result["ts"] == "2026-04-19 20:30:00"
    assert result["tag"] == "dispatch_guard"
    assert result["message"] == "blocked C1-S4 overshoot"


def test_parse_untagged_legacy_line():
    """Legacy format without a `[TAG]` bracket — tag field empty."""
    result = _parse_activity_line(
        "[2026-04-19 20:30:00] Commit: evaluating scene-1"
    )
    assert result["ts"] == "2026-04-19 20:30:00"
    assert result["tag"] == ""
    assert result["message"] == "Commit: evaluating scene-1"


def test_parse_unparseable_line_surfaces_raw():
    """A line that doesn't match the `[TS]` prefix falls back to raw."""
    garbled = "not a valid log line"
    result = _parse_activity_line(garbled)
    # Regex requires `^\[` so no match → all empty + raw holds the source.
    assert result["raw"] == garbled


def test_parse_strips_trailing_newline():
    result = _parse_activity_line(
        "[2026-04-19 20:30:00] [tag] message\n"
    )
    assert not result["raw"].endswith("\n")


# -------------------------------------------------------------------
# _action_get_recent_events — end-to-end through tmp_path fixtures
# -------------------------------------------------------------------


@pytest.fixture
def universe_with_log(tmp_path, monkeypatch):
    """Create a fake universe dir with a populated activity.log.

    Patches `_universe_dir` so the action finds our synthetic universe
    rather than the host's real output directory.
    """
    from tinyassets.api import universe as uni

    udir = tmp_path / "test-universe"
    udir.mkdir()
    # Point the resolver at THIS root and give the directory an owner. Since
    # 2026-09-02 a universe exists because an ownership row names it, so the
    # default resolvers this fixture exercises skip an unowned directory -- and
    # without the data-dir override they were consulting the host's real root.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(
        tmp_path, founder_sub="test-owner::test-universe", universe_id="test-universe",
    )
    log = udir / "activity.log"
    log.write_text(
        "[2026-04-19 10:00:00] Commit: evaluating scene-1\n"
        "[2026-04-19 10:01:00] [dispatch_guard] blocked C1-S4 overshoot\n"
        "[2026-04-19 10:02:00] [revert_gate] reverting scene-3 low score\n"
        "[2026-04-19 10:03:00] [dispatch_execution] selected task draft\n"
        "[2026-04-19 10:04:00] Worldbuild: 3 canon docs generated\n",
        encoding="utf-8",
    )
    def _fake_universe_dir(uid, _expected="test-universe"):
        # Assert the RESOLVED uid rather than ignoring it. Without this the
        # `_default_universe` patch below is not load-bearing: the resolver
        # could return anything and these tests would still pass, because
        # nothing else reads the universe id.
        assert uid == _expected, f"resolved universe {uid!r}, expected {_expected!r}"
        return udir

    monkeypatch.setattr(uni, "_universe_dir", _fake_universe_dir)
    # `_default_universe` MOVED to tinyassets.api.helpers and is now
    # reached through `_request_universe`, so patching it on `uni`
    # raised AttributeError and errored these tests at SETUP. Patch it
    # where it now lives: `_request_universe` is defined in helpers and
    # resolves the name as a module global there, so this still covers
    # the default-resolution path these tests exist to exercise (the
    # anonymous branch, which is what an uncredentialed test hits).
    monkeypatch.setattr(helpers, "_default_universe", lambda: "test-universe")
    # ...and the branch an AUTHENTICATED caller with no bound home
    # takes: `_designated_public_universe`, which honours this first.
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "test-universe")
    return udir


def _parse_response(raw: str) -> dict:
    return json.loads(raw)


def test_no_filter_returns_all_events_newest_first(universe_with_log):
    response = _parse_response(_action_get_recent_events())

    events = response["events"]
    assert len(events) == 5
    # Newest first.
    assert events[0]["ts"] == "2026-04-19 10:04:00"
    assert events[-1]["ts"] == "2026-04-19 10:00:00"
    assert response["source"] == "activity.log"


def test_tag_filter_exact_match(universe_with_log):
    response = _parse_response(
        _action_get_recent_events(tag="dispatch_guard")
    )

    assert len(response["events"]) == 1
    assert response["events"][0]["tag"] == "dispatch_guard"
    assert response["events"][0]["message"] == "blocked C1-S4 overshoot"


def test_tag_filter_prefix_match(universe_with_log):
    """Prefix match: ``dispatch`` matches both dispatch_guard AND
    dispatch_execution."""
    response = _parse_response(
        _action_get_recent_events(tag="dispatch")
    )

    assert len(response["events"]) == 2
    tags = {e["tag"] for e in response["events"]}
    assert tags == {"dispatch_guard", "dispatch_execution"}


def test_tag_filter_no_match_returns_caveat(universe_with_log):
    response = _parse_response(
        _action_get_recent_events(tag="nonexistent_tag")
    )

    assert response["events"] == []
    assert any("matched 0" in c for c in response["caveats"])
    assert response["matched"] == 0


def test_dispatch_guard_empty_match_adds_absence_caveat(tmp_path, monkeypatch):
    """tag='dispatch_guard' with zero matches must warn empty != no-overshoots."""
    from tinyassets.api import universe as uni

    udir = tmp_path / "no-dispatch-universe"
    udir.mkdir()
    # ...and an owner plus this data root, or the default resolvers skip it
    # (2026-09-02: a universe exists because an ownership row names it).
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(
        tmp_path,
        founder_sub="test-owner::no-dispatch-universe",
        universe_id="no-dispatch-universe",
    )
    (udir / "activity.log").write_text(
        "[2026-04-19 10:00:00] [revert_gate] reverting scene-3\n"
        "[2026-04-19 10:01:00] Commit: evaluating scene-1\n",
        encoding="utf-8",
    )
    def _fake_universe_dir(uid, _expected="no-dispatch-universe"):
        # Assert the RESOLVED uid rather than ignoring it. Without this the
        # `_default_universe` patch below is not load-bearing: the resolver
        # could return anything and these tests would still pass, because
        # nothing else reads the universe id.
        assert uid == _expected, f"resolved universe {uid!r}, expected {_expected!r}"
        return udir

    monkeypatch.setattr(uni, "_universe_dir", _fake_universe_dir)
    # `_default_universe` MOVED to tinyassets.api.helpers and is now
    # reached through `_request_universe`, so patching it on `uni`
    # raised AttributeError and errored these tests at SETUP. Patch it
    # where it now lives: `_request_universe` is defined in helpers and
    # resolves the name as a module global there, so this still covers
    # the default-resolution path these tests exist to exercise (the
    # anonymous branch, which is what an uncredentialed test hits).
    monkeypatch.setattr(helpers, "_default_universe", lambda: "no-dispatch-universe")
    # ...and the branch an AUTHENTICATED caller with no bound home
    # takes: `_designated_public_universe`, which honours this first.
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "no-dispatch-universe")

    response = _parse_response(_action_get_recent_events(tag="dispatch_guard"))

    assert response["events"] == []
    assert any("matched 0" in c for c in response["caveats"])
    assert any(
        "Empty dispatch_guard list does not prove no overshoots" in c
        for c in response["caveats"]
    )


def test_dispatch_guard_missing_log_adds_absence_caveat(tmp_path, monkeypatch):
    """tag='dispatch_guard' on a universe with no activity.log still warns."""
    from tinyassets.api import universe as uni

    udir = tmp_path / "fresh-dispatch-universe"
    udir.mkdir()
    # ...and an owner plus this data root, or the default resolvers skip it
    # (2026-09-02: a universe exists because an ownership row names it).
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(
        tmp_path,
        founder_sub="test-owner::fresh-dispatch-universe",
        universe_id="fresh-dispatch-universe",
    )
    def _fake_universe_dir(uid, _expected="fresh-dispatch-universe"):
        # Assert the RESOLVED uid rather than ignoring it. Without this the
        # `_default_universe` patch below is not load-bearing: the resolver
        # could return anything and these tests would still pass, because
        # nothing else reads the universe id.
        assert uid == _expected, f"resolved universe {uid!r}, expected {_expected!r}"
        return udir

    monkeypatch.setattr(uni, "_universe_dir", _fake_universe_dir)
    # `_default_universe` MOVED to tinyassets.api.helpers and is now
    # reached through `_request_universe`, so patching it on `uni`
    # raised AttributeError and errored these tests at SETUP. Patch it
    # where it now lives: `_request_universe` is defined in helpers and
    # resolves the name as a module global there, so this still covers
    # the default-resolution path these tests exist to exercise (the
    # anonymous branch, which is what an uncredentialed test hits).
    monkeypatch.setattr(helpers, "_default_universe", lambda: "fresh-dispatch-universe")
    # ...and the branch an AUTHENTICATED caller with no bound home
    # takes: `_designated_public_universe`, which honours this first.
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "fresh-dispatch-universe")

    response = _parse_response(_action_get_recent_events(tag="dispatch_guard"))

    assert response["events"] == []
    assert any("No activity.log" in c for c in response["caveats"])
    assert any(
        "Empty dispatch_guard list does not prove no overshoots" in c
        for c in response["caveats"]
    )


def test_dispatch_guard_with_matches_no_absence_caveat(universe_with_log):
    """When dispatch_guard events DO match, absence caveat must NOT fire."""
    response = _parse_response(
        _action_get_recent_events(tag="dispatch_guard")
    )

    assert len(response["events"]) == 1
    assert not any(
        "Empty dispatch_guard list does not prove" in c
        for c in response["caveats"]
    )


def test_non_dispatch_tag_empty_match_skips_absence_caveat(universe_with_log):
    """Absence caveat is specific to dispatch_guard — other empty tags unaffected."""
    response = _parse_response(
        _action_get_recent_events(tag="nonexistent_tag")
    )

    assert response["events"] == []
    assert any("matched 0" in c for c in response["caveats"])
    assert not any(
        "Empty dispatch_guard list" in c for c in response["caveats"]
    )


def test_untagged_caveat_when_no_filter(universe_with_log):
    """The two legacy untagged lines surface a caveat on unfiltered reads."""
    response = _parse_response(_action_get_recent_events())

    assert any("carry no tag" in c for c in response["caveats"])


def test_missing_log_returns_empty_with_caveat(tmp_path, monkeypatch):
    from tinyassets.api import universe as uni

    udir = tmp_path / "fresh-universe"
    udir.mkdir()  # no activity.log inside
    # ...but an owner and this data root, or the default resolvers skip it
    # (2026-09-02: a universe exists because an ownership row names it).
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(
        tmp_path,
        founder_sub="test-owner::fresh-universe",
        universe_id="fresh-universe",
    )
    def _fake_universe_dir(uid, _expected="fresh-universe"):
        # Assert the RESOLVED uid rather than ignoring it. Without this the
        # `_default_universe` patch below is not load-bearing: the resolver
        # could return anything and these tests would still pass, because
        # nothing else reads the universe id.
        assert uid == _expected, f"resolved universe {uid!r}, expected {_expected!r}"
        return udir

    monkeypatch.setattr(uni, "_universe_dir", _fake_universe_dir)
    # `_default_universe` MOVED to tinyassets.api.helpers and is now
    # reached through `_request_universe`, so patching it on `uni`
    # raised AttributeError and errored these tests at SETUP. Patch it
    # where it now lives: `_request_universe` is defined in helpers and
    # resolves the name as a module global there, so this still covers
    # the default-resolution path these tests exist to exercise (the
    # anonymous branch, which is what an uncredentialed test hits).
    monkeypatch.setattr(helpers, "_default_universe", lambda: "fresh-universe")
    # ...and the branch an AUTHENTICATED caller with no bound home
    # takes: `_designated_public_universe`, which honours this first.
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "fresh-universe")

    response = _parse_response(_action_get_recent_events())

    assert response["events"] == []
    assert response["source"] == "activity.log"
    assert any("No activity.log" in c for c in response["caveats"])


def test_limit_clamps_at_500(universe_with_log):
    """Limit > 500 is clamped. Sanity check against the 500-line cap."""
    response = _parse_response(_action_get_recent_events(limit=10000))

    # 5 entries total in our fixture; still just 5 returned.
    assert len(response["events"]) == 5


def test_limit_returns_most_recent_slice(universe_with_log):
    """Limit=2 returns the 2 most recent of 5 events."""
    response = _parse_response(_action_get_recent_events(limit=2))

    events = response["events"]
    assert len(events) == 2
    # Newest first: 10:04:00, then 10:03:00.
    assert events[0]["ts"] == "2026-04-19 10:04:00"
    assert events[1]["ts"] == "2026-04-19 10:03:00"


def test_total_lines_reflects_log_not_matched(universe_with_log):
    """total_lines = full log; matched = post-filter; returned = post-limit."""
    response = _parse_response(
        _action_get_recent_events(tag="dispatch", limit=1)
    )

    assert response["total_lines"] == 5
    assert response["matched"] == 2  # dispatch_guard + dispatch_execution
    assert response["returned"] == 1


# -------------------------------------------------------------------
# Dispatch table wiring — regression guard
# -------------------------------------------------------------------


# -------------------------------------------------------------------
# activity_log tag kwarg — backward compat + new behavior
# -------------------------------------------------------------------


def test_activity_log_backward_compat_no_tag(tmp_path):
    from domains.fantasy_daemon.phases._activity import activity_log

    state = {"_universe_path": str(tmp_path)}
    activity_log(state, "legacy message")

    content = (tmp_path / "activity.log").read_text(encoding="utf-8")
    # Legacy format: no `[tag]` bracket.
    assert "legacy message" in content
    # No tag bracket in the line body — only the timestamp bracket.
    # Count `[` occurrences on that line: exactly 1 (for the timestamp).
    line = content.strip().splitlines()[0]
    assert line.count("[") == 1


def test_activity_log_with_tag_inserts_bracket(tmp_path):
    from domains.fantasy_daemon.phases._activity import activity_log

    state = {"_universe_path": str(tmp_path)}
    activity_log(state, "overshoot blocked", tag="dispatch_guard")

    content = (tmp_path / "activity.log").read_text(encoding="utf-8")
    assert "[dispatch_guard] overshoot blocked" in content


def test_activity_log_tag_roundtrips_through_parser(tmp_path):
    """The writer + parser must agree on format — end-to-end guard."""
    from domains.fantasy_daemon.phases._activity import activity_log

    state = {"_universe_path": str(tmp_path)}
    activity_log(state, "roundtrip test", tag="dispatch_guard")

    line = Path(tmp_path / "activity.log").read_text(
        encoding="utf-8"
    ).strip().splitlines()[0]

    parsed = _parse_activity_line(line)
    assert parsed["tag"] == "dispatch_guard"
    assert parsed["message"] == "roundtrip test"

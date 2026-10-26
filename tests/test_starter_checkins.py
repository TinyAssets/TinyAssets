"""The starter watch runner fails loudly on an invalid observation (Fact 8)."""

import json
import runpy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

CHECKINS = Path(__file__).resolve().parents[1] / "tinyassets/starter/muse/checkins.py"
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
check = runpy.run_path(str(CHECKINS))["check"]


def watch(**fields):
    return {"id": "w", "active": True, "requested": True, "matched": False,
            "observed_at": NOW.isoformat(), "change_id": "c1", "summary": "Changed"} | fields


def home(tmp_path, *watches, notified=None):
    folder = tmp_path / "starter"
    folder.mkdir()
    (folder / "settings.json").write_text(json.dumps({"proactivity": "high"}))
    (folder / "monitors.json").write_text(json.dumps({"monitors": list(watches)}))
    if notified is not None:
        (folder / "monitors-state.json").write_text(json.dumps(notified))
    return folder


@pytest.mark.verifies("starter-muse-package:Meaningful notifications")
@pytest.mark.parametrize("fields", [
    {"matched": None},
    {"matched": "yes"},
    {"observed_at": None},
    {"observed_at": "2026-10-26T12:00:00"},
    {"observed_at": (NOW - timedelta(hours=2)).isoformat()},
    {"observed_at": (NOW + timedelta(minutes=1)).isoformat()},
])
def test_an_invalid_watch_fails_the_batch_before_any_notice(tmp_path, fields):
    folder = home(tmp_path, watch(id="good", matched=True), watch(**fields))
    sent = []
    with pytest.raises(ValueError):
        check(tmp_path, "monitors", now=NOW, send=lambda *a: sent.append(a))
    assert sent == [] and not (folder / "monitors-state.json").exists()


@pytest.mark.verifies("starter-muse-package:Meaningful notifications")
def test_a_fresh_nonmatch_keeps_the_last_notified_change(tmp_path):
    folder = home(tmp_path, watch(), notified={"w": "c0"})
    result = check(tmp_path, "monitors", now=NOW, send=lambda *a: pytest.fail("notified"))
    assert result["changes"] == 0
    assert json.loads((folder / "monitors-state.json").read_text()) == {"w": "c0"}


@pytest.mark.verifies("starter-muse-package:Meaningful notifications")
def test_inactive_or_unrequested_watches_need_no_observation(tmp_path):
    home(tmp_path, watch(id="off", active=False, matched=None, observed_at=None),
         watch(id="unasked", requested=False, matched=None, observed_at=None))
    assert check(tmp_path, "monitors", now=NOW)["changes"] == 0

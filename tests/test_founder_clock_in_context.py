"""The universe knows its founder's clock and never asks for it.

Live 2026-09-30: asked for a daily morning note, the universe opened a request
for "time and timezone" -- which the app reports at every sign-in and the
scheduler already uses. The platform knew; the universe was never told.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tinyassets.api.interlocutor as interlocutor
import tinyassets.universe_intelligence as ui
from tinyassets.universe_bundle import seed_okf_bundle

UID = "u-clock"
OWNER = "user_clock_owner"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


def _seed(tmp_path: Path, *, zone: str = "") -> Path:
    import tinyassets.api.visibility as vis
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_ownership,
    )
    from tinyassets.storage.account_timezone import set_account_timezone

    udir = tmp_path / UID
    udir.mkdir()
    seed_okf_bundle(udir, purpose="To help my founder.")
    ensure_universe_registered(tmp_path, universe_id=UID, universe_path=udir)
    grant_universe_ownership(tmp_path, universe_id=UID, owner_id=OWNER)
    vis.set_universe_visibility(UID, "public", source="owner")
    if zone:
        set_account_timezone(tmp_path, owner_user_id=OWNER, timezone_name=zone)
    return udir


def test_the_founder_turn_carries_the_stored_timezone(tmp_path: Path) -> None:
    udir = _seed(tmp_path, zone="America/Los_Angeles")
    prompt = ui._build_persona_system_prompt(
        udir, universe_id=UID, tier=interlocutor.FOUNDER,
    )
    assert "America/Los_Angeles" in prompt


def test_a_visitor_turn_does_not_learn_the_founders_clock(tmp_path: Path) -> None:
    udir = _seed(tmp_path, zone="America/Los_Angeles")
    prompt = ui._build_persona_system_prompt(
        udir, universe_id=UID, tier=interlocutor.T1,
    )
    assert "America/Los_Angeles" not in prompt
    assert "founder's clock" not in prompt


def test_an_unknown_zone_adds_nothing_rather_than_a_guess(tmp_path: Path) -> None:
    udir = _seed(tmp_path)
    prompt = ui._build_persona_system_prompt(
        udir, universe_id=UID, tier=interlocutor.FOUNDER,
    )
    assert "founder's clock" not in prompt

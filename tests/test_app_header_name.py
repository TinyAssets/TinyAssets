"""The app header names the command center and never shows its id.

Seen 2026-10-01 in the founder's desktop app: the header read
"u-01kxm1vszd8hwp7em418asq8h9". ``get_status`` has no name, so the header fell
back to ``s.universe`` / ``s.universe_id``. Now ``/app/me`` carries the learned
name (or nothing), and the page refuses anything id-shaped.
"""
from __future__ import annotations

import re

import pytest

from tests.test_app_browser_notifications import functions, run_js
from tests.test_onboarding_app import _js_function
from tests.test_onboarding_model_setup import _me
from tinyassets.onboarding import render_app_html

PRELUDE = """
const COMMAND_CENTER_FALLBACK="Your command center";
const header={textContent:"Your command center"};
const $=id=>id==="universe-name"?header:null;
"""


def _shown(value) -> str:
    import json

    return run_js(functions("commandCenterName", "showCommandCenterName") + PRELUDE
                  + f"showCommandCenterName({json.dumps(value)});"
                  + "console.log(JSON.stringify(header.textContent));")


@pytest.mark.parametrize("value", [
    "u-01kxm1vszd8hwp7em418asq8h9", "U-0000000000000001", "cc-01kxm1vszd8hwp7em418asq8h9",
    "", "   ", None,
])
def test_an_id_or_no_name_shows_the_fallback(value):
    assert _shown(value) == "Your command center"


def test_a_learned_name_is_shown():
    assert _shown("  Ashwater ") == "Ashwater"


def test_nothing_else_writes_the_header():
    """Every write to the header goes through the guard, so no reader that
    falls back to an id can reach the screen."""
    html, _ = render_app_html()
    writes = re.findall(r'\$\("universe-name"\)\.textContent\s*=', html)
    guarded = _js_function(html, "showCommandCenterName")
    assert writes == []
    assert "commandCenterName(value)" in guarded
    assert "universe-name" not in _js_function(html, "pollStatus")
    assert "showCommandCenterName(me&&me.name)" in _js_function(html, "enterSignedIn")


def test_me_sends_no_name_until_one_is_learned(tmp_path, monkeypatch):
    from tinyassets.daemon_server import ensure_universe_registered

    (tmp_path / "u-owner").mkdir()
    _, unregistered = _me(tmp_path, monkeypatch)
    ensure_universe_registered(tmp_path, universe_id="u-owner",
                               universe_path=tmp_path / "u-owner")
    _, defaulted = _me(tmp_path, monkeypatch)

    assert unregistered["name"] == ""
    # The row's default display_name is the id itself: not a name.
    assert defaulted["name"] == ""


def test_me_sends_the_learned_name(tmp_path, monkeypatch):
    from tinyassets.daemon_server import ensure_universe_registered, set_universe_display_name

    (tmp_path / "u-owner").mkdir()
    ensure_universe_registered(tmp_path, universe_id="u-owner",
                               universe_path=tmp_path / "u-owner")
    set_universe_display_name(tmp_path, universe_id="u-owner", display_name="Ashwater")

    _, doc = _me(tmp_path, monkeypatch)

    assert doc["name"] == "Ashwater"

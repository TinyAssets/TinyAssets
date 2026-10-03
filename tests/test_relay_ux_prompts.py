"""Onboarding / relay UX guards (2026-07-03 live-test findings).

Regression guards for the behavioral fixes: first-person is the default (no
consent menu), the relay stays thin (no over-narration), the connector does not
do the universe's work, the engine is sandboxed, and learning extraction does
not stamp generic identity boilerplate.
"""
from __future__ import annotations

import tinyassets.universe_intelligence as ui
from tinyassets.api.prompts import _CONTROL_STATION_PROMPT

# Whitespace-normalized so assertions don't break on line wrapping.
_CS = " ".join(_CONTROL_STATION_PROMPT.split())


def test_first_person_is_default_no_consent_menu():
    # after creation, bring them into contact immediately in first person
    assert "first-person contact IS the default" in _CS
    # no menu / no "do you want first person?" gate
    assert "present a menu of choices" in _CS
    assert "Do NOT pause to" in _CS


def test_opening_message_relays_to_converse_not_status():
    assert "opening message" in _CS
    assert "relay it through `converse` first" in _CS
    assert "do not call `get_status` as the opening experience" in _CS.lower()
    assert "get_status` auto-creates" not in _CS


def test_relay_is_thin_no_over_narration():
    assert "THIN relay" in _CS
    assert "do NOT append your own" in _CS


def test_connector_does_not_do_the_universes_work():
    assert "do NOT fetch, research, or answer it yourself" in _CS
    assert "never assume what the command center can or cannot do" in _CS


def test_learning_extraction_guards_generic_identity():
    assert "NEVER restate your own generic nature" in ui._LEARNING_SYSTEM
    # identity.md is only filled when the founder explicitly says who/names it
    assert "gave me a name" in ui._LEARNING_SYSTEM


def test_engine_sandbox_denies_host_tools():
    # web-only for the reply turn; host tools + filesystem denied
    assert ui._ENGINE_ALLOWED_TOOLS == ("WebFetch",)
    for denied in ("Bash", "Read", "Write", "Edit", "WebSearch", "Task", "Glob", "Grep"):
        assert denied in ui._ENGINE_DISALLOWED_TOOLS

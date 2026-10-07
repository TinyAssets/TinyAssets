"""D4a: onboarding is one bounded instruction in the resident agent's turn."""

import json

import pytest

from tinyassets.onboarding_note import _MAX_ONBOARDING_BYTES, agent_identity, onboarding_note
from tinyassets.universe_intelligence import read_operating_instructions
from tinyassets.universe_tools import harness_prompt


def _onboard(universe):
    (universe / "identity.md").write_text("---\nname: Lumen\n---\n", encoding="utf-8")
    (universe / "AGENTS.md").write_text(
        "## Responsibility\nOwn the weekly report.\n## Other\nNot the responsibility.\n",
        encoding="utf-8",
    )


def test_seeded_universe_gets_one_short_instruction(tmp_path):
    read_operating_instructions(tmp_path)
    note = onboarding_note(tmp_path)
    assert 0 < len(note) <= 500
    for text in ("name", "what I own", "where I learn", "quality bar", "approval",
                 "how often I report", "identity.md", "## Responsibility", "ask_first",
                 "automation", "only my owner"):
        assert text in note
    assert note not in harness_prompt(tmp_path)
    from tinyassets.starter_skills import starter_agent_files
    files = starter_agent_files()
    assert "starter-onboarding" in files["starter/hooks.md"]
    assert "## Responsibility" in files["skills/starter-onboarding/SKILL.md"]


def test_note_disappears_after_files_are_written(tmp_path):
    _onboard(tmp_path)
    assert agent_identity(tmp_path) == ("Lumen", "Own the weekly report.")
    assert onboarding_note(tmp_path) == ""
    assert "## Onboarding" not in harness_prompt(tmp_path)


@pytest.mark.parametrize("filename,body", [
    ("identity.md", "---\nname: ''\n---\nI have no name yet."),
    ("AGENTS.md", "## Responsibility\n  \n## Other\nUnrelated text"),
    ("AGENTS.md", "Seed instructions without a responsibility."),
])
def test_both_name_and_responsibility_are_required(tmp_path, filename, body):
    _onboard(tmp_path)
    (tmp_path / filename).write_text(body, encoding="utf-8")
    assert onboarding_note(tmp_path)


@pytest.mark.parametrize("filename", ["AGENTS.md", "identity.md"])
def test_linked_files_are_absent_never_followed(tmp_path, filename):
    universe = tmp_path / "home"
    universe.mkdir()
    _onboard(universe)
    foreign = tmp_path / "foreign.md"
    (universe / filename).replace(foreign)
    try:
        (universe / filename).symlink_to(foreign)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows requires symlink privilege; exercised by the Linux oracle")
        raise
    assert onboarding_note(universe)
    name, responsibility = agent_identity(universe)
    assert (responsibility if filename == "AGENTS.md" else name) == ""


@pytest.mark.parametrize("filename", ["AGENTS.md", "identity.md"])
def test_oversized_files_are_absent(tmp_path, filename):
    _onboard(tmp_path)
    with (tmp_path / filename).open("a", encoding="utf-8") as handle:
        handle.write("x" * _MAX_ONBOARDING_BYTES)
    assert onboarding_note(tmp_path)


def test_skill_listing_keeps_the_onboarding_note(tmp_path):
    skill = tmp_path / "skills" / "report"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: report\ndescription: Write reports\n---\n", encoding="utf-8",
    )
    assert "Write reports" in harness_prompt(tmp_path)
    assert onboarding_note(tmp_path) not in harness_prompt(tmp_path)
    assert "Read matching skills" in harness_prompt(tmp_path)
    _onboard(tmp_path)
    assert "Write reports" in harness_prompt(tmp_path)
    assert "## Onboarding" not in harness_prompt(tmp_path)


def test_resident_tool_description_budget_still_passes():
    from tests.test_converse_turn_cost import test_engine_tool_description_budget_does_not_grow

    test_engine_tool_description_budget_does_not_grow()


@pytest.mark.parametrize("has_skill", [False, True])
def test_onboarding_and_current_folder_evidence_survive_together(tmp_path, has_skill):
    """D4 and request economy must both survive the shared harness merge."""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "current.md").write_text("current evidence", encoding="utf-8")
    if has_skill:
        skill = tmp_path / "skills" / "report"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: report\ndescription: Write reports\n---\n", encoding="utf-8",
        )
    prompt = harness_prompt(tmp_path)
    assert onboarding_note(tmp_path) not in prompt
    assert "Read matching skills" in prompt
    assert "notes/current.md" in prompt
    assert "## What is in my folder now" in prompt
    assert ("Write reports" if has_skill else "(none yet)") in prompt
    _onboard(tmp_path)
    prompt = harness_prompt(tmp_path)
    assert "## Onboarding" not in prompt
    assert "notes/current.md" in prompt
    assert ("Write reports" if has_skill else "(none yet)") in prompt


def test_no_compute_returns_setup_notice_without_a_reply(tmp_path, monkeypatch):
    from tests.test_converse_handle import _founder_auth
    from tinyassets import universe_intelligence, universe_server
    from tinyassets.api import pending_requests, universe
    from tinyassets.provider_serving_binding import NoServingProvider

    _founder_auth(monkeypatch, base=tmp_path)
    monkeypatch.setattr(universe, "engine_setup_required_payload", lambda *_: None)
    monkeypatch.setattr(pending_requests, "_serving_llm_bound", lambda *_: False)

    def unpowered(*_args, **_kwargs):
        raise NoServingProvider("no serving assignment")

    monkeypatch.setattr(universe_intelligence, "converse", unpowered)
    result = json.loads(universe_server.converse(message="Help me get started", graph_id="u-x"))
    assert result["reason"] == "setup_required"
    assert "reply" not in result
    assert "no model connected" in result["note"]
    assert "nothing ran" in result["note"]
    assert "Waiting on you" in result["note"]

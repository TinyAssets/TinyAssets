"""The product is a person's command center, not a universe (founder, 2026-10-01).

C0 of `openspec/changes/rename-universe-to-command-center`: every word a person
or the agent reads says "command center". Machine names (`universe_id`,
`target="universe"`, `universe_files`, CSS ids, storage) move in later slices:
the public MCP names in C1 (`tests/test_command_center_names.py`), code in C3,
storage in C4. So this guard strips exactly those machine tokens and refuses
any other "universe".
"""

from __future__ import annotations

import asyncio
import html
import pathlib
import re

from tinyassets import engine_mcp_server as engine
from tinyassets import universe_server
from tinyassets.api.prompts import _CONTROL_STATION_PROMPT, _MEET_UNIVERSE_PROMPT
from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

APP = pathlib.Path(__file__).resolve().parents[1] / "tinyassets" / "onboarding" / "app.html"

#: Machine names C0 deliberately leaves alone: identifiers, keys, target values,
#: error codes and the stored actor prefix. Anything else spelled "universe" is copy.
_MACHINE = re.compile(
    r"[\w.-]*universe[\w-]*(?==)|"      # target=universe, universe_id=...
    r"[\w.$-]*universe[\w-]*\(|"        # function calls
    r"\w+_universe\w*|universe_\w+|"    # snake_case names
    r"[\w-]+-universe[\w-]*|universe-[\w-]+|"  # css / kebab ids
    r"[\"'`]universe[\"'`]|``universe``|universe:|<universe>",
    re.IGNORECASE,
)


def _copy_words(text: str) -> list[str]:
    stripped = _MACHINE.sub("", text)
    return [m.group(0) for m in re.finditer(r".{0,40}\buniverses?\b.{0,40}", stripped, re.I)]


def _visible_app_text() -> str:
    page = APP.read_text(encoding="utf-8")
    page = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", " ", page, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def test_the_app_greets_a_new_commander_and_switches_command_centers():
    text = _visible_app_text()
    assert "Welcome, commander." in text
    assert "Your command center is waking up." in text
    assert _copy_words(text) == []


def test_the_app_script_copy_says_command_center():
    """Strings the app's script renders (status lines, errors, labels)."""
    page = APP.read_text(encoding="utf-8")
    scripts = " ".join(re.findall(r"<script\b[^>]*>(.*?)</script>", page, flags=re.S))
    literals = re.findall(r'"((?:[^"\\\n]|\\.)*\s(?:[^"\\\n]|\\.)*)"', scripts)
    # A span with code punctuation is the gap BETWEEN two literals, not one.
    offenders = [s for s in literals if not re.search(r"[(=|&]", s) and _copy_words(s)]
    assert offenders == []


def test_the_served_engine_tools_call_it_your_command_center():
    async def descriptions():
        tools = {tool.name: tool for tool in await engine.mcp.list_tools()}
        return {name: tools[name].description or "" for name in SERVED_ENGINE_MCP_TOOLS}

    for name, text in asyncio.run(descriptions()).items():
        assert re.search(r"universe", text, re.I) is None, name


def test_the_connector_guidance_speaks_of_the_command_center():
    instructions = universe_server.mcp.instructions or ""
    for text in (instructions, _CONTROL_STATION_PROMPT, _MEET_UNIVERSE_PROMPT):
        assert "command center" in text
        assert _copy_words(text) == []


# ---------------------------------------------------------------------------
# Records written before the rename still read the same way
# ---------------------------------------------------------------------------
# C0 changes words people read. Errors and identity text already STORED carry
# the old word, so every reader that classifies stored text matches both.

_PRE_RENAME_FOREIGN_CODE = (
    "Node 'edit' carries source_code this run did not author (caller provenance: "
    "public-foreign). Code runs only in the universe that authored it: remix the "
    "branch into your universe with write_graph (fork_from) so the code is yours."
)


def test_a_stored_pre_rename_foreign_code_failure_still_classifies():
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.runs import _classify_failure

    assert _classify_run_outcome_error(_PRE_RENAME_FOREIGN_CODE)[0] == "node_not_accepted"
    assert _classify_failure(
        {"status": "failed", "error": _PRE_RENAME_FOREIGN_CODE},
    ) == "node_not_accepted"


def test_a_stored_pre_rename_held_authority_error_keeps_its_class():
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.providers.owner_binding import (
        AUTHORITY_HELD_DETAIL,
        LEGACY_AUTHORITY_HELD_DETAIL,
    )

    tail = "a timeout while checking the credential"
    current = _classify_run_outcome_error(AUTHORITY_HELD_DETAIL + tail)
    legacy = _classify_run_outcome_error(LEGACY_AUTHORITY_HELD_DETAIL + tail)
    assert current is not None and legacy is not None
    assert legacy[0] == current[0]


def test_the_old_identity_boilerplate_is_still_not_learned():
    from tinyassets.universe_intelligence import _is_generic_identity_boilerplate

    assert _is_generic_identity_boilerplate("I am a personified universe.")
    assert _is_generic_identity_boilerplate("I am a personified command center.")


def test_a_new_agent_opens_its_first_reply_by_welcoming_its_commander():
    """Seeded operating instructions: the first reply opens "Welcome, commander."."""
    from tinyassets.starter_skills import starter_agent_files

    assert 'my reply opens with "Welcome, commander."' in starter_agent_files()["AGENTS.md"]

"""Real tool-jail edit proof for the newly seeded starter skill."""
from tests import test_universe_tools_jail as jail
from tinyassets.starter_skills import CONNECT_SKILL_PATH
from tinyassets.universe_bundle import seed_okf_bundle
from tinyassets.universe_tools import harness_prompt

pytestmark = jail.pytestmark
world = jail.world


def test_seeded_connect_skill_can_be_read_and_edited_through_agent_tools(world, monkeypatch):
    seed_okf_bundle(world.universe_a)
    engine = jail._engine(monkeypatch, world)
    assert "# Connect anything" in jail._run(engine.read_file(path=CONNECT_SKILL_PATH))
    result = jail._run(engine.edit_file(
        path=CONNECT_SKILL_PATH, old_text="Connect any service, platform or API",
        new_text="My editable connection recipe",
    ))
    assert not result.startswith("error:"), result
    assert "My editable connection recipe" in harness_prompt(world.universe_a)
    assert "My editable connection recipe" in jail._run(engine.read_file(path=CONNECT_SKILL_PATH))

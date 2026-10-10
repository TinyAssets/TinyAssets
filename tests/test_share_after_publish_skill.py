"""Editable starter delivery and a scripted offer through the real agent harness."""
import json

import pytest

from tests import test_agent_node as harness
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_command_center_packages import (
    OWNER,
    UNIVERSE,
    _answer,
    _ask,
    _publish_action,
)
from tinyassets import engine_mcp_server, universe_tools
from tinyassets.starter_skills import SHARE_SKILL_PATH, share_skill
from tinyassets.universe_bundle import seed_okf_bundle
from tinyassets.universe_files import write_universe_file

http_wire = harness.http_wire
work_agent = harness.work_agent
engine = harness.engine


def test_share_skill_is_seeded_indexed_and_never_overwrites_edits(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    home = tmp_path / "new"
    seed_okf_bundle(home)
    assert (home / SHARE_SKILL_PATH).read_text(encoding="utf-8") == share_skill()
    assert "share-after-publish" in dict(universe_tools.skill_index(home))
    # The index names the skill; the prompt states where a named skill lives.
    prompt = universe_tools.harness_prompt(home)
    assert "`share-after-publish`" in prompt and "skills/<name>/SKILL.md" in prompt
    write_universe_file(home, SHARE_SKILL_PATH, b"My own sharing recipe")
    seed_okf_bundle(home)
    assert (home / SHARE_SKILL_PATH).read_text(encoding="utf-8") == "My own sharing recipe"


def test_existing_owner_can_copy_or_delete_share_skill(tmp_path):
    chapter = engine_mcp_server._handbook_read("write_graph.share-after-publish")
    fetched = json.loads(chapter)["text"]
    assert fetched == share_skill()
    assert universe_tools.skill_index(tmp_path) == []
    write_universe_file(tmp_path, SHARE_SKILL_PATH, fetched.encode())
    assert "share-after-publish" in dict(universe_tools.skill_index(tmp_path))
    (tmp_path / SHARE_SKILL_PATH).unlink()
    engine_mcp_server._handbook_read("write_graph.share-after-publish")
    assert universe_tools.skill_index(tmp_path) == []
    assert "`share-after-publish`" not in universe_tools.harness_prompt(tmp_path)


@pytest.mark.usefixtures("cloud_runtime")
def test_scripted_turn_offers_a_public_post_and_picture_without_posting(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    from tests.test_automations import _seed_branch
    from tests.test_in_platform_agent_systems import SCOUT, SCRIBE, _library
    from tests.test_ui_preview import _png
    from tinyassets import ui_preview
    from tinyassets.runs import get_run

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ui_preview, "_render_spec", lambda *_: {"png": _png()})
    _seed_branch(tmp_path, branch_def_id=SCOUT, visibility="private")
    _seed_branch(tmp_path, branch_def_id=SCRIBE, visibility="private")
    _library(tmp_path)
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    from tests.test_publication_completion import wait_for_preview

    done = wait_for_preview(tmp_path, done)
    receipt = done["completion"]
    offered = (
        "Your command center is published. Would you like to share it on a connected platform? "
        "Suggested post: 'I just published GTM Village, a village of agents.' "
        f"I can include this preview: {receipt['preview_image_path']}. "
        "If nothing suitable is connected, you can connect a platform. "
        "Please approve the destination, text and picture before I post."
    )
    engine.script = [
        [harness._call("read_graph", target="handbook", query="write_graph.share-after-publish")],
        [harness._call("read_graph", target="pending_requests")],
        offered,
    ]
    posted = []

    def external_post(**kwargs):
        posted.append(kwargs)
        raise AssertionError("An offer must never execute a post")

    monkeypatch.setattr(
        "tinyassets.effectors.authenticated_external_call.run_authenticated_external_call_effector",
        external_post,
    )
    result = harness._run(tmp_path, monkeypatch, authenticate_request, ["agent"])
    assert result["terminal_status"] == "completed", (result, engine.errors)
    assert get_run(tmp_path, result["run_id"])["output"]["ledger"] == offered
    assert any(receipt["listing_id"] in (text or "") for text in engine.results)
    assert any("Never post without" in (text or "") for text in engine.results)
    assert posted == []
    assert engine.script == []

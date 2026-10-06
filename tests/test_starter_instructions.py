"""Prepared cutover reader: preserve owner choices and never follow links."""
import pytest

from tinyassets.starter_instructions import read_instruction_files
from tinyassets.starter_skills import starter_agent_files
from tinyassets.universe_tools import skill_index


def test_published_bundle_uses_existing_skill_index(tmp_path):
    files = starter_agent_files()
    assert len(files) == 7
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    assert len(skill_index(tmp_path)) == 5
    assert len(files["starter/hooks.md"]) + len(files["AGENTS.md"]) < 1600
    assert "persist it" in files["starter/hooks.md"]
    assert "persist it" not in files["AGENTS.md"]


@pytest.mark.parametrize("owner", [None, "", "Follow my custom policy.\n"])
def test_hooks_load_independently_without_mutating_owner(tmp_path, owner):
    (tmp_path / "starter").mkdir()
    (tmp_path / "starter/hooks.md").write_text("Editable memory hook.", encoding="utf-8")
    agents = tmp_path / "AGENTS.md"
    if owner is not None:
        agents.write_text(owner, encoding="utf-8")
    result = read_instruction_files(tmp_path)
    assert result.blocks[0] == ("starter/hooks.md", "Editable memory hook.")
    assert not result.notices
    if owner is None:
        assert not agents.exists()
        assert len(result.blocks) == 1
    else:
        assert result.blocks[1] == ("AGENTS.md", owner)
        assert agents.read_text(encoding="utf-8") == owner


def test_hook_edits_emptying_deletion_and_selected_root(tmp_path):
    (tmp_path / "starter").mkdir()
    hooks = tmp_path / "starter/hooks.md"
    for body in ("First.", "Owner edit.", ""):
        hooks.write_text(body, encoding="utf-8")
        assert read_instruction_files(tmp_path).blocks == (("starter/hooks.md", body),)
    hooks.unlink()
    assert read_instruction_files(tmp_path).blocks == ()
    assert not hooks.exists()
    hooks.write_text("Old main guidance.", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    assert read_instruction_files(replacement).blocks == ()


@pytest.mark.parametrize("linked", ["AGENTS.md", "starter", "starter/hooks.md"])
def test_link_refusal_is_visible_without_disclosing_target(tmp_path, linked):
    root = tmp_path / "center"
    root.mkdir()
    external = tmp_path / "private"
    external.mkdir()
    (external / "hooks.md").write_text("OTHER OWNER SECRET", encoding="utf-8")
    target = external if linked == "starter" else external / "hooks.md"
    link = root / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=linked == "starter")
    result = read_instruction_files(root)
    assert not result.blocks
    assert len(result.notices) == 1
    assert "Could not read" in result.render()
    assert "OTHER OWNER SECRET" not in result.render()
    assert link.is_symlink()


@pytest.mark.parametrize("failure", [PermissionError("private path"), UnicodeError("bad bytes")])
def test_failed_owner_read_keeps_hooks_and_reports_failure(tmp_path, monkeypatch, failure):
    def read(root, path, **kwargs):
        if path == "AGENTS.md":
            raise failure
        return "owner-editable hook"

    monkeypatch.setattr("tinyassets.starter_instructions.read_universe_text", read)
    result = read_instruction_files(tmp_path)
    assert result.blocks == (("starter/hooks.md", "owner-editable hook"),)
    assert result.notices == ("Could not read AGENTS.md; no substitute instructions loaded.",)
    assert not (tmp_path / "AGENTS.md").exists()

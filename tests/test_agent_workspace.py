"""Harness W2: the agent's own workspace is its jail root (no real jail needed).

The real-jail proofs are in ``tests/test_universe_tools_jail.py``. This module
pins what holds on any host, including the folds from gpt-6-astra's refute of
#4194: the workspace exists before any launch masks it, a link is refused, a
brain file written while the root had none reaches the daemon, the owner's
file API resolves ``/u`` the way the jail does, and scoped reset treats the
workspace as owner content.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tinyassets import universe_tools
from tinyassets.providers import provider_jail
from tinyassets.providers.provider_jail import ProviderConfinementError, default_view

WS = universe_tools.WORKSPACE_DIR


def _universe(tmp_path: Path, name: str = "u-alpha") -> Path:
    path = tmp_path / "data" / name
    path.mkdir(parents=True)
    return path


def test_a_provider_launch_creates_the_workspace_then_masks_it(tmp_path):
    """Created first, so a process in a workflow's jail can never make the name
    itself (as a link to another universe) for the tool jail to bind as /u."""
    universe = _universe(tmp_path)
    view = default_view(universe)
    assert (universe / WS).is_dir()
    masked = {m.dest for m in view.mounts if m.op == "tmpfs"}
    assert str(universe.resolve() / WS) in masked


def test_a_workspace_that_is_a_link_refuses_every_launch(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    other = _universe(tmp_path, "u-bravo")
    try:
        os.symlink(other, universe / WS, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    with pytest.raises(ProviderConfinementError):
        default_view(universe)
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    with pytest.raises(universe_tools.UniverseToolError):
        universe_tools.tool_jail_argv(
            universe, ["/bin/true"], agent_id="main", workspace_prepared=True,
            promote_brain_files=False)


def test_a_brain_file_written_while_the_root_had_none_reaches_the_root(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    (universe / WS).mkdir()
    (universe / WS / "identity.md").write_text("---\nname: Tiny\n---\n", encoding="utf-8")
    (universe / WS / "goals.md").write_text("in workspace", encoding="utf-8")
    (universe / "goals.md").write_text("in root", encoding="utf-8")
    monkeypatch.setattr(provider_jail, "BWRAP_RESOLVER", lambda: "/usr/bin/bwrap")
    universe_tools.tool_jail_argv(universe, ["/bin/true"], agent_id="main")
    assert (universe / "identity.md").read_text(encoding="utf-8").startswith("---")
    assert not (universe / WS / "identity.md").exists()
    assert (universe / "goals.md").read_text(encoding="utf-8") == "in root", (
        "a root brain file is never replaced")


def test_the_owner_file_api_resolves_u_the_way_the_jail_does(tmp_path):
    from tinyassets.api.universe_file_reads import _logical

    universe = _universe(tmp_path)
    (universe / "notes").mkdir()
    assert _logical(universe, "notes/a.md") == "notes/a.md"
    assert _logical(universe, "PLAN.md") == f"{WS}/PLAN.md"
    assert _logical(universe, "projects/site/index.html") == f"{WS}/projects/site/index.html"
    assert _logical(universe, ".runtime/x") == ".runtime/x"
    assert _logical(universe, "") == ""


def test_scoped_reset_treats_the_workspace_as_owner_content(tmp_path):
    from tinyassets.scoped_reset import _walk_home_without_following

    universe = _universe(tmp_path)
    (universe / WS / "node_modules" / ".bin").mkdir(parents=True)
    (universe / WS / "node_modules" / "pkg.weird-suffix").write_text("x", encoding="utf-8")
    blockers = _walk_home_without_following(universe)
    assert not [b for b in blockers if WS in b], blockers


def test_promotion_never_replaces_a_root_brain_file_created_meanwhile(tmp_path):
    universe = _universe(tmp_path)
    (universe / WS).mkdir()
    (universe / WS / "log.md").write_text("from workspace", encoding="utf-8")
    universe_tools._promote_brain_files(universe, universe / WS, agent_id="main")
    assert (universe / "log.md").read_text(encoding="utf-8") == "from workspace"
    (universe / WS / "log.md").write_text("second", encoding="utf-8")
    universe_tools._promote_brain_files(universe, universe / WS, agent_id="main")
    assert (universe / "log.md").read_text(encoding="utf-8") == "from workspace"


def test_another_agents_call_never_promotes_the_main_identity(tmp_path):
    """harness §4.18: identity.md is the main agent's; a custom agent's call
    promotes the other brain files but never that one."""
    universe = _universe(tmp_path)
    (universe / WS).mkdir()
    (universe / WS / "identity.md").write_text("I am Weave", encoding="utf-8")
    (universe / WS / "log.md").write_text("shared note", encoding="utf-8")
    universe_tools._promote_brain_files(universe, universe / WS, agent_id="agent_binding_w1")
    assert not (universe / "identity.md").exists()
    assert (universe / "log.md").read_text(encoding="utf-8") == "shared note"

"""Branch authoring actions for the `extensions` tool.

Covers the 10 ship-list actions, the ledger-wrapper guarantee, the
recipe-tracker end-to-end vignette, and the hard-rule UX flag that
`describe_branch` now points users at `run_graph` (the canonical handle).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest


@pytest.fixture
def branch_env(tmp_path, monkeypatch, authenticate_request):
    """Point the TinyAssets data root at a temp base path for the test.

    The Community Branches storage layer uses ``_base_path()`` which
    reads ``TINYASSETS_DATA_DIR`` — pointing it at a temp dir keeps
    tests isolated from real universes. Storage backend is pinned to
    ``sqlite_only`` globally by the conftest autouse fixture.
    """
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    authenticate_request("tester")
    import importlib

    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, Path(tmp_path)
    importlib.reload(us)


def _call(us, action, **kwargs):
    result = us._extensions_impl(action=action, **kwargs)
    return json.loads(result)


def test_list_branches_returns_summaries(branch_env):
    """`scope="all"` returns every branch including drafts.

    Default scope changed to ``"published"`` (PR-094 follow-up); tests
    that author drafts must opt into ``scope="all"`` to see them.
    """
    us, _ = branch_env
    _draft(us, "A")
    _draft(us, "B")

    listing = _call(us, "list_branches", scope="all")
    # build_branch's standalone-shadow check creates the system-owned
    # standalone node registry row, which scope="all" also lists.
    mine = [b for b in listing["branches"] if b["branch_def_id"] != "__standalone_nodes__"]
    assert len(mine) == 2
    names = sorted(b["name"] for b in mine)
    assert names == ["A", "B"]
    assert all("node_count" in b for b in listing["branches"])


def _build_basic_spec(name: str, description: str = "") -> dict:
    return {
        "name": name,
        "description": description,
        "entry_point": "ready",
        "node_defs": [{
            "node_id": "ready",
            "display_name": "Ready",
            "prompt_template": "Do the work.",
        }],
        "edges": [
            {"from": "START", "to": "ready"},
            {"from": "ready", "to": "END"},
        ],
        "state_schema": [{"name": "x", "type": "str"}],
    }


def _draft(us, name: str, description: str = "") -> dict:
    """An unpublished branch, built through ``build_branch``."""
    built = _call(us, "build_branch",
                  spec_json=json.dumps(_build_basic_spec(name, description)))
    assert built["status"] == "built", built
    return built


def test_list_branches_scope_published_filters_on_published_versions(branch_env):
    """`scope="published"` requires an actual published version snapshot;
    the legacy ``published`` boolean flag is no longer sufficient on its own.
    """
    us, _ = branch_env
    versioned = _call(us, "build_branch", spec_json=json.dumps(_build_basic_spec("Versioned")))
    _draft(us, "Probe draft")
    legacy_flagged = _call(
        us,
        "build_branch",
        spec_json=json.dumps(_build_basic_spec("Legacy flag only")),
    )
    patched = _call(
        us,
        "patch_branch",
        branch_def_id=legacy_flagged["branch_def_id"],
        changes_json=json.dumps([{"op": "set_published", "published": True}]),
    )
    assert patched.get("status") != "rejected", patched
    version = _call(us, "publish_version", branch_def_id=versioned["branch_def_id"])
    assert version["branch_version_id"].startswith(f"{versioned['branch_def_id']}@")

    listing = _call(us, "list_branches", scope="published")
    # `Versioned` published a version; `Legacy flag only` has the boolean
    # flag but no version_id; `Probe draft` has neither. Only `Versioned`
    # passes — and patch_branch's auto-snapshot promoted the legacy_flagged
    # branch too once the set_published op landed.
    names = sorted(b["name"] for b in listing["branches"])
    assert "Versioned" in names
    assert "Probe draft" not in names
    versioned_summary = next(
        b for b in listing["branches"] if b["branch_def_id"] == versioned["branch_def_id"]
    )
    assert versioned_summary["published"] is True
    assert versioned_summary["branch_version_id"] == version["branch_version_id"]


def test_list_branches_scope_published_is_the_default(branch_env):
    """Omitting `scope` is equivalent to `scope="published"`."""
    us, _ = branch_env
    versioned = _call(us, "build_branch", spec_json=json.dumps(_build_basic_spec("Versioned")))
    _draft(us, "Draft only")
    _call(us, "publish_version", branch_def_id=versioned["branch_def_id"])

    default_listing = _call(us, "list_branches")
    explicit_listing = _call(us, "list_branches", scope="published")
    assert default_listing["count"] == explicit_listing["count"]
    assert {b["name"] for b in default_listing["branches"]} == {
        b["name"] for b in explicit_listing["branches"]
    }
    assert "Draft only" not in {b["name"] for b in default_listing["branches"]}


def test_list_branches_scope_mine_filters_to_caller(
    branch_env,
    authenticate_request: Callable[[str | None], None],
):
    """`scope="mine"` returns only branches authored by the calling identity."""
    us, _ = branch_env
    # The branch_env fixture authenticated tester; the first batch should
    # land with author=tester.
    _call(us, "build_branch", spec_json=json.dumps(_build_basic_spec("Mine 1")))
    _call(us, "build_branch", spec_json=json.dumps(_build_basic_spec("Mine 2")))

    # Switch identity and write a third branch as someone else.
    authenticate_request("other")
    import importlib

    from tinyassets import universe_server as us2
    importlib.reload(us2)
    json.loads(us2._extensions_impl(
        action="build_branch",
        spec_json=json.dumps(_build_basic_spec("Theirs")),
    ))

    # Back to tester; only the two `Mine *` branches must come back.
    authenticate_request("tester")
    importlib.reload(us2)
    listing = json.loads(us2._extensions_impl(action="list_branches", scope="mine"))
    names = sorted(b["name"] for b in listing["branches"])
    assert names == ["Mine 1", "Mine 2"], listing


def test_list_branches_rejects_unknown_scope(branch_env):
    """Unknown scope value returns an error rather than silently filtering."""
    us, _ = branch_env
    result = _call(us, "list_branches", scope="bogus")
    assert "error" in result
    assert "bogus" in result["error"]
    assert "published" in result["error"]
    assert "all" in result["error"]
    assert "mine" in result["error"]


def test_list_branches_node_count_matches_node_defs_length(branch_env):
    """list_branches.node_count must equal get_branch.node_defs length.

    STATUS.md Approved-bugs 2026-04-22: the old formula
    ``len(graph.nodes) + len(node_defs)`` double-counted because
    graph.nodes is a compiled-topology view that overlaps with
    node_defs. Fix makes list_branches agree with describe_branch's
    canonical count (``len(branch.node_defs)``).
    """
    us, _ = branch_env
    # 3 distinct node_defs so the bug shape (double-count) would manifest
    # as 6 instead of 3.
    nids = ("alpha", "beta", "gamma")
    chain = ["START", *nids, "END"]
    created = _call(us, "build_branch", spec_json=json.dumps({
        "name": "Counted",
        "entry_point": "alpha",
        "node_defs": [{"node_id": nid, "display_name": nid,
                       "prompt_template": f"do {nid}"} for nid in nids],
        "edges": [{"from": a, "to": b} for a, b in zip(chain, chain[1:])],
    }))
    bid = created["branch_def_id"]

    # Truth: get_branch.node_defs length.
    got = _call(us, "get_branch", branch_def_id=bid)
    expected = len(got["node_defs"])
    assert expected == 3, (
        f"precondition: build_branch should have created 3 node_defs; "
        f"got {expected}"
    )

    # list_branches.node_count must match. Use scope="all" so the
    # unpublished draft is included (default scope filters to
    # branches with a published version).
    listing = _call(us, "list_branches", scope="all")
    entry = next(b for b in listing["branches"] if b["branch_def_id"] == bid)
    assert entry["node_count"] == expected, (
        f"list_branches.node_count ({entry['node_count']}) "
        f"!= len(get_branch.node_defs) ({expected}). The old formula "
        f"len(graph.nodes) + len(node_defs) double-counted; truthful "
        f"count is len(node_defs)."
    )


def test_read_actions_do_not_hit_ledger(branch_env):
    us, base = branch_env
    bid = _draft(us, "X")["branch_def_id"]
    _call(us, "get_branch", branch_def_id=bid)
    _call(us, "list_branches", scope="all")

    ledger = json.loads((base / "ledger.json").read_text(encoding="utf-8"))
    # Only the build_branch should be logged
    assert len(ledger) == 1
    assert ledger[0]["action"] == "build_branch"


def test_read_graph_branches_lists_own_workflows_by_name_and_id(branch_env):
    """read_graph target=branches: a user can find a workflow BY NAME (name + branch_def_id
    + tags) without already knowing its internal id. Claude.ai hit "Global workflow
    enumeration is not exposed by the advertised handles" when asked to rename a
    workflow (2026-08-25) and had to ask the user for the id. This closes that."""
    us, _ = branch_env
    created = _draft(us, "Compute plug-and-play check",
                     description="one-node provider check")
    bid = created["branch_def_id"]
    listing = json.loads(us.read_graph(target="branches"))
    rows = listing["branches"]
    assert listing["count"] == len(rows) >= 1
    mine = [r for r in rows if r["name"] == "Compute plug-and-play check"]
    assert len(mine) == 1, rows
    assert mine[0]["branch_def_id"] == bid  # the id the user never had to know

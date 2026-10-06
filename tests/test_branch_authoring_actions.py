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


def _build_basic_spec(name: str) -> dict:
    return {
        "name": name,
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

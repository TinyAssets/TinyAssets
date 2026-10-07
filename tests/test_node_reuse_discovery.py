"""#62 Part B: cross-branch + cross-Goal node reuse discovery MCP surface.

Pairs with #62 Part A (daemon_server helpers) and #66 (node_ref /
intent primitives). Part A gave us the query helpers; Part B wires
them to MCP so Claude.ai can actually reach them.

What we pin:
- `extensions action=search_nodes` returns reuse candidates with a
  phone-card text layout, and each hit carries the branch_def_id
  needed for a subsequent `node_ref` copy.
- `goals action=common_nodes scope=all` aggregates across every Goal
  + unbound Branches; `scope=this_goal` (default) still works and
  requires goal_id.
- The control_station prompt + branch_design_guide carry the
  "search before invent" nudge so the bot steers toward reuse.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def ext_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
            authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    # Branch mutation requires a credential-derived subject, and the scope
    # check is per-family: this file drives `goals` as well as `extensions`.
    # Nothing here asserts a *scope* refusal, so granting the writes these
    # tests perform costs no assertion strength. `extensions.costly` and
    # `goals.costly` stay withheld.
    authenticate_request("tester", capabilities=[
        "tinyassets.extensions.read",
        "tinyassets.extensions.write",
        "tinyassets.extensions.admin",
        "tinyassets.goals.read",
        "tinyassets.goals.write",
    ])
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, base
    importlib.reload(us)


# ─────────────────────────────────────────────────────────────────────────────
# extensions action=search_nodes
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# goals action=common_nodes scope=all
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Reuse-nudge prompt language
# ─────────────────────────────────────────────────────────────────────────────


class TestReusePromptNudges:

    def test_control_station_has_search_before_invent_rule(self, ext_env):
        us, _ = ext_env
        text = us._CONTROL_STATION_PROMPT.lower()
        assert "reuse before invent" in text or "before you invent" in text
        assert 'read_graph target="branch"' in text
        assert "global node search" in text
        assert "not exposed" in text
        assert "node_ref" in text

    def test_branch_design_guide_points_at_search_first(self, ext_env):
        from tinyassets.api.branches import _BRANCH_DESIGN_GUIDE
        text = _BRANCH_DESIGN_GUIDE.lower()
        assert 'read_graph target="branch"' in text
        assert "node_ref" in text
        # The guide must position search BEFORE the author flow.
        search_idx = text.index("before you invent")
        # Heading renamed by 46ff5c5c ("reconcile control station with
        # canonical MCP handles") — it dropped the word "gap" once authoring
        # via `write_graph target="branch"` was actually documented. The
        # ORDERING invariant this test exists for is unchanged.
        author_idx = text.index("new-workflow authoring")
        assert search_idx < author_idx, (
            "search-before-invent nudge must appear before the author flow"
        )

    def test_branch_design_guide_authoring_uses_canonical_handles(self, ext_env):
        """The guide documents authoring via the CANONICAL handle.

        Renamed from `..._reports_unsupported_new_authoring`, because that
        test's premise inverted. It asserted the guide says TinyAssets "does
        not currently expose creation of a new branch" and steers users to
        "GitHub Actions YAML". Commit 46ff5c5c ("reconcile control station
        with canonical MCP handles") deliberately removed both: authoring IS
        exposed now, through `write_graph target="branch"`.

        Asserting the GitHub-Actions phrasing is ABSENT is the useful
        direction — it pins the reconciliation, which hard rule 11 exists to
        protect, instead of pinning wording that was intentionally retired.
        """
        from tinyassets.api.branches import _BRANCH_DESIGN_GUIDE
        text = _BRANCH_DESIGN_GUIDE
        flat = " ".join(text.split())
        assert 'write_graph target="branch"' in text, (
            "the guide must point authoring at the canonical handle"
        )
        assert "GitHub Actions" not in flat, (
            "46ff5c5c removed the GitHub-Actions steer; its return would mean "
            "the control station drifted off the canonical handles again"
        )
        assert "does not currently expose creation of a new branch" not in text, (
            "authoring is exposed now; this disclaimer would contradict the "
            "New-workflow authoring section"
        )
        assert "New-branch creation is not exposed" not in flat
        assert "set_io_manifest" in text
        assert '"io_type":"file_bundle"' in text
        assert "Explicit null clears" in text
        assert "runs keep their old contract" in text

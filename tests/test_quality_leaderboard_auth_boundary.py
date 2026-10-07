"""PR-123 round-2 auth-boundary regression guards.

Codex's round-1 review on PR #970 caught two real P1 issues:

  P1.1 — Caller-controlled visibility leak. ``extensions.py`` mapped
         caller-supplied ``author`` to ``viewer`` and ``force`` to
         ``include_private``. A chatbot calling
         ``extensions action=quality_leaderboard author=alice`` saw
         alice's private branches; ``force=true`` returned every row.

  P1.2 — Fork-count signal leaked private descendant existence. The
         leaderboard hid private fork rows from the entry list but
         the public parent's ``fork_count`` aggregate still counted
         them, so the third-party viewer could infer the private
         fork's existence.

These tests lock in the fix and serve as the regression gate for any
future signal that aggregates over rows.

Threat model:
  * actor_alice  — author of one private branch ``priv-alice``.
  * actor_bob    — author of one public branch ``pub-bob`` plus one
                   private fork ``priv-bob-fork`` of a public peer.
  * actor_eve    — third party. Default test caller — should NEVER
                   see alice's or bob's private rows.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_selector_passthrough(monkeypatch):
    """DESIGN-008 — pass-through selector mock for auth-boundary tests.

    The auth-boundary tests in this file probe the substrate's
    visibility filter at the leaderboard layer. Under DESIGN-008,
    leaderboard production requires a selector dispatch (one LLM
    call). These tests don't need to exercise selector behavior —
    only the candidate-set visibility filter. So we monkeypatch
    ``dispatch_selector`` to pass the candidate list straight
    through as ``ranked_entries`` (preserving input order, score
    0.0). The substrate's filter (already applied via
    ``list_branch_definitions`` + ``_fork_count`` before dispatch)
    is what's under test.
    """
    def _passthrough(
        base_path,
        *,
        goal_id,
        candidate_branches,
        actor="anonymous",
        timeout_s=None,
        **_extra,
    ):
        return {
            "ok": True,
            "branch_version_id": "mock_selector@authtest",
            "source": "platform_default",
            "run_id": "mock-run",
            "ranked_entries": [
                {
                    "branch_def_id": c["branch_def_id"],
                    "branch_version_id": c.get("branch_version_id", ""),
                    "score": 0.0,
                    "rationale": "passthrough",
                }
                for c in candidate_branches
            ],
        }
    monkeypatch.setattr(
        "tinyassets.api.quality_leaderboard.dispatch_selector",
        _passthrough,
    )


# ---------------------------------------------------------------------------
# P1.1 — caller cannot impersonate another viewer
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P1.2 — fork_count must respect viewer visibility
# ---------------------------------------------------------------------------


def test_fork_count_unit_at_storage_layer_respects_viewer(tmp_path):
    """Direct unit on ``_fork_count`` so the regression can't slip
    through if the dispatch wiring shifts."""
    from tinyassets.api.quality_leaderboard import _fork_count
    from tinyassets.daemon_server import (
        initialize_author_server,
        save_branch_definition,
        save_goal,
    )
    initialize_author_server(tmp_path)
    save_goal(
        tmp_path,
        goal=dict(goal_id="g", name="g", author="h", tags=[],
                  visibility="public"),
    )
    save_branch_definition(
        tmp_path,
        branch_def=dict(
            branch_def_id="parent",
            name="P", description="", author="bob", tags=[],
            graph_nodes=[], edges=[], state_schema=[], entry_point="",
            published=True, goal_id="g", visibility="public",
        ),
    )
    save_branch_definition(
        tmp_path,
        branch_def=dict(
            branch_def_id="pub-fork",
            name="PF", description="", author="bob", tags=[],
            graph_nodes=[], edges=[], state_schema=[], entry_point="",
            published=True, goal_id="g", visibility="public",
            parent_def_id="parent",
        ),
    )
    save_branch_definition(
        tmp_path,
        branch_def=dict(
            branch_def_id="priv-fork",
            name="PrivF", description="", author="alice", tags=[],
            graph_nodes=[], edges=[], state_schema=[], entry_point="",
            published=True, goal_id="g", visibility="private",
            parent_def_id="parent",
        ),
    )
    # Third-party viewer.
    assert _fork_count(tmp_path, "parent", viewer="eve") == 1
    # Owner.
    assert _fork_count(tmp_path, "parent", viewer="alice") == 2
    # No viewer (strictly public).
    assert _fork_count(tmp_path, "parent", viewer="") == 1
    # Public via fork_from column.
    save_branch_definition(
        tmp_path,
        branch_def=dict(
            branch_def_id="forkfrom-pub",
            name="FF", description="", author="charlie", tags=[],
            graph_nodes=[], edges=[], state_schema=[], entry_point="",
            published=True, goal_id="g", visibility="public",
            fork_from="parent",
        ),
    )
    assert _fork_count(tmp_path, "parent", viewer="eve") == 2


# ---------------------------------------------------------------------------
# Public-API signature lock: include_private is no longer a kwarg
# ---------------------------------------------------------------------------


def test_build_quality_leaderboard_signature_has_no_include_private():
    """Lock the public API surface: ``include_private`` is REMOVED
    from the public entry point so a future refactor can't reintroduce
    a caller-controllable visibility knob without a test failure."""
    import inspect

    from tinyassets.api.quality_leaderboard import build_quality_leaderboard
    sig = inspect.signature(build_quality_leaderboard)
    assert "include_private" not in sig.parameters
    # ``viewer`` is required (no default) so callers cannot accidentally
    # invoke the public path without explicitly committing to a viewer
    # identity. The MCP handler always supplies ``_current_actor()``.
    assert sig.parameters["viewer"].default is inspect.Parameter.empty


def test_recommend_parent_for_fork_signature_has_no_include_private():
    import inspect

    from tinyassets.api.quality_leaderboard import recommend_parent_for_fork
    sig = inspect.signature(recommend_parent_for_fork)
    assert "include_private" not in sig.parameters
    assert sig.parameters["viewer"].default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# Empty current_actor falls back to strictly-public, not "expose all"
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Time-domain: ensure visibility test doesn't depend on recency decay
# ---------------------------------------------------------------------------

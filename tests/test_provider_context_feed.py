from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "provider_context_feed.py"
SPEC = importlib.util.spec_from_file_location("provider_context_feed", SCRIPT)
assert SPEC is not None
provider_context_feed = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = provider_context_feed
SPEC.loader.exec_module(provider_context_feed)



def test_collects_claude_memory_and_shared_ideas(tmp_path: Path) -> None:
    memory = tmp_path / ".claude" / "agent-memory" / "navigator" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text(
        "Next action: carry provider memory refs into the lane.\n"
        "Worktree lanes fold back through PR review.\n",
        encoding="utf-8",
    )
    ideas = tmp_path / "ideas" / "INBOX.md"
    ideas.parent.mkdir()
    ideas.write_text("- Idea: keep loose ideas as feed refs, not build authority.\n")

    candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="all",
        phase="plan",
        limit=None,
    )

    assert any(item.provider == "claude" for item in candidates)
    assert any(item.source_type == "idea-feed" for item in candidates)
    assert any(item.signal == "coordination" for item in candidates)


def test_provider_filter_keeps_shared_context(tmp_path: Path) -> None:
    cursor_rule = tmp_path / ".cursor" / "rules" / "tinyassets.mdc"
    cursor_rule.parent.mkdir(parents=True)
    cursor_rule.write_text("TODO: route Cursor memories into STATUS.md lanes.\n")
    codex_config = tmp_path / ".codex" / "config.toml"
    codex_config.parent.mkdir()
    codex_config.write_text("# TODO: codex-only note\n", encoding="utf-8")
    ideas = tmp_path / "ideas" / "PIPELINE.md"
    ideas.parent.mkdir()
    ideas.write_text("Pending promotion: review gate should fold into PR.\n")

    candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="cursor",
        phase="claim",
        limit=None,
    )

    assert any(item.provider == "cursor" for item in candidates)
    assert any(item.provider == "shared" for item in candidates)
    assert not any(item.provider == "codex" for item in candidates)


def test_provider_alias_maps_to_family(tmp_path: Path) -> None:
    codex_config = tmp_path / ".codex" / "config.toml"
    codex_config.parent.mkdir()
    codex_config.write_text("# TODO: codex desktop claim context\n", encoding="utf-8")
    claude_memory = tmp_path / ".claude" / "agent-memory" / "dev" / "MEMORY.md"
    claude_memory.parent.mkdir(parents=True)
    claude_memory.write_text("TODO: claude-only memory\n", encoding="utf-8")
    ideas = tmp_path / "ideas" / "PIPELINE.md"
    ideas.parent.mkdir()
    ideas.write_text("Pending promotion: shared worktree review gate.\n", encoding="utf-8")

    candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="codex-gpt5-desktop",
        phase="claim",
        limit=None,
    )

    assert any(item.provider == "codex" for item in candidates)
    assert any(item.provider == "shared" for item in candidates)
    assert any(item.provider == "claude" for item in candidates)
    assert any(
        item.provider == "claude" and item.source_type == "provider-memory"
        for item in candidates
    )


def test_other_provider_memory_is_claim_only(tmp_path: Path) -> None:
    codex_config = tmp_path / ".codex" / "config.toml"
    codex_config.parent.mkdir()
    codex_config.write_text("# TODO: codex desktop claim context\n", encoding="utf-8")
    claude_memory = tmp_path / ".claude" / "agent-memory" / "dev" / "MEMORY.md"
    claude_memory.parent.mkdir(parents=True)
    claude_memory.write_text("TODO: claude-authored prior context\n", encoding="utf-8")

    claim_candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="codex-gpt5-desktop",
        phase="claim",
        limit=None,
    )
    build_candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="codex-gpt5-desktop",
        phase="build",
        limit=None,
    )

    assert any(item.provider == "claude" for item in claim_candidates)
    assert not any(item.provider == "claude" for item in build_candidates)


def test_active_pipeline_ranks_before_old_activity_log(tmp_path: Path) -> None:
    activity = tmp_path / ".agents" / "activity.log"
    activity.parent.mkdir()
    activity.write_text("TODO: old shared activity item.\n", encoding="utf-8")
    pipeline = tmp_path / "ideas" / "PIPELINE.md"
    pipeline.parent.mkdir()
    pipeline.write_text("Pending promotion: current PR review gate.\n", encoding="utf-8")

    candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="all",
        phase="claim",
        limit=1,
    )

    assert candidates[0].source_type == "idea-pipeline"


def test_short_feed_caps_each_file_so_other_sources_surface(tmp_path: Path) -> None:
    first = tmp_path / "ideas" / "PIPELINE.md"
    first.parent.mkdir()
    first.write_text(
        "\n".join(f"Pending promotion {index}: review gate." for index in range(8)),
        encoding="utf-8",
    )
    second = tmp_path / "docs" / "concerns" / "review.md"
    second.parent.mkdir(parents=True)
    second.write_text("Needs follow-up: compare implications before build.\n", encoding="utf-8")

    candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="all",
        phase="claim",
        limit=5,
    )

    assert sum(item.path == "ideas/PIPELINE.md" for item in candidates) == 4
    assert any(item.path == "docs/concerns/review.md" for item in candidates)


def test_short_feed_caps_each_source_type_so_exec_plans_do_not_saturate(tmp_path: Path) -> None:
    exec_root = tmp_path / "openspec" / "changes"
    exec_root.mkdir(parents=True)
    for index in range(14):
        (exec_root / f"plan-{index}.md").write_text(
            "Pending task: fold branch memory into PR.\n",
            encoding="utf-8",
        )
    memory = tmp_path / ".claude" / "agent-memory" / "navigator" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Remember: dirty checkout must not switch to main.\n", encoding="utf-8")

    candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="all",
        phase="claim",
        limit=15,
    )

    assert sum(item.source_type == "exec-plan" for item in candidates) == 10
    assert any(item.source_type == "provider-memory" for item in candidates)


def test_brain_pages_are_shared_context_sources(tmp_path: Path) -> None:
    concept = tmp_path / "pages" / "concepts" / "skill-sync.md"
    concept.parent.mkdir(parents=True)
    concept.write_text(
        "Next action: sync project skills through brain pages before runtime pickup.\n",
        encoding="utf-8",
    )
    plan = tmp_path / "pages" / "plans" / "brain-module.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "Proposal: branch brain pages into accepted skill records.\n",
        encoding="utf-8",
    )

    candidates = provider_context_feed.collect_candidates(
        tmp_path,
        provider="codex",
        phase="plan",
        limit=None,
    )

    assert any(item.source_type == "brain-concept" for item in candidates)
    assert any(item.source_type == "brain-plan" for item in candidates)


def test_absolute_worktree_purpose_directory_is_safe(tmp_path: Path) -> None:
    root = tmp_path / "TinyAssets"
    root.mkdir()
    worktree = tmp_path / "wf-demo"
    worktree.mkdir()
    purpose = worktree / "_PURPOSE.md"
    purpose.write_text("Purpose: fold worktree implications into PR review.\n", encoding="utf-8")

    paths = list(
        provider_context_feed._iter_files(
            root,
            provider_context_feed.SourceSpec(
                str(worktree),
                "shared",
                "worktree-purpose",
            ),
        )
    )

    assert purpose in paths


def test_worktree_purpose_discovery_includes_non_wf_and_claude_nested_paths(tmp_path: Path) -> None:
    root = tmp_path / "TinyAssets"
    root.mkdir()
    sibling = tmp_path / "TinyAssets-scorched-pwa-live"
    sibling.mkdir()
    (sibling / "_PURPOSE.md").write_text("Purpose: sibling branch memory.\n", encoding="utf-8")
    nested = root / ".claude" / "worktrees" / "agent-a54683e4"
    nested.mkdir(parents=True)
    (nested / "_PURPOSE.md").write_text("Purpose: nested agent branch memory.\n", encoding="utf-8")

    paths = provider_context_feed._worktree_purpose_paths(root)

    assert (sibling / "_PURPOSE.md").resolve() in paths
    assert (nested / "_PURPOSE.md").resolve() in paths


def test_worktree_purpose_feed_surfaces_memory_related_and_idea_refs(tmp_path: Path) -> None:
    root = tmp_path / "TinyAssets"
    root.mkdir()
    worktree = tmp_path / "wf-demo"
    worktree.mkdir()
    (worktree / "_PURPOSE.md").write_text(
        "\n".join(
            [
                "Purpose: preserve branch memory.",
                "Provider: codex-gpt5-desktop",
                "Branch: codex/demo",
                "STATUS/Issue/PR: STATUS.md demo row.",
                "Ship condition: draft PR reviewed.",
                "Abandon condition: superseded.",
                "Memory refs:",
                "- .claude/agent-memory/navigator/demo.md",
                "Related implications:",
                "- docs/audits/demo.md",
                "Pickup hints:",
                "- Do not switch dirty checkout to main.",
                "Idea feed refs:",
                "- ideas/INBOX.md loose idea only.",
            ]
        ),
        encoding="utf-8",
    )

    candidates = provider_context_feed.collect_candidates(
        root,
        provider="all",
        phase="claim",
        limit=12,
    )
    texts = "\n".join(item.text for item in candidates)

    assert "Memory refs" in texts
    assert ".claude/agent-memory/navigator/demo.md" in texts
    assert "Related implications" in texts
    assert "Idea feed refs" in texts


def test_render_text_names_lifecycle_checkpoints() -> None:
    item = provider_context_feed.FeedCandidate(
        provider="shared",
        source_type="idea-feed",
        path="ideas/INBOX.md",
        line=12,
        signal="idea",
        text="Idea: use GitHub worktree lanes for provider memories.",
    )

    rendered = provider_context_feed.render_text(
        [item],
        phase="review",
        provider="all",
    )

    assert "claim, plan, build, review" in rendered
    assert "worktree/PR" in rendered
    assert "ideas/INBOX.md" in rendered


def test_parse_worktree_branches_extracts_branch_per_worktree() -> None:
    porcelain = (
        "worktree C:/repo\n"
        "HEAD aaaaaaa\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree C:/repo/.claude/worktrees/feature-x\n"
        "HEAD bbbbbbb\n"
        "branch refs/heads/worktree-feature-x\n"
        "\n"
        "worktree C:/repo/.claude/worktrees/detached\n"
        "HEAD ccccccc\n"
        "detached\n"
    )
    result = provider_context_feed._parse_worktree_branches(porcelain)
    branches = {str(path).replace("\\", "/"): branch for path, branch in result.items()}
    assert any(b == "worktree-feature-x" for b in branches.values())
    assert any(b == "main" for b in branches.values())
    # detached worktrees omit the branch line and are skipped
    assert "ref/heads/detached" not in branches
    assert all("detached" not in b for b in branches.values())


def test_drop_dead_lane_purposes_filters_merged_branches(
    tmp_path: Path, monkeypatch
) -> None:
    """A _PURPOSE.md sitting in a worktree whose branch is already an ancestor of
    origin/main is no longer actionable — the lead does not need to see it as a
    candidate lane."""
    dead_wt = tmp_path / "wf-dead"
    live_wt = tmp_path / "wf-live"
    dead_wt.mkdir()
    live_wt.mkdir()
    dead_purpose = dead_wt / "_PURPOSE.md"
    live_purpose = live_wt / "_PURPOSE.md"
    dead_purpose.write_text("Purpose: this lane was already merged.\n", encoding="utf-8")
    live_purpose.write_text("Purpose: this lane still has unmerged work.\n", encoding="utf-8")

    branch_map = {
        dead_wt.resolve(): "codex/dead-lane",
        live_wt.resolve(): "codex/live-lane",
    }

    monkeypatch.setattr(
        provider_context_feed,
        "_merged_branch_set",
        lambda *_a, **_kw: {"codex/dead-lane"},
    )

    kept = provider_context_feed._drop_dead_lane_purposes(
        [dead_purpose, live_purpose],
        branch_map=branch_map,
        root=tmp_path,
    )

    assert live_purpose in kept
    assert dead_purpose not in kept


def test_drop_dead_lane_purposes_passes_through_when_env_flag_set(
    tmp_path: Path, monkeypatch
) -> None:
    """`TINYASSETS_FEED_INCLUDE_DEAD_LANES=1` lets archaeologists see everything."""
    dead_wt = tmp_path / "wf-dead"
    dead_wt.mkdir()
    dead_purpose = dead_wt / "_PURPOSE.md"
    dead_purpose.write_text("Purpose: merged lane.\n", encoding="utf-8")
    branch_map = {dead_wt.resolve(): "codex/dead-lane"}

    monkeypatch.setattr(
        provider_context_feed, "_merged_branch_set", lambda *_a, **_kw: {"codex/dead-lane"}
    )
    monkeypatch.setenv("TINYASSETS_FEED_INCLUDE_DEAD_LANES", "1")

    kept = provider_context_feed._drop_dead_lane_purposes(
        [dead_purpose], branch_map=branch_map, root=tmp_path
    )

    assert dead_purpose in kept


def test_drop_dead_lane_purposes_keeps_paths_outside_any_worktree(
    tmp_path: Path, monkeypatch
) -> None:
    """A _PURPOSE.md that isn't under any tracked worktree (e.g. an ad-hoc origin/
    snapshot) should pass through untouched — we only prune when we have confident
    branch info."""
    stranger_dir = tmp_path / "origin" / "ad-hoc"
    stranger_dir.mkdir(parents=True)
    stranger_purpose = stranger_dir / "_PURPOSE.md"
    stranger_purpose.write_text("Purpose: not a worktree.\n", encoding="utf-8")

    dead_wt = tmp_path / "wf-dead"
    dead_wt.mkdir()
    dead_purpose = dead_wt / "_PURPOSE.md"
    dead_purpose.write_text("Purpose: merged lane.\n", encoding="utf-8")

    branch_map = {dead_wt.resolve(): "codex/dead-lane"}

    monkeypatch.setattr(
        provider_context_feed, "_merged_branch_set", lambda *_a, **_kw: {"codex/dead-lane"}
    )

    kept = provider_context_feed._drop_dead_lane_purposes(
        [stranger_purpose, dead_purpose],
        branch_map=branch_map,
        root=tmp_path,
    )

    assert stranger_purpose in kept
    assert dead_purpose not in kept


def test_closest_worktree_ancestor_picks_deepest_match(tmp_path: Path) -> None:
    """If a purpose path lives several directories deep inside a worktree, the
    matching worktree must still be detected as its ancestor."""
    worktree = tmp_path / "wf-foo"
    nested_purpose = worktree / "sub" / "dir" / "_PURPOSE.md"
    nested_purpose.parent.mkdir(parents=True)
    nested_purpose.write_text("anything", encoding="utf-8")

    other_worktree = tmp_path / "wf-bar"
    other_worktree.mkdir()

    ancestor = provider_context_feed._closest_worktree_ancestor(
        nested_purpose, [other_worktree, worktree]
    )

    assert ancestor == worktree.resolve()


def test_drop_dead_lane_purposes_returns_input_when_branch_map_empty(
    tmp_path: Path,
) -> None:
    """If we couldn't read the worktree porcelain (empty branch_map), passthrough."""
    purpose = tmp_path / "_PURPOSE.md"
    purpose.write_text("anything", encoding="utf-8")

    kept = provider_context_feed._drop_dead_lane_purposes(
        [purpose], branch_map={}, root=tmp_path
    )

    assert kept == [purpose]

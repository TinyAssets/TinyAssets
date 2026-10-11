"""Goal subscription and pool producer tests.

Covers phase_f_preflight.md (archived in git) §4.4:

- BranchTaskProducer protocol (4 tests)
- GoalPoolProducer (8 tests)
- Subscription data model (6 tests)
- MCP post_to_goal_pool (5 tests)
- Flag matrix (5 tests)
- Dispatcher wire-up (5 tests, R1)
- Invariants 1/2/3/5/9/10 (6 tests)
- R13 double-execution (1 test)

Total: ~40 tests. All tests rely on tmp_path fixtures; `TINYASSETS_REPO_ROOT`
pinning avoids needing a git scaffold.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest
import yaml

from tinyassets.branch_tasks import (
    BranchTask,
    append_task,
    mark_status,
    new_task_id,
    read_queue,
)
from tinyassets.dispatcher import (
    run_branch_task_producers_into_queue,
)
from tinyassets.producers import branch_task as bt_producer_mod
from tinyassets.producers.branch_task import (
    register_branch_task_producer,
    registered_branch_task_producers,
    reset_branch_task_registry,
    run_branch_task_producers,
)
from tinyassets.producers.goal_pool import (
    POOL_DIRNAME,
    POOL_ORIGIN,
    GoalPoolProducer,
    _accessible_branch_slugs,
    goal_pool_enabled,
    repo_root_path,
    validate_pool_task_inputs,
    write_pool_post,
)
from tinyassets.subscriptions import (
    DEFAULT_GOALS,
    list_subscriptions,
    subscribe,
    unsubscribe,
)

# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_branch_task_registry():
    """Reset the BranchTaskProducer registry between tests."""
    saved = list(bt_producer_mod._REGISTRY)
    reset_branch_task_registry()
    yield
    reset_branch_task_registry()
    bt_producer_mod._REGISTRY.extend(saved)


def _register_fantasy_branch_slugs() -> None:
    import fantasy_daemon.branch_registrations as registrations

    importlib.reload(registrations)


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch) -> Path:
    """Tmp repo-root with env pinning (avoids git scaffolding)."""
    _register_fantasy_branch_slugs()
    root = tmp_path / "repo"
    root.mkdir()
    (root / "branches").mkdir()
    (root / POOL_DIRNAME).mkdir()
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(root))
    return root


@pytest.fixture
def universe_dir(tmp_path: Path) -> Path:
    udir = tmp_path / "uni-a"
    udir.mkdir()
    return udir


@pytest.fixture
def two_universe(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """Two universes sharing a repo_root. Returns (repo_root, uni_a, uni_b)."""
    _register_fantasy_branch_slugs()
    root = tmp_path / "shared_repo"
    root.mkdir()
    (root / "branches").mkdir()
    (root / POOL_DIRNAME).mkdir()
    uni_a = root / "uni-a"
    uni_a.mkdir()
    uni_b = root / "uni-b"
    uni_b.mkdir()
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(root))
    return root, uni_a, uni_b


@pytest.fixture
def pool_flag_on(monkeypatch):
    monkeypatch.setenv("TINYASSETS_GOAL_POOL", "on")
    return True


@pytest.fixture
def pool_flag_off(monkeypatch):
    monkeypatch.setenv("TINYASSETS_GOAL_POOL", "off")
    return True


def _write_pool_yaml(
    repo_root: Path,
    goal: str,
    task_id: str,
    *,
    branch_def_id: str = "fantasy_author:universe_cycle_wrapper",
    inputs: dict | None = None,
    priority_weight: float = 0.0,
    posted_by: str = "anonymous",
    goal_id_in_yaml: str | None = None,
) -> Path:
    pool_dir = repo_root / POOL_DIRNAME / goal
    pool_dir.mkdir(parents=True, exist_ok=True)
    path = pool_dir / f"{task_id}.yaml"
    payload = {
        "branch_task_id": task_id,
        "branch_def_id": branch_def_id,
        "goal_id": goal_id_in_yaml or goal,
        "inputs": inputs or {"active_series": "test"},
        "priority_weight": priority_weight,
        "posted_by": posted_by,
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


# ───────────────────────────────────────────────────────────────────────
# BranchTaskProducer protocol (4 tests)
# ───────────────────────────────────────────────────────────────────────


class _StubProducer:
    name = "stub"
    origin = "goal_pool"

    def __init__(self, tasks=None, name="stub"):
        self._tasks = tasks or []
        self.name = name
        self.calls = 0

    def produce(self, universe_path, *, subscribed_goals, config=None):
        self.calls += 1
        return list(self._tasks)


def test_register_branch_task_producer_appends_and_lists():
    p = _StubProducer()
    register_branch_task_producer(p)
    assert p in registered_branch_task_producers()


def test_register_duplicate_name_replaces_prior():
    p1 = _StubProducer(name="dup")
    p2 = _StubProducer(name="dup")
    register_branch_task_producer(p1)
    register_branch_task_producer(p2)
    regs = registered_branch_task_producers()
    assert len(regs) == 1
    assert regs[0] is p2


def test_run_branch_task_producers_empty_registry_returns_empty(
    universe_dir,
):
    assert run_branch_task_producers(
        universe_dir, subscribed_goals=["x"],
    ) == []


def test_run_branch_task_producers_swallows_producer_exception(
    universe_dir, caplog,
):
    class _BadProducer:
        name = "bad"
        origin = "goal_pool"

        def produce(self, *args, **kwargs):
            raise RuntimeError("boom")

    register_branch_task_producer(_BadProducer())
    import logging as _logging
    caplog.set_level(_logging.WARNING)
    # Must not raise
    out = run_branch_task_producers(
        universe_dir, subscribed_goals=["x"],
    )
    assert out == []


# ───────────────────────────────────────────────────────────────────────
# GoalPoolProducer (8 tests)
# ───────────────────────────────────────────────────────────────────────


def test_goal_pool_empty_dir_returns_empty(repo_root, universe_dir):
    prod = GoalPoolProducer()
    out = prod.produce(
        universe_dir, subscribed_goals=["maintenance"],
    )
    assert out == []


def test_goal_pool_malformed_yaml_skipped_with_warning(
    repo_root, universe_dir, caplog,
):
    pool_dir = repo_root / POOL_DIRNAME / "maintenance"
    pool_dir.mkdir(parents=True, exist_ok=True)
    (pool_dir / "bad.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
    prod = GoalPoolProducer()
    import logging as _logging
    caplog.set_level(_logging.WARNING)
    out = prod.produce(
        universe_dir, subscribed_goals=["maintenance"],
    )
    assert out == []


def test_goal_pool_rejects_universe_path_in_inputs(repo_root, universe_dir):
    _write_pool_yaml(
        repo_root, "maintenance", "contamination",
        inputs={"_universe_path": "/foo"},
    )
    prod = GoalPoolProducer()
    out = prod.produce(
        universe_dir, subscribed_goals=["maintenance"],
    )
    assert out == []


def test_goal_pool_rejects_missing_required_fields(repo_root, universe_dir):
    pool_dir = repo_root / POOL_DIRNAME / "maintenance"
    pool_dir.mkdir(parents=True, exist_ok=True)
    (pool_dir / "missing.yaml").write_text(
        yaml.safe_dump({"inputs": {"x": 1}}),  # no branch_def_id
        encoding="utf-8",
    )
    prod = GoalPoolProducer()
    out = prod.produce(
        universe_dir, subscribed_goals=["maintenance"],
    )
    assert out == []


def test_goal_pool_valid_task_emits_branch_task(repo_root, universe_dir):
    task_id = "valid_task_1"
    _write_pool_yaml(repo_root, "maintenance", task_id)
    prod = GoalPoolProducer()
    out = prod.produce(
        universe_dir, subscribed_goals=["maintenance"],
    )
    assert len(out) == 1
    assert out[0].branch_task_id == task_id
    assert out[0].trigger_source == POOL_ORIGIN
    assert out[0].status == "pending"


def test_goal_pool_emits_goal_ladder_rung_claim_recommendations(
    repo_root, universe_dir,
):
    from tinyassets.daemon_server import save_goal

    save_goal(
        universe_dir.parent,
        goal={
            "goal_id": "maintenance",
            "name": "Maintenance",
            "author": "host",
            "gate_ladder": [
                {
                    "rung_key": "pr_ready",
                    "label": "PR ready",
                    "description": "Patch and tests are ready.",
                    "branch_requirements": {
                        "allowed_writer_families": ["claude", "codex"],
                        "forbid_same_family_checker": True,
                    },
                    "bounty_requirements": {
                        "settlement_gate": "pr_ready",
                    },
                },
            ],
        },
    )
    _write_pool_yaml(repo_root, "maintenance", "loop_2_task")

    out = GoalPoolProducer().produce(
        universe_dir, subscribed_goals=["maintenance"],
    )

    assert len(out) == 1
    assert out[0].rung_claim_recommendations == [
        {
            "goal_id": "maintenance",
            "rung_key": "pr_ready",
            "label": "PR ready",
            "description": "Patch and tests are ready.",
            "branch_requirements": {
                "allowed_writer_families": ["claude", "codex"],
                "forbid_same_family_checker": True,
            },
            "bounty_requirements": {
                "settlement_gate": "pr_ready",
            },
            "claim_action": None,
            "claim_gap": (
                "Gate claims are not exposed by the advertised handles; "
                "use an internal operator surface."
            ),
        },
    ]


def test_goal_pool_rescan_idempotency(repo_root, universe_dir):
    """Invariant 4: re-running produces same tasks, not duplicates."""
    for i in range(3):
        _write_pool_yaml(repo_root, "maintenance", f"task_{i}")
    prod = GoalPoolProducer()
    first = prod.produce(
        universe_dir, subscribed_goals=["maintenance"],
    )
    second = prod.produce(
        universe_dir, subscribed_goals=["maintenance"],
    )
    ids_a = sorted(t.branch_task_id for t in first)
    ids_b = sorted(t.branch_task_id for t in second)
    assert ids_a == ids_b == sorted(f"task_{i}" for i in range(3))


def test_goal_pool_unresolved_branch_slug_skipped(
    repo_root, universe_dir, caplog,
):
    """Invariant 6 / R9: branch_def_id not in accessible slugs → skipped."""
    _write_pool_yaml(
        repo_root, "maintenance", "wrong_branch",
        branch_def_id="nonexistent/branch/slug",
    )
    prod = GoalPoolProducer()
    import logging as _logging
    caplog.set_level(_logging.INFO)
    out = prod.produce(
        universe_dir, subscribed_goals=["maintenance"],
    )
    assert out == []


def test_accessible_branch_slugs_do_not_inject_fantasy_by_default(
    repo_root, universe_dir,
):
    from tinyassets.domain_registry import clear_domain_branch_slugs

    clear_domain_branch_slugs()

    assert _accessible_branch_slugs(repo_root, universe_dir) == set()


def test_goal_pool_accepts_registered_fantasy_seed(repo_root, universe_dir):
    from tinyassets.domain_registry import clear_domain_branch_slugs

    clear_domain_branch_slugs()
    _register_fantasy_branch_slugs()
    _write_pool_yaml(repo_root, "maintenance", "registered_fantasy_seed")

    out = GoalPoolProducer().produce(
        universe_dir, subscribed_goals=["maintenance"],
    )

    assert len(out) == 1
    assert out[0].branch_def_id == "fantasy_author:universe_cycle_wrapper"


def test_goal_pool_skips_fantasy_seed_when_unregistered(repo_root, universe_dir):
    """Pin the process-dependent de-fantasy divergence.

    In a process that has NOT loaded the fantasy domain (the cloud-worker
    producer pump, the plugin runtime), ``registered_domain_branch_slugs()`` is
    empty, so a goal-pool task targeting the fantasy wrapper is skipped here.
    This is the intended engine-is-infrastructure contract, not a silent
    regression: the fantasy daemon (which imports its registrations) still
    scans the pool at graph-start. The complement of
    ``test_goal_pool_accepts_registered_fantasy_seed`` — together they make the
    divergence an explicit, tested contract instead of a hidden one.
    """
    from tinyassets.domain_registry import clear_domain_branch_slugs

    clear_domain_branch_slugs()  # simulate a process without the fantasy domain
    # A real repo has public branches, so the accessible-slug filter is active
    # (it is fail-open only when the subscriber can reach NO slugs at all). The
    # fantasy wrapper is simply absent from the accessible set in this process.
    (repo_root / "branches" / "public_branch.yaml").write_text("{}", encoding="utf-8")
    _write_pool_yaml(repo_root, "maintenance", "unregistered_fantasy_seed")

    out = GoalPoolProducer().produce(
        universe_dir, subscribed_goals=["maintenance"],
    )

    assert out == []


def test_goal_pool_fails_open_when_no_accessible_slugs(repo_root, universe_dir):
    """Pin the fail-OPEN half of the accessibility filter.

    The guard in ``_parse_pool_yaml`` is ``if accessible_slugs and ...`` — it
    only filters when the subscriber can reach at least one branch. When the
    accessible set is genuinely empty (no public branches, no catalog slugs, no
    registered domain — a bare/degenerate repo, or enumeration that legitimately
    found nothing) the filter fail-OPENS and the task is queued rather than
    silently dropped, because an empty set means "can't determine accessibility,"
    not "this branch is inaccessible." This is intentional and NOT
    fantasy-specific. Pinning it keeps the fail-open an explicit contract so a
    future "tighten the guard" change has to confront this test rather than
    silently flipping degenerate-repo behavior to fail-closed.
    """
    from tinyassets.domain_registry import clear_domain_branch_slugs

    clear_domain_branch_slugs()  # no registered domain slugs
    # Deliberately create NO public branch, so accessible_slugs stays empty
    # (the complement of test_goal_pool_skips_fantasy_seed_when_unregistered,
    # which seeds public_branch.yaml to activate the filter).
    _write_pool_yaml(repo_root, "maintenance", "seed_in_bare_repo")

    out = GoalPoolProducer().produce(
        universe_dir, subscribed_goals=["maintenance"],
    )

    assert len(out) == 1
    assert out[0].branch_def_id == "fantasy_author:universe_cycle_wrapper"


def test_goal_pool_mtime_cache_invalidation(repo_root, universe_dir):
    """R3: mtime-based cache. Adding a file invalidates."""
    _write_pool_yaml(repo_root, "maintenance", "first")
    prod = GoalPoolProducer()
    first = prod.produce(universe_dir, subscribed_goals=["maintenance"])
    assert len(first) == 1
    # Add another file — dir mtime changes, cache invalidates.
    import time as _time
    _time.sleep(0.05)
    _write_pool_yaml(repo_root, "maintenance", "second")
    # Touch the directory mtime explicitly (some filesystems don't
    # change dir mtime on child file creation).
    pool_dir = repo_root / POOL_DIRNAME / "maintenance"
    new_mtime = pool_dir.stat().st_mtime + 10
    os.utime(pool_dir, (new_mtime, new_mtime))
    second = prod.produce(universe_dir, subscribed_goals=["maintenance"])
    assert len(second) == 2


# ───────────────────────────────────────────────────────────────────────
# Subscription data model (6 tests)
# ───────────────────────────────────────────────────────────────────────


def test_fresh_install_returns_maintenance_default(universe_dir):
    """Invariant 10."""
    assert list_subscriptions(universe_dir) == list(DEFAULT_GOALS)


def test_subscribe_is_idempotent(universe_dir):
    subscribe(universe_dir, "research-paper")
    subscribe(universe_dir, "research-paper")
    subs = list_subscriptions(universe_dir)
    assert subs.count("research-paper") == 1


def test_unsubscribe_silent_on_missing(universe_dir):
    # Just ensure no raise
    unsubscribe(universe_dir, "never-subscribed")


def test_list_subscriptions_returns_current_set(universe_dir):
    subscribe(universe_dir, "fantasy-novel")
    subscribe(universe_dir, "research-paper")
    subs = list_subscriptions(universe_dir)
    assert "fantasy-novel" in subs
    assert "research-paper" in subs


def test_list_subscriptions_mcp_drift_pool_enabled_no_subs(
    tmp_path, monkeypatch, pool_flag_on,
):
    from tinyassets.api.universe import _action_list_subscriptions

    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    (base / uid).mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)

    # Enable pool in dispatcher config
    cfg_yaml = base / uid / "dispatcher_config.yaml"
    cfg_yaml.write_text("accept_goal_pool: true\n", encoding="utf-8")

    # Clear default — write explicit empty subscriptions
    (base / uid / "subscriptions.json").write_text(
        json.dumps({"goals": [], "updated_at": ""}), encoding="utf-8",
    )

    resp = json.loads(_action_list_subscriptions(universe_id=uid))
    assert resp["config_vs_subscriptions_drift"] == "pool_enabled_no_subs"


def test_list_subscriptions_mcp_drift_subs_but_pool_disabled(
    tmp_path, monkeypatch, pool_flag_on,
):
    from tinyassets.api.universe import _action_list_subscriptions

    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    (base / uid).mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)

    # accept_goal_pool defaults to False
    subscribe(base / uid, "research-paper")

    resp = json.loads(_action_list_subscriptions(universe_id=uid))
    assert resp["config_vs_subscriptions_drift"] == "subs_but_pool_disabled"


# ───────────────────────────────────────────────────────────────────────
# MCP post_to_goal_pool (5 tests)
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def mcp_harness(tmp_path, monkeypatch, pool_flag_on):
    """TINYASSETS_DATA_DIR + TINYASSETS_REPO_ROOT pinned."""
    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    (base / uid).mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "branches").mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(repo))
    return {"base": base, "uid": uid, "repo": repo}


def test_post_to_goal_pool_valid_writes_yaml(mcp_harness):
    from tinyassets.api.universe import _action_post_to_goal_pool

    resp = json.loads(_action_post_to_goal_pool(
        universe_id=mcp_harness["uid"],
        goal_id="maintenance",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        inputs_json=json.dumps({"active_series": "x"}),
    ))
    assert resp["status"] == "posted"
    assert Path(resp["path"]).exists()


def test_post_to_goal_pool_response_has_git_push_hint(mcp_harness):
    from tinyassets.api.universe import _action_post_to_goal_pool

    resp = json.loads(_action_post_to_goal_pool(
        universe_id=mcp_harness["uid"],
        goal_id="maintenance",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        inputs_json=json.dumps({"active_series": "x"}),
    ))
    assert "git add" in resp["next_step"]
    assert "git commit" in resp["next_step"]
    assert "git push" in resp["next_step"]


def test_post_to_goal_pool_priority_capability_weight_persists(
    mcp_harness, monkeypatch,
):
    from tinyassets.api.universe import _action_post_to_goal_pool

    monkeypatch.setenv("UNIVERSE_SERVER_USER", "operator")
    monkeypatch.setenv("UNIVERSE_SERVER_CAPABILITIES", "post_priority_goal_pool")
    resp = json.loads(_action_post_to_goal_pool(
        universe_id=mcp_harness["uid"],
        goal_id="maintenance",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        inputs_json="{}",
        priority_weight=50.0,
    ))
    assert resp["status"] == "posted"
    data = yaml.safe_load(Path(resp["path"]).read_text(encoding="utf-8"))
    assert data["priority_weight"] == 50.0


def test_post_to_goal_pool_missing_priority_capability_weight_clamped(
    mcp_harness, monkeypatch,
):
    """Invariant 7."""
    from tinyassets.api.universe import _action_post_to_goal_pool

    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.delenv("UNIVERSE_SERVER_CAPABILITIES", raising=False)
    resp = json.loads(_action_post_to_goal_pool(
        universe_id=mcp_harness["uid"],
        goal_id="maintenance",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        inputs_json="{}",
        priority_weight=50.0,
    ))
    assert resp["status"] == "posted"
    data = yaml.safe_load(Path(resp["path"]).read_text(encoding="utf-8"))
    assert data["priority_weight"] == 0.0


def test_post_to_goal_pool_rejects_universe_path_in_inputs(mcp_harness):
    """R4 symmetry: server-side rejects the same inputs the producer would."""
    from tinyassets.api.universe import _action_post_to_goal_pool

    resp = json.loads(_action_post_to_goal_pool(
        universe_id=mcp_harness["uid"],
        goal_id="maintenance",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        inputs_json=json.dumps({"_universe_path": "/etc"}),
    ))
    assert resp["status"] == "rejected"
    assert "invalid_inputs" in resp["error"]


# ───────────────────────────────────────────────────────────────────────
# Flag matrix (5 tests per §4.4 enumeration)
# ───────────────────────────────────────────────────────────────────────


def test_flag_matrix_f_off_mcp_post_returns_not_available(
    tmp_path, monkeypatch,
):
    """Cell 3 (D-on+E-on+F-off) + any F-off: post returns not_available."""
    monkeypatch.setenv("TINYASSETS_GOAL_POOL", "off")
    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    (base / uid).mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)

    from tinyassets.api.universe import _action_post_to_goal_pool
    resp = json.loads(_action_post_to_goal_pool(
        universe_id=uid, goal_id="maintenance",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        inputs_json="{}",
    ))
    assert resp["status"] == "not_available"


def test_flag_matrix_f_on_pool_producer_registers(monkeypatch):
    """Cell 5 (D-on+E-on+F-on): GoalPoolProducer registers on import."""
    monkeypatch.setenv("TINYASSETS_GOAL_POOL", "on")
    # Force re-registration by calling register_if_enabled
    from tinyassets.producers.goal_pool import register_if_enabled
    reset_branch_task_registry()
    assert register_if_enabled() is True
    names = [p.name for p in registered_branch_task_producers()]
    assert "goal_pool" in names


def test_flag_matrix_f_off_pool_producer_does_not_register(monkeypatch):
    """Cell 3 (F-off): register_if_enabled returns False, no registration."""
    monkeypatch.setenv("TINYASSETS_GOAL_POOL", "off")
    from tinyassets.producers.goal_pool import register_if_enabled
    reset_branch_task_registry()
    assert register_if_enabled() is False
    names = [p.name for p in registered_branch_task_producers()]
    assert "goal_pool" not in names


def test_flag_matrix_d_off_e_on_f_on_staging(
    tmp_path, monkeypatch, pool_flag_on, repo_root, universe_dir,
):
    """Cell 4 (D-off + E-on + F-on): staging state — posts work,
    subscribe works, YAMLs land on disk, but execution path inert."""
    monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", "0")
    monkeypatch.setenv("TINYASSETS_DISPATCHER_ENABLED", "on")

    # Post lands:
    _write_pool_yaml(repo_root, "maintenance", "staging_task")
    # Subscribe works:
    subscribe(universe_dir, "maintenance")
    # Producer runs:
    from tinyassets.producers.goal_pool import register_if_enabled
    reset_branch_task_registry()
    register_if_enabled()
    appended = run_branch_task_producers_into_queue(
        universe_dir, subscribed_goals=["maintenance"],
    )
    assert appended == 1
    # But no daemon drives execution — verified elsewhere. Here we
    # only pin that the F-on path records into queue.
    q = read_queue(universe_dir)
    assert q[0].trigger_source == POOL_ORIGIN


def test_flag_matrix_default_off_state(monkeypatch):
    """Cell default: TINYASSETS_GOAL_POOL defaults to off."""
    monkeypatch.delenv("TINYASSETS_GOAL_POOL", raising=False)
    assert goal_pool_enabled() is False


# ───────────────────────────────────────────────────────────────────────
# Dispatcher wire-up (5 tests, R1 + §4.10)
# ───────────────────────────────────────────────────────────────────────


def test_wire_up_try_pick_returns_none_when_queue_empty(
    universe_dir, monkeypatch,
):
    from fantasy_daemon.__main__ import _try_dispatcher_pick

    monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", "1")
    monkeypatch.setenv("TINYASSETS_DISPATCHER_ENABLED", "on")
    claimed, inputs = _try_dispatcher_pick(universe_dir, "daemon-test")
    assert claimed is None
    assert inputs == {}


def test_wire_up_try_pick_claims_pending_task(
    universe_dir, monkeypatch,
):
    from fantasy_daemon.__main__ import _try_dispatcher_pick

    monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", "1")
    monkeypatch.setenv("TINYASSETS_DISPATCHER_ENABLED", "on")
    # user_request tier is default-enabled in DispatcherConfig; goal_pool
    # is default-disabled. Wire-up is tier-agnostic so we use the tier
    # that's live by default.
    task = BranchTask(
        branch_task_id=new_task_id(),
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id="uni-a",
        inputs={"active_series": "pooled"},
        trigger_source="user_request",
    )
    append_task(universe_dir, task)

    claimed, inputs_merge = _try_dispatcher_pick(
        universe_dir, "daemon-test",
    )
    assert claimed is not None
    assert claimed.branch_task_id == task.branch_task_id
    assert inputs_merge == {"active_series": "pooled"}
    # Queue shows running status
    q = read_queue(universe_dir)
    assert q[0].status == "running"
    assert q[0].claimed_by == "daemon-test"


def test_wire_up_cancel_during_claim_race(universe_dir, monkeypatch):
    """Invariant 8.2: cancel between select + claim → None, no raise."""
    from fantasy_daemon.__main__ import _try_dispatcher_pick

    monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", "1")
    monkeypatch.setenv("TINYASSETS_DISPATCHER_ENABLED", "on")

    task = BranchTask(
        branch_task_id=new_task_id(),
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id="uni-a",
        trigger_source="user_request",
    )
    append_task(universe_dir, task)
    # Simulate cancel before the wire-up's claim attempt by patching
    # select_next_task to cancel the task after returning it.
    import tinyassets.dispatcher as dispatcher_mod
    real_select = dispatcher_mod.select_next_task

    def _select_then_cancel(*args, **kwargs):
        result = real_select(*args, **kwargs)
        if result is not None:
            mark_status(
                universe_dir, result.branch_task_id, status="cancelled",
            )
        return result

    monkeypatch.setattr(dispatcher_mod, "select_next_task", _select_then_cancel)
    # Must not raise; returns None.
    claimed, inputs = _try_dispatcher_pick(universe_dir, "daemon-test")
    assert claimed is None
    assert inputs == {}


def test_wire_up_flag_off_returns_none(universe_dir, monkeypatch):
    """Dispatcher flag off → pick returns None regardless of queue."""
    from fantasy_daemon.__main__ import _try_dispatcher_pick

    monkeypatch.setenv("TINYASSETS_DISPATCHER_ENABLED", "off")
    task = BranchTask(
        branch_task_id=new_task_id(),
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id="uni-a",
        trigger_source="user_request",
    )
    append_task(universe_dir, task)
    claimed, inputs = _try_dispatcher_pick(universe_dir, "daemon-test")
    assert claimed is None


def test_wire_up_finalize_marks_succeeded(universe_dir):
    from fantasy_daemon.__main__ import _finalize_claimed_task

    task = BranchTask(
        branch_task_id=new_task_id(),
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        universe_id="uni-a",
        trigger_source="user_request",
        status="running",
    )
    # Inject directly as running:
    from tinyassets.branch_tasks import queue_path
    qp = queue_path(universe_dir)
    qp.write_text(json.dumps([task.to_dict()]), encoding="utf-8")

    _finalize_claimed_task(universe_dir, task, success=True)
    q = read_queue(universe_dir)
    assert q[0].status == "succeeded"


# ───────────────────────────────────────────────────────────────────────
# Invariants (6 tests)
# ───────────────────────────────────────────────────────────────────────


def test_invariant_1_flat_dict_isolation(repo_root, universe_dir):
    """Invariant 1 + R4: nested dicts rejected, flat accepted."""
    # Nested:
    ok, _ = validate_pool_task_inputs(
        {"outer": {"_universe_path": "/sneaky"}},
    )
    assert ok is False
    # Underscore key at top level:
    ok, _ = validate_pool_task_inputs({"_universe_path": "/bad"})
    assert ok is False
    # Clean flat:
    ok, reason = validate_pool_task_inputs({"series": "x", "ch": 5})
    assert ok is True, reason
    # List value rejected (not a primitive):
    ok, _ = validate_pool_task_inputs({"items": [1, 2, 3]})
    assert ok is False


def test_invariant_2_registry_separation():
    """Invariant 2: TaskProducer and BranchTaskProducer registries
    contain zero shared instances by id()."""
    from tinyassets import producers as in_universe_mod

    in_universe_ids = {id(p) for p in in_universe_mod._REGISTRY}
    bt_ids = {id(p) for p in bt_producer_mod._REGISTRY}
    assert not (in_universe_ids & bt_ids)


def test_invariant_3_producer_boundary_separation(
    universe_dir, monkeypatch, pool_flag_on, repo_root,
):
    """Invariant 3: BranchTaskProducer.produce called at dispatcher
    boundary; in-universe TaskProducer.produce NOT called there.
    """
    from tinyassets import producers as in_universe_mod

    in_universe_calls: list[int] = []
    bt_calls: list[int] = []

    class _InUnivProducer:
        name = "iu-stub"
        origin = "seed"

        def produce(self, *a, **kw):
            in_universe_calls.append(1)
            return []

    class _BTProducer:
        name = "bt-stub"
        origin = "goal_pool"

        def produce(self, *a, **kw):
            bt_calls.append(1)
            return []

    saved_iu = list(in_universe_mod._REGISTRY)
    in_universe_mod._REGISTRY.clear()
    in_universe_mod._REGISTRY.append(_InUnivProducer())
    reset_branch_task_registry()
    register_branch_task_producer(_BTProducer())
    try:
        run_branch_task_producers_into_queue(
            universe_dir, subscribed_goals=["maintenance"],
        )
        # Dispatcher boundary: bt called exactly once; in-universe ZERO.
        assert len(bt_calls) == 1
        assert len(in_universe_calls) == 0
    finally:
        in_universe_mod._REGISTRY.clear()
        in_universe_mod._REGISTRY.extend(saved_iu)


def test_invariant_5_subscribe_post_pick_roundtrip(
    two_universe, monkeypatch, pool_flag_on,
):
    """End-to-end: uni-A posts → uni-B subscribed → uni-B's queue has it."""
    _, uni_a, uni_b = two_universe
    repo = Path(os.environ["TINYASSETS_REPO_ROOT"])

    # uni-B subscribes
    subscribe(uni_b, "test_goal")

    # uni-A posts
    task_id = "roundtrip_task"
    _write_pool_yaml(repo, "test_goal", task_id)

    # uni-B's producer runs:
    reset_branch_task_registry()
    register_branch_task_producer(GoalPoolProducer())
    appended = run_branch_task_producers_into_queue(
        uni_b, subscribed_goals=["test_goal"],
    )
    assert appended == 1

    q = read_queue(uni_b)
    assert any(t.branch_task_id == task_id for t in q)
    # uni-A has no queue entry from this post
    q_a = read_queue(uni_a)
    assert not any(t.branch_task_id == task_id for t in q_a)


def test_invariant_9_write_scoping_poster_unchanged(
    two_universe, pool_flag_on,
):
    """Invariant 9: a pool task posted from A leaves A's state
    byte-identical. This test is the simpler pre-execution version —
    execution is not triggered here; we verify the POST doesn't
    write to poster's universe state."""
    _, uni_a, uni_b = two_universe
    repo = Path(os.environ["TINYASSETS_REPO_ROOT"])

    # Snapshot uni-A contents before post:
    before = sorted(p.name for p in uni_a.iterdir())
    # Post from "uni-A" to pool:
    write_pool_post(
        repo, "test_goal",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        inputs={"active_series": "x"},
        posted_by="alice",
    )
    after = sorted(p.name for p in uni_a.iterdir())
    assert before == after  # no changes to poster universe


def test_invariant_10_fresh_install_default(universe_dir):
    """Already covered by test_fresh_install_returns_maintenance_default,
    but re-asserted under the invariant banner for clarity."""
    assert list_subscriptions(universe_dir) == list(DEFAULT_GOALS)


# ───────────────────────────────────────────────────────────────────────
# R13 double-execution visibility (1 test)
# ───────────────────────────────────────────────────────────────────────


def test_r13_double_execution_risk_visible_not_hidden(
    two_universe, pool_flag_on,
):
    """Two daemons both subscribing to the same goal and running the
    pool producer BOTH enqueue the same task into their own queues.
    This is accepted v1 behavior — the risk is visible (two queues,
    two entries), not hidden."""
    repo_root, uni_a, uni_b = two_universe
    subscribe(uni_a, "test_goal")
    subscribe(uni_b, "test_goal")

    _write_pool_yaml(repo_root, "test_goal", "shared_task")

    reset_branch_task_registry()
    register_branch_task_producer(GoalPoolProducer())
    run_branch_task_producers_into_queue(
        uni_a, subscribed_goals=["test_goal"],
    )
    run_branch_task_producers_into_queue(
        uni_b, subscribed_goals=["test_goal"],
    )

    q_a = read_queue(uni_a)
    q_b = read_queue(uni_b)
    assert any(t.branch_task_id == "shared_task" for t in q_a)
    assert any(t.branch_task_id == "shared_task" for t in q_b)
    # Both queues independently claim/mark: test one cancel doesn't
    # affect the other.
    mark_status(uni_a, "shared_task", status="cancelled")
    q_b_after = read_queue(uni_b)
    assert any(
        t.branch_task_id == "shared_task" and t.status == "pending"
        for t in q_b_after
    )


# ───────────────────────────────────────────────────────────────────────
# Bonus: repo_root resolution contract
# ───────────────────────────────────────────────────────────────────────


def test_repo_root_env_var_takes_precedence(tmp_path, monkeypatch):
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(pinned))
    # Even if no .git, env wins
    result = repo_root_path(tmp_path / "somewhere-else")
    assert result == pinned.resolve()


def test_repo_root_raises_when_unresolvable(tmp_path, monkeypatch):
    monkeypatch.delenv("TINYASSETS_REPO_ROOT", raising=False)
    bare = tmp_path / "bare"
    bare.mkdir()
    checked_dirs = {bare.resolve(), *bare.resolve().parents}
    real_exists = Path.exists

    def exists_without_git_ancestor(path: Path) -> bool:
        if path.name == ".git" and path.parent in checked_dirs:
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists_without_git_ancestor)
    with pytest.raises(RuntimeError, match="TINYASSETS_REPO_ROOT"):
        repo_root_path(bare)


def test_idempotent_append_via_dispatcher(
    universe_dir, monkeypatch, pool_flag_on, repo_root,
):
    """run_branch_task_producers_into_queue dedupes by branch_task_id."""
    _write_pool_yaml(repo_root, "maintenance", "idempo_task")
    reset_branch_task_registry()
    register_branch_task_producer(GoalPoolProducer())
    first = run_branch_task_producers_into_queue(
        universe_dir, subscribed_goals=["maintenance"],
    )
    second = run_branch_task_producers_into_queue(
        universe_dir, subscribed_goals=["maintenance"],
    )
    assert first == 1
    assert second == 0  # no duplicate append
    q = read_queue(universe_dir)
    assert sum(1 for t in q if t.branch_task_id == "idempo_task") == 1


def test_subscribe_unsubscribe_when_pool_flag_off_returns_not_available(
    tmp_path, monkeypatch,
):
    from tinyassets.api.universe import (
        _action_list_subscriptions,
        _action_subscribe_goal,
        _action_unsubscribe_goal,
    )

    monkeypatch.setenv("TINYASSETS_GOAL_POOL", "off")
    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    (base / uid).mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)

    for handler in (
        _action_subscribe_goal,
        _action_unsubscribe_goal,
        _action_list_subscriptions,
    ):
        resp = json.loads(handler(universe_id=uid, goal_id="x"))
        assert resp["status"] == "not_available"


@pytest.mark.parametrize('goal', ['../outside', '..', '.', '/tmp', 'a/b', r'a\b', 'C:evil', ''])
def test_unsafe_goal_never_reaches_pool_scan(repo_root, universe_dir, monkeypatch, caplog, goal):
    from tinyassets.producers.goal_pool import pool_post_path

    with pytest.raises(ValueError, match='safe path segment'):
        subscribe(universe_dir, goal)
    with pytest.raises(ValueError, match='safe path segment'):
        pool_post_path(repo_root, goal)
    with pytest.raises(ValueError, match='safe path segment'):
        write_pool_post(repo_root, goal, branch_def_id='branch', inputs={})
    (universe_dir / 'subscriptions.json').write_text(json.dumps({'goals': [goal, 'safe']}))
    assert list_subscriptions(universe_dir) == ['safe']
    producer = GoalPoolProducer()
    scanned = []
    monkeypatch.setattr(producer, '_scan_goal_dir', lambda *a, **kw: scanned.append(a) or [])
    assert producer.produce(universe_dir, subscribed_goals=[goal]) == []
    assert scanned == []
    assert 'skipping unsafe goal' in caplog.text

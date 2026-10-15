"""Phase 7 three-way commit atomicity — commit-failure rollback.

Covers the contract added by task #19:

1. When ``git_bridge.commit`` returns ``ok=False``, each composite
   ``*_and_commit`` helper rolls the YAML working tree back to its
   pre-call state and unstages the paths.
2. The SQLite row stays put (Path A — SQLite is the accepted-write
   boundary).
3. A row is appended to ``unreconciled_writes`` so the future
   ``sync_commit`` replay has a queue to drain.
4. ``CommitFailedError`` is raised so MCP callers can surface a
   structured error rather than silently succeeding.
5. ``git_bridge.unstage`` clears the index entries it staged.

Git-disabled and git-enabled-success paths are covered elsewhere
(``test_storage_phase7_git_integration.py``); this file targets the
new failure seam exclusively.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

from tinyassets import git_bridge
from tinyassets.branches import BranchDefinition, NodeDefinition
from tinyassets.catalog import (
    CommitFailedError,
    SqliteCachedBackend,
    YamlRepoLayout,
    list_unreconciled_writes,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git binary not available",
)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--initial-branch=main"], path)
    _run(["git", "config", "user.email", "ci@example.invalid"], path)
    _run(["git", "config", "user.name", "CI Bot"], path)
    _run(["git", "config", "commit.gpgsign", "false"], path)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _run(["git", "add", "README.md"], path)
    _run(["git", "commit", "-m", "seed", "--no-gpg-sign"], path)


@pytest.fixture(autouse=True)
def _reset_git_bridge_cache():
    git_bridge.invalidate_cache()
    yield
    git_bridge.invalidate_cache()


@pytest.fixture
def base_path(tmp_path, monkeypatch):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    from tinyassets.daemon_server import initialize_author_server
    initialize_author_server(base)
    from tinyassets import universe_server as us
    importlib.reload(us)
    yield base


@pytest.fixture
def center(base_path):
    """A command center under the data root, its owner's box session bound.

    Owner git runs only inside an admitted owner box, so the catalog
    repository sits in a command center and every call carries its binding.
    """
    from tinyassets.control_plane import binding as binding_module

    cc = base_path / "cc-catalog"
    cc.mkdir()
    binding = binding_module.file_binding(cc)
    assert binding is not None, "the owner box double binds a test center"
    with binding_module.use_binding(binding):
        yield cc


def _make_branch(name: str = "Commit-fail branch") -> BranchDefinition:
    b = BranchDefinition(
        name=name,
        description="initial desc",
        author="tester",
        entry_point="n1",
    )
    b.node_defs = [
        NodeDefinition(
            node_id="n1",
            display_name="Node one",
            prompt_template="echo: {x}",
            input_keys=["x"],
            output_keys=["y"],
        ),
    ]
    return b


def _patch_commit_to_fail(monkeypatch, error: str = "disk full"):
    """Make ``git_bridge.commit`` return ok=False without actually
    invoking git. ``git_bridge.stage`` / ``unstage`` / probes stay real.
    """
    def _fail(*args, **kwargs):
        return git_bridge.CommitResult(ok=False, error=error)
    monkeypatch.setattr(git_bridge, "commit", _fail)


# ─── unstage helper ──────────────────────────────────────────────────


def test_unstage_removes_paths_from_index(center):
    repo = center / "repo"
    _init_repo(repo)
    target = repo / "a.txt"
    target.write_text("hello\n", encoding="utf-8")
    _run(["git", "add", "a.txt"], repo)
    staged_before = _run(
        ["git", "diff", "--name-only", "--cached"], repo,
    ).stdout.splitlines()
    assert "a.txt" in staged_before

    assert git_bridge.unstage([target], repo_path=repo) is True

    staged_after = _run(
        ["git", "diff", "--name-only", "--cached"], repo,
    ).stdout.splitlines()
    assert "a.txt" not in staged_after


def test_unstage_no_op_when_git_disabled(tmp_path, monkeypatch):
    # Point at a non-repo directory so is_enabled returns False.
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    assert git_bridge.unstage(
        [not_a_repo / "x"], repo_path=not_a_repo,
    ) is False


# ─── save_branch_and_commit failure → rollback ────────────────────────


def test_save_branch_commit_failure_rolls_back_yaml(
    base_path, center, monkeypatch,
):
    repo = center / "repo"
    _init_repo(repo)
    backend = SqliteCachedBackend(base_path, repo_root=repo)
    layout = YamlRepoLayout(repo)
    branch_path = layout.branch_path("commit-fail-branch")

    # Sanity: file doesn't exist pre-call.
    assert not branch_path.exists()

    _patch_commit_to_fail(monkeypatch, error="simulated commit fail")

    with pytest.raises(CommitFailedError) as exc_info:
        backend.save_branch_and_commit(
            _make_branch(),
            author="CI Bot <ci@example.invalid>",
            message="branches.create_branch: fail-test",
        )

    # YAML rolled back (file deleted since it didn't exist pre-call).
    assert not branch_path.exists(), (
        "YAML should be rolled back after commit failure"
    )

    # Index should be clean.
    staged = _run(
        ["git", "diff", "--name-only", "--cached"], repo,
    ).stdout.splitlines()
    assert not any("commit-fail-branch" in s for s in staged), (
        f"nothing should remain staged; staged={staged}"
    )

    # SQLite row retained — explorer's Path A decision.
    from tinyassets.daemon_server import get_branch_definition
    row = get_branch_definition(
        base_path, branch_def_id=exc_info.value.row_ref,
    )
    assert row is not None
    assert row["name"] == "Commit-fail branch"

    # unreconciled_writes got a row.
    pending = list_unreconciled_writes(base_path)
    assert len(pending) == 1
    entry = pending[0]
    assert entry["helper_name"] == "save_branch_and_commit"
    assert entry["git_error"] == "simulated commit fail"
    assert entry["row_ref"] == exc_info.value.row_ref
    assert any("commit-fail-branch" in p for p in entry["paths"])


def test_save_branch_commit_failure_restores_prior_file(
    base_path, center, monkeypatch,
):
    """When the YAML existed before the call, rollback restores its
    prior bytes rather than deleting.
    """
    repo = center / "repo"
    _init_repo(repo)
    backend = SqliteCachedBackend(base_path, repo_root=repo)
    layout = YamlRepoLayout(repo)
    branch_path = layout.branch_path("commit-fail-branch")

    # First save + commit to get a prior file state.
    backend.save_branch_and_commit(
        _make_branch(),
        author="CI Bot <ci@example.invalid>",
        message="branches.create_branch: initial",
    )
    prior_bytes = branch_path.read_bytes()

    # Second save with a changed description — patched to fail commit.
    _patch_commit_to_fail(monkeypatch, error="simulated second-fail")
    updated = _make_branch()
    updated.description = "would be overwritten"
    with pytest.raises(CommitFailedError):
        backend.save_branch_and_commit(
            updated,
            author="CI Bot <ci@example.invalid>",
            message="branches.patch_branch: updated desc",
            force=True,  # skip dirty-check so we test commit-failure path
        )

    # Prior bytes restored.
    assert branch_path.read_bytes() == prior_bytes


# ─── save_goal_and_commit failure → rollback ──────────────────────────


def test_save_goal_commit_failure_rolls_back_yaml(
    base_path, center, monkeypatch,
):
    repo = center / "repo"
    _init_repo(repo)
    backend = SqliteCachedBackend(base_path, repo_root=repo)
    layout = YamlRepoLayout(repo)

    goal = {
        "goal_id": "g-fail-1",
        "name": "Failing goal",
        "description": "scope check",
        "author": "tester",
        "visibility": "public",
        "tags": [],
    }

    _patch_commit_to_fail(monkeypatch, error="goal commit fail")

    with pytest.raises(CommitFailedError):
        backend.save_goal_and_commit(
            goal,
            author="CI Bot <ci@example.invalid>",
            message="goals.propose: Failing goal",
        )

    goal_path = layout.goal_path("failing-goal")
    assert not goal_path.exists(), "goal YAML should be rolled back"

    pending = list_unreconciled_writes(base_path)
    assert len(pending) == 1
    assert pending[0]["helper_name"] == "save_goal_and_commit"


# ─── save_gate_claim_and_commit failure → rollback ────────────────────


def test_save_gate_claim_commit_failure_rolls_back(
    base_path, center, monkeypatch,
):
    from tinyassets.daemon_server import save_branch_definition, save_goal

    # Seed a branch and goal so claim_gate has real rows to reference.
    branch = _make_branch("Gate-fail branch")
    branch_saved = save_branch_definition(
        base_path, branch_def=branch.to_dict(),
    )
    goal_saved = save_goal(base_path, goal={
        "goal_id": "g-gate-fail",
        "name": "Gate fail goal",
        "description": "",
        "author": "tester",
        "visibility": "public",
        "tags": [],
        "gate_ladder": [{"key": "peer_reviewed", "label": "Peer reviewed"}],
    })

    repo = center / "repo"
    _init_repo(repo)
    backend = SqliteCachedBackend(base_path, repo_root=repo)
    layout = YamlRepoLayout(repo)
    claim_path = layout.gate_claim_path(
        "gate-fail-goal", "gate-fail-branch", "peer_reviewed",
    )

    _patch_commit_to_fail(monkeypatch, error="gate commit fail")

    with pytest.raises(CommitFailedError):
        backend.save_gate_claim_and_commit(
            branch_def_id=branch_saved["branch_def_id"],
            goal_id=goal_saved["goal_id"],
            rung_key="peer_reviewed",
            evidence_url="https://example.invalid/evidence",
            evidence_note="",
            claimed_by="tester",
            goal_slug="gate-fail-goal",
            branch_slug="gate-fail-branch",
            author="CI Bot <ci@example.invalid>",
            message="gates.claim: gate-fail-goal/gate-fail-branch@peer_reviewed",
        )

    assert not claim_path.exists(), "claim YAML should be rolled back"

    pending = list_unreconciled_writes(base_path)
    assert len(pending) == 1
    assert pending[0]["helper_name"] == "save_gate_claim_and_commit"

    # SQLite claim row retained.
    from tinyassets.daemon_server import list_gate_claims
    rows = list_gate_claims(
        base_path, branch_def_id=branch_saved["branch_def_id"],
    )
    assert any(r["rung_key"] == "peer_reviewed" for r in rows)


# ─── retract_gate_claim_and_commit failure → rollback ─────────────────


def test_retract_gate_claim_commit_failure_rolls_back(
    base_path, center, monkeypatch,
):
    from tinyassets.daemon_server import save_branch_definition, save_goal

    branch = _make_branch("Retract-fail branch")
    branch_saved = save_branch_definition(
        base_path, branch_def=branch.to_dict(),
    )
    goal_saved = save_goal(base_path, goal={
        "goal_id": "g-retract-fail",
        "name": "Retract fail goal",
        "description": "",
        "author": "tester",
        "visibility": "public",
        "tags": [],
        "gate_ladder": [{"key": "peer_reviewed", "label": "Peer reviewed"}],
    })

    repo = center / "repo"
    _init_repo(repo)
    backend = SqliteCachedBackend(base_path, repo_root=repo)
    layout = YamlRepoLayout(repo)

    # First, claim succeeds normally (real git).
    backend.save_gate_claim_and_commit(
        branch_def_id=branch_saved["branch_def_id"],
        goal_id=goal_saved["goal_id"],
        rung_key="peer_reviewed",
        evidence_url="https://example.invalid/evidence",
        evidence_note="",
        claimed_by="tester",
        goal_slug="retract-fail-goal",
        branch_slug="retract-fail-branch",
        author="CI Bot <ci@example.invalid>",
        message="gates.claim: initial",
    )
    claim_path = layout.gate_claim_path(
        "retract-fail-goal", "retract-fail-branch", "peer_reviewed",
    )
    prior_bytes = claim_path.read_bytes()

    # Now force retract to fail.
    _patch_commit_to_fail(monkeypatch, error="retract commit fail")

    with pytest.raises(CommitFailedError):
        backend.retract_gate_claim_and_commit(
            branch_def_id=branch_saved["branch_def_id"],
            rung_key="peer_reviewed",
            reason="test rollback",
            goal_slug="retract-fail-goal",
            branch_slug="retract-fail-branch",
            author="CI Bot <ci@example.invalid>",
            message="gates.retract: fail-test",
        )

    # YAML restored to pre-retract content.
    assert claim_path.read_bytes() == prior_bytes

    pending = list_unreconciled_writes(base_path)
    assert len(pending) == 1
    assert pending[0]["helper_name"] == "retract_gate_claim_and_commit"


# ─── sanity: list_unreconciled_writes ordering ────────────────────────


def test_list_unreconciled_writes_returns_most_recent_first(
    base_path, center, monkeypatch,
):
    repo = center / "repo"
    _init_repo(repo)
    backend = SqliteCachedBackend(base_path, repo_root=repo)

    _patch_commit_to_fail(monkeypatch, error="fail-1")
    with pytest.raises(CommitFailedError):
        backend.save_branch_and_commit(
            _make_branch("First fail"),
            author="CI Bot <ci@example.invalid>",
            message="branches.create_branch: one",
        )

    _patch_commit_to_fail(monkeypatch, error="fail-2")
    with pytest.raises(CommitFailedError):
        backend.save_branch_and_commit(
            _make_branch("Second fail"),
            author="CI Bot <ci@example.invalid>",
            message="branches.create_branch: two",
        )

    pending = list_unreconciled_writes(base_path)
    assert len(pending) == 2
    # Most recent first (DESC by id).
    assert pending[0]["git_error"] == "fail-2"
    assert pending[1]["git_error"] == "fail-1"

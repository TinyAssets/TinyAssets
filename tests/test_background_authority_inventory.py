from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_background_authority_inventory as inventory
from scripts.check_background_authority_inventory import (
    CANONICAL_READ_INTERFACES,
    EXPECTED_SENSITIVE_CALL_SITES,
    REQUIRED_BACKGROUND_ROOTS,
    SENSITIVE_EXECUTION_CALLS,
    CallSite,
    collect_sensitive_call_sites,
    compare_call_sites,
    scan_python_calls,
    validate_inventory,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_current_repository_matches_reviewed_background_authority_inventory() -> None:
    assert validate_inventory(REPO_ROOT) == []


def test_scanner_detects_a_new_sensitive_execution_call(tmp_path: Path) -> None:
    source = tmp_path / "new_root.py"
    source.write_text(
        "def newly_added_background_root():\n    return execute_branch_async('branch-id')\n",
        encoding="utf-8",
    )

    assert scan_python_calls(source, SENSITIVE_EXECUTION_CALLS, relative_to=tmp_path) == {
        CallSite(
            path="new_root.py",
            function="newly_added_background_root",
            callee="execute_branch_async",
        )
    }


def test_scanner_counts_duplicate_calls_in_one_function(tmp_path: Path) -> None:
    source = tmp_path / "duplicate_root.py"
    source.write_text(
        "def duplicated_background_root():\n"
        "    execute_branch('first')\n"
        "    return execute_branch('second')\n",
        encoding="utf-8",
    )

    assert scan_python_calls(source, SENSITIVE_EXECUTION_CALLS, relative_to=tmp_path) == {
        CallSite(
            path="duplicate_root.py",
            function="duplicated_background_root",
            callee="execute_branch",
            count=2,
        )
    }


def test_scanner_resolves_imported_sensitive_alias(tmp_path: Path) -> None:
    source = tmp_path / "aliased_root.py"
    source.write_text(
        "from tinyassets.runs import execute_branch as run_now\n\n"
        "def aliased_background_root():\n"
        "    return run_now('branch-id')\n",
        encoding="utf-8",
    )

    assert scan_python_calls(source, SENSITIVE_EXECUTION_CALLS, relative_to=tmp_path) == {
        CallSite(
            path="aliased_root.py",
            function="aliased_background_root",
            callee="execute_branch",
        )
    }


def test_scanner_resolves_assigned_sensitive_alias(tmp_path: Path) -> None:
    source = tmp_path / "assigned_alias_root.py"
    source.write_text(
        "import tinyassets.runs\n\n"
        "run_now = tinyassets.runs.execute_branch\n\n"
        "def aliased_background_root():\n"
        "    return run_now('branch-id')\n",
        encoding="utf-8",
    )

    assert scan_python_calls(source, SENSITIVE_EXECUTION_CALLS, relative_to=tmp_path) == {
        CallSite(
            path="assigned_alias_root.py",
            function="aliased_background_root",
            callee="execute_branch",
        )
    }


def test_repository_scan_detects_an_unreviewed_root(tmp_path: Path) -> None:
    package = tmp_path / "tinyassets"
    package.mkdir()
    source = package / "new_root.py"
    source.write_text(
        "def newly_added_background_root():\n    return execute_branch_async('branch-id')\n",
        encoding="utf-8",
    )

    observed = collect_sensitive_call_sites(tmp_path, scan_roots=("tinyassets",))
    assert observed == {
        CallSite(
            path="tinyassets/new_root.py",
            function="newly_added_background_root",
            callee="execute_branch_async",
        )
    }
    assert compare_call_sites(observed, ()) == [
        "unreviewed sensitive callsite: "
        "CallSite(path='tinyassets/new_root.py', "
        "function='newly_added_background_root', "
        "callee='execute_branch_async', count=1)"
    ]


def test_inventory_covers_every_required_source_family() -> None:
    assert set(REQUIRED_BACKGROUND_ROOTS) == {
        "activities",
        "schedule_and_event",
        "goal_subscription",
        "soul_and_compiled_cycle",
        "request_admission",
        "goal_pool_and_paid_market",
        "branch_task_and_graph_enqueue",
        "direct_live_and_versioned_run",
        "resume_and_recovery",
        "request_actor_boundary",
        "daemon_cloud_and_distributed_worker",
        "selector_leaderboard_and_market_delegate",
        "retired_wiki_forwarding",
    }
    assert all(REQUIRED_BACKGROUND_ROOTS.values())


def test_inventory_records_canonical_read_interfaces_without_new_truth() -> None:
    # Three owners (`paid_market_acceptance`, `provider_work`, `provider_attempt`)
    # were dropped 2026-08-26: each held ONLY a reference into an archived
    # OpenSpec change, which is unfalsifiable -- archives are frozen, so the
    # marker can never stop matching while the canonical requirement is free
    # to change. The checker now refuses such references outright.
    assert set(CANONICAL_READ_INTERFACES) == {
        "identity",
        "acl",
        "branch",
        "branch_write_author",
        "daemon",
        "run",
        "request_admission",
        "filing_only_wiki_negative",
        "goal_subscription",
        "queue",
        "b2",
    }
    assert all(CANONICAL_READ_INTERFACES.values())

    assert {ref.marker for ref in CANONICAL_READ_INTERFACES["branch"]} == {
        "def _resolve_readable_branch(",
        "def _resolve_readable_version(",
    }
    assert {ref.marker for ref in CANONICAL_READ_INTERFACES["daemon"]} == {
        "def get_daemon(",
        "def list_runtime_instances(",
    }
    assert any(
        ref.marker == "class RecordVerifier:" and ref.state == "contract-only"
        for ref in CANONICAL_READ_INTERFACES["b2"]
    )


def test_inventory_closes_indirect_and_packaged_execution_boundaries() -> None:
    observed = set(EXPECTED_SENSITIVE_CALL_SITES)
    assert (
        CallSite(
            "fantasy_daemon/__main__.py",
            "DaemonController._run_graph",
            # Qualified since 2026-08-26: `stream` alone could not tell a
            # compiled-graph run from an HTTP `request.stream()` body read, so
            # registering the body reads as reviewed would have blinded the
            # checker to a real graph stream added beside them.
            "compiled.stream",
        )
        in observed
    )
    assert (
        CallSite(
            "packaging/claude-plugin/plugins/tinyassets-universe-server/"
            "runtime/tinyassets/graph_compiler.py",
            "_build_invoke_branch_node._node_fn",
            "execute_branch",
        )
        in observed
    )


def test_sensitive_call_manifest_is_nonempty_and_duplicate_free() -> None:
    assert EXPECTED_SENSITIVE_CALL_SITES
    assert len(EXPECTED_SENSITIVE_CALL_SITES) == len(set(EXPECTED_SENSITIVE_CALL_SITES))


@pytest.mark.parametrize("path", [
    "tinyassets/activity_runner.py",
    "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/activity_runner.py",
])
def test_activity_registration_refuses_an_extra_launch(tmp_path: Path, path: str) -> None:
    expected = {call for call in EXPECTED_SENSITIVE_CALL_SITES if call.path == path}
    assert expected == {CallSite(path, "start", "execute_branch_async")}
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    original = (REPO_ROOT / path).read_text(encoding="utf-8")
    source.write_text(original, encoding="utf-8")
    assert scan_python_calls(source, SENSITIVE_EXECUTION_CALLS, relative_to=tmp_path) == expected
    # A second call in the registered function must not inherit its registration.
    source.write_text(original.replace(
        "    outcome = execute_branch_async(",
        "    execute_branch_async(base_path, branch=branch)\n"
        "        outcome = execute_branch_async(",
        1,
    ), encoding="utf-8")
    observed = scan_python_calls(source, SENSITIVE_EXECUTION_CALLS, relative_to=tmp_path)
    assert CallSite(path, "start", "execute_branch_async", count=2) in observed
    assert any("unreviewed sensitive callsite" in error
               for error in compare_call_sites(observed, expected))


@pytest.mark.parametrize(("guard", "replacement"), [
    ("_bind_automation_provider_call(base_path, who)", "None"),
    ("owner_run_identity(base_path, universe_id, owner)", "unrelated_context()"),
    ("if not bound:", "if False:"),
    ("owner_user_id=owner,", "owner_user_id=None,"),
    ("on_node_status=_authority_guard(base_path, who),", "on_node_status=None,"),
    ('activities.bind_run(universe_dir, record["activity_id"], generation, run_id)', "True"),
    ("stop(base_path, run_id)", "pass"),
    ("activities.activity_for_run(universe_dir, run_id)", "None"),
    ("raise PermissionError(\n                \"activity_run_unlinked:",
     "raise RuntimeError(\n                \"activity_run_unlinked:"),
    ("activity_runner.linked_activity(", "activity_runner.unrelated_lookup("),
    ("activity_runner.is_activities_branch(", "activity_runner.unrelated_branch_check("),
])
def test_activity_guard_removal_fails_inventory(
    tmp_path: Path, monkeypatch, guard: str, replacement: str,
) -> None:
    references = REQUIRED_BACKGROUND_ROOTS["activities"]
    paths = {ref.path for ref in references} | set(inventory._WIKI_NEGATIVE_PATHS)
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO_ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")
    # Isolate this source family in the fixture; retain the real AST scanner and
    # source-marker validator. The repository-wide test above checks every root.
    monkeypatch.setattr(inventory, "REQUIRED_BACKGROUND_ROOTS", {"activities": references})
    monkeypatch.setattr(inventory, "CANONICAL_READ_INTERFACES", {})
    monkeypatch.setattr(inventory, "EXPECTED_SENSITIVE_CALL_SITES",
                        tuple(collect_sensitive_call_sites(tmp_path)))
    assert validate_inventory(tmp_path) == []
    matching = [tmp_path / path for path in paths
                if guard in (tmp_path / path).read_text(encoding="utf-8")]
    assert len(matching) == 1
    target = matching[0]
    target.write_text(target.read_text(encoding="utf-8").replace(guard, replacement, 1),
                      encoding="utf-8")
    assert any("activities missing marker" in error for error in validate_inventory(tmp_path))

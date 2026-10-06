"""Run repository-wide structural guards before queue admission.

Run with ``python -m scripts.ci_structural_guards``. No quarantine, selection,
or skip allowance: these checks must execute on every PR, including docs-only
changes. Repair advice is attached to setup, collection and assertion failures.
"""
from __future__ import annotations

import sys

import pytest

# Keep existing tests intact. Phase 2 may generate inventories; Phase 1 only
# brings their existing checks forward and makes their failures actionable.
GUARDS = {
    'test_execution_authority_import_boundary': (
        'Remove the reported operational adapter import from the authority core; pass '
        'facts through the boundary instead. '
    ),
    'test_fastmcp_pin_matches_runtime': (
        'Align the FastMCP constraint in pyproject.toml and requirements.txt with the '
        'installed runtime and regenerate the plugin mirror. '
    ),
    'test_no_platform_github_push_credential': (
        'Remove the reported platform GitHub push credential reader, deployment input or '
        'workflow; use owner-bound connection credentials. '
    ),
    'test_patch_thread_safety_invariant': (
        'Move mock.patch or monkeypatch setup outside the thread target and keep the '
        'patch active until the worker joins. '
    ),
    'test_owner_stores': (
        'Classify reported SQLite writers in tinyassets/owner_stores.py; fence owner '
        'writes and remove stale inventory entries. '
    ),
    'test_channel_agnostic_ratchet': (
        'Replace the reported channel-specific substrate branch with generic behavior '
        'driven by manifests; keep channel names in documentation only. '
    ),
    'test_mcp_instruction_surfaces': (
        'Correct the reported served instruction or route example to use a live '
        'advertised handle and its supported parameter names. '
    ),
    "test_real_browser_proof_workflow": (
        'Update .github/workflows/real-browser-proof.yml: remove deleted literal paths and '
        'add every real_browser test file to pull_request.paths; preserve the oracle and '
        'no-skip assertion steps. '
    ),
    "test_real_browser_import_guard": (
        'Move Playwright imports inside the fixture or test that uses them, after '
        'collection; mark browser proofs real_browser. '
    ),
    "test_storage_registry_complete": (
        'Classify each reported name in tinyassets/storage_accounting.py ROOT_ENTRIES, '
        'UNIVERSE_ENTRIES or ELSEWHERE_ENTRIES; ROOT_ENTRIES must name a registered STORES '
        'entry or explain platform ownership. '
    ),
    "test_storage_registry": (
        'Repair the storage registration in tinyassets/storage_accounting.py so each store '
        'has its real accounting implementation and ownership classification. '
    ),
    "test_control_plane_inventory": (
        'Update tests/control_plane_timer_inventory.py SITES for the reported timer '
        'callsites, with their authority class and reason; remove stale sites and keep '
        'agent work out of platform timers. '
    ),
    "test_background_authority_inventory": (
        'Update the callsite/source-family inventory in '
        'scripts/check_background_authority_inventory.py to match reviewed authority '
        'owners; run python scripts/check_background_authority_inventory.py for the exact '
        'differences. '
    ),
    "test_served_tool_guidance": (
        'Repair the affected handbook chapter and its index in '
        'tinyassets/engine_mcp_server.py; preserve verbatim guidance and keep expanded '
        'instructions in on-demand chapters. '
    ),
    "test_served_systems_guidance": (
        'Repair the systems handbook chapter in tinyassets/engine_mcp_server.py to name the '
        'actual supported in-platform calls and fetch path. '
    ),
    "test_owner_door_import_boundary": (
        'Remove model-door bounding/projection imports from the reported owner/shared-read '
        'module; keep those imports in model-door modules and read account type through the '
        'billing owner. '
    ),
    "test_app_reads_use_owner_door": (
        'Route app reads through /app/api/read or /app/api/status in the owner client; '
        'preserve paging, account-switch fencing and visible read errors. '
    ),
    "test_onboarding_app": (
        'Repair onboarding.onboarding_routes() and its app path constants to match the '
        'asserted public /app routes; remove retired /mcp/app mounts. '
    ),
    "test_concerns_index_matches_the_directory": (
        'Give each docs/concerns file YAML front matter with severity, title, '
        'filed YYYY-MM-DD and summary; use python scripts/concerns_index.py to render the index '
        'instead of maintaining a README table. '
    ),
    "test_universe_path_io_guard": (
        'Replace the reported raw filesystem operation with the scoped universe file API; '
        "remove obsolete entries from the guard's grandfathered pin when their raw I/O "
        'disappears. '
    ),
    "test_engine_secret_inventory": (
        'Classify the reported compose credential and its required engine reason in '
        'tinyassets/platform_secrets.py; keep daemon-only credentials out of engine launch. '
    ),
    "test_live_docs_reference_real_scripts": (
        'Correct each reported documentation command to reference an existing script, or '
        'remove the obsolete command from the live doc. '
    ),
    "test_rulebook_ratchet": (
        'Shorten the changed always-loaded rule files; move task-specific guidance to an '
        'on-demand handbook or skill and run python scripts/check_context_budget.py. '
    ),
    "test_tests_workflow": (
        'Restore the reported trigger, dependency, shard or fail-closed property in '
        '.github/workflows/tests.yml; run actionlint .github/workflows/tests.yml. '
    ),
    "test_linux_jail_proof_workflow": (
        'Repair .github/workflows/linux-jail-proof.yml to retain the Linux oracle and '
        'assert every real_jail case executed without skips. '
    ),
}

# Only route contracts from this large mixed UI/behaviour file belong in the
# fast stage. The affected runner continues to execute its behavioural cases.
ROUTE_CASES = (
    "test_route_is_apex_app_get",
    "test_no_route_is_mounted_under_the_retired_mcp_app_prefix",
    "test_the_app_path_constant_is_the_single_source_of_truth",
)


def targets() -> list[str]:
    return [
        "tests/test_converse_turn_cost.py::test_engine_tool_description_budget_does_not_grow",
        "tests/test_converse_turn_cost.py::test_static_folder_harness_budget_does_not_grow",
    ] + [f"tests/{name}.py" for name in GUARDS if name != "test_onboarding_app"] + [
        f"tests/test_onboarding_app.py::{name}" for name in ROUTE_CASES
    ]


def repair(nodeid: str) -> str:
    name = nodeid.split("::", 1)[0].replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py")
    if name == "test_converse_turn_cost":
        return (
            "Fix: Move detailed agent instructions verbatim into an on-demand handbook "
            "chapter, leaving a short pointer; do not raise the static prompt budgets. "
            f"Reproduce: python -m pytest -q {nodeid}"
        )
    hint = GUARDS.get(
        name, "Install dev,browser extras and fix the reported collection/import error."
    )
    return f"Fix: {hint} Reproduce: python -m pytest -q {nodeid}"


class GuardResults:
    def __init__(self) -> None:
        self.problems: set[str] = set()

    def _record(self, report) -> None:
        if report.failed or report.skipped:
            self.problems.add(report.nodeid)
            report.sections.append(("Structural guard repair", repair(report.nodeid)))

    def pytest_runtest_logreport(self, report) -> None:
        self._record(report)

    def pytest_collectreport(self, report) -> None:
        self._record(report)

    def pytest_terminal_summary(self, terminalreporter) -> None:
        for nodeid in sorted(self.problems):
            terminalreporter.write_line(f"{nodeid}: {repair(nodeid)}")


def main() -> int:
    results = GuardResults()
    code = pytest.main(["-q", "--tb=short", "--durations=10", *targets()], plugins=[results])
    return int(code) or int(bool(results.problems))


if __name__ == "__main__":
    sys.exit(main())

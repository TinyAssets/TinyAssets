"""The oracle's posture must stay equal to what production actually runs.

`scripts/role_image_oracle.py` proves the cutover image by booting it in
`deploy/compose.yml`'s posture and migrating a volume with the runbook's own
command. Both of those are copied into the script as constants, so they can
drift from the files they came from without anything failing — and a posture
that has drifted proves the wrong thing while still printing PASS. These checks
are that pairing, plus the internal invariants of the leg table.

Nothing here runs docker: the kernel behaviour is the oracle's own job.
"""
from __future__ import annotations

import ast
import runpy
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ORACLE_PATH = REPO / "scripts" / "role_image_oracle.py"
ORACLE = runpy.run_path(str(ORACLE_PATH))
DAEMON = yaml.safe_load((REPO / "deploy" / "compose.yml").read_text(encoding="utf-8"))[
    "services"]["daemon"]
RUNBOOK = (REPO / "docs" / "ops" / "owner-split-cutover-runbook.md").read_text(encoding="utf-8")


def test_the_serving_posture_is_the_one_compose_declares():
    assert ORACLE["COMPOSE_USER"] == DAEMON["user"]
    assert list(ORACLE["COMPOSE_CAPS"]) == DAEMON["cap_add"]
    assert DAEMON["cap_drop"] == ["ALL"]
    assert sorted(ORACLE["COMPOSE_SECURITY"]) == sorted(DAEMON["security_opt"])


def test_the_serving_posture_holds_none_of_the_migration_capabilities():
    assert not set(ORACLE["COMPOSE_CAPS"]) & set(ORACLE["MIGRATION_CAPS"])
    launcher = runpy.run_path(str(REPO / "deploy" / "role_launcher.py"))
    names = {"KILL": 5, "SETGID": 6, "SETUID": 7, "SETPCAP": 8}
    assert launcher["ENTRY_CAPS"] == sum(
        1 << names[capability] for capability in ORACLE["COMPOSE_CAPS"])


def test_the_migration_posture_is_the_one_the_runbook_publishes():
    step = RUNBOOK.split("## 4. Migrate", 1)[1].split("## 5.", 1)[0]
    for capability in ORACLE["MIGRATION_CAPS"]:
        assert f"--cap-add {capability}" in step, capability
    assert step.count("--cap-add") == len(ORACLE["MIGRATION_CAPS"])
    assert "/usr/local/libexec/ta-migrate.py" in step
    assert "--cap-drop ALL" in step and "--network none" in step


def test_the_healthcheck_the_oracle_asserts_is_the_one_compose_runs():
    assert DAEMON["healthcheck"]["test"] == ["CMD", "/usr/local/libexec/ta-op", "pulse"]


@pytest.mark.parametrize("name", ORACLE["LEG_NAMES"])
def test_every_named_leg_exists_in_the_container_program(name):
    program = ast.parse(ORACLE["CELLS"])
    defined = {node.name for node in program.body if isinstance(node, ast.FunctionDef)}
    assert f"leg_{name}" in defined


def test_the_container_programs_leg_table_is_exactly_the_named_set():
    program = ast.parse(ORACLE["CELLS"])
    table = next(node for node in program.body
                 if isinstance(node, ast.Assign)
                 and getattr(node.targets[0], "id", "") == "LEGS")
    assert [key.value for key in table.value.keys] == list(ORACLE["LEG_NAMES"])


def test_a_blocked_leg_names_a_live_concern_and_is_not_in_the_default_set():
    """A leg a defect blocks is excluded loudly, never silently (Fact 8)."""
    for name, concern in ORACLE["BLOCKED_LEGS"].items():
        assert name in ORACLE["LEG_NAMES"]
        assert name not in ORACLE["DEFAULT_LEGS"]
        assert (REPO / concern).is_file(), (
            f"leg {name} points at {concern}, which no longer exists: either the "
            "defect is fixed and the leg belongs in DEFAULT_LEGS, or the pointer "
            "is stale")
    assert set(ORACLE["DEFAULT_LEGS"]) | set(ORACLE["BLOCKED_LEGS"]) == set(
        ORACLE["LEG_NAMES"])


def test_the_oracle_reuses_the_probes_it_is_built_on():
    """Task 2.5 reuses the PR1 fixture and builder C's probe; it never forks them."""
    source = ORACLE_PATH.read_text(encoding="utf-8")
    assert "/src/scripts/role_migrate_probe.py" in source
    assert "role_provider_cell_probe.py" in source
    for name in ("role_migrate_probe.py", "role_provider_cell_probe.py"):
        assert (REPO / "scripts" / name).is_file(), name

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
import re
import runpy
import subprocess
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
    assert ORACLE['COMPOSE_MEMORY'] == DAEMON['mem_limit'] == DAEMON['memswap_limit']


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


@pytest.mark.parametrize('document,expected', [
    ({'tools': [{'name': 'mcp__engine__write'}]}, ['mcp__engine__write']),
    ({'input': [{'type': 'additional_tools', 'tools': [
        {'type': 'namespace', 'name': 'functions', 'tools': [
            {'type': 'function', 'name': 'write'}, {'type': 'function', 'name': 'read'}]}]}]},
     ['write', 'read']),
    ({'input': [{'type': 'message', 'content': 'auxiliary request'}]}, []),
])
def test_chat_acceptance_reads_both_vendor_tool_envelopes(document, expected):
    probe = runpy.run_path(str(REPO / 'scripts/role_chat_probe.py'))
    assert probe['advertised_tools'](document) == expected


def test_runbook_start_dispatches_only_after_enabling(tmp_path):
    start = RUNBOOK.split('## 5. Start', 1)[1].split('## 6.', 1)[0]
    blocks = re.findall(r'```sh\n(.*?)```', start, re.S)
    assert blocks, 'start has no executable enable/dispatch sequence'
    command = '\n'.join(blocks)
    # Execute the documented commands against a workflow-state double. It
    # refuses dispatch while disabled, checks the merge SHA and counts runs.
    script = '''
set -e
CUTOVER_SHA=abcdef1234567890
enabled=0
dispatched=0
gh() {
  case "$1 $2" in
    'api repos/TinyAssets/TinyAssets/commits/main') echo "$CUTOVER_SHA" ;;
    'workflow enable') enabled=1 ;;
    'workflow run') test "$enabled" = 1; dispatched=$((dispatched + 1));
      test "$*" = 'workflow run deploy-prod.yml --ref main -f image_tag=abcdef123456' ;;
    'run list') : ;;
    *) return 1 ;;
  esac
}
''' + command + '\ntest "$dispatched" = 1\n'
    result = subprocess.run(['bash', '-s'], input=script.encode(), cwd=tmp_path,
                            capture_output=True)
    assert result.returncode == 0, result.stderr


def test_runbook_checksum_uses_archive_directory(tmp_path):
    import hashlib
    backup = tmp_path / 'backups'
    backup.mkdir()
    (backup / 'fixture.tar.gz').write_bytes(b'archive')
    (backup / 'fixture.tar.gz.sha256').write_text(
        hashlib.sha256(b'archive').hexdigest() + '  fixture.tar.gz\n')
    rollback = RUNBOOK.split('## 7. Rollback', 1)[1]
    line = next(line.strip() for line in rollback.splitlines() if '<id>.sha256' in line)
    line = line.replace('/var/backups/tinyassets', 'backups')
    line = line.replace('<id>', 'fixture.tar.gz')
    result = subprocess.run(['bash', '-s'], input=line.encode(), cwd=tmp_path, capture_output=True)
    assert result.returncode == 0, result.stderr

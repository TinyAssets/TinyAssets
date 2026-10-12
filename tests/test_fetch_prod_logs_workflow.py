"""Fetch production logs is a read, on the deploy key, of one person's trail."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "fetch-prod-logs.yml"
INSTALL = ROOT / ".github" / "workflows" / "install-host-services.yml"


def test_fetch_workflow_is_read_only_and_queries_a_trail():
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert workflow["name"] == "Fetch production logs"
    assert "DO_SSH_KEY" in text
    assert "DO_LOG_READ_SSH_KEY" not in text
    assert "ssh-keygen" not in text
    assert "production-host-observation" in text
    assert "production-host-mutation" not in text
    assert "systemctl" not in text
    assert "docker restart" not in text
    assert "bash -s" not in text
    assert "sh -c" not in text
    assert "upload-artifact" in text
    assert "actions/checkout" in text
    triggers = workflow.get(True) or workflow.get("on") or {}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert {"since", "until", "session_id", "account_id", "trace_id", "grep"} <= set(inputs)
    assert triggers["repository_dispatch"]["types"] == ["fetch-prod-logs"]
    assert workflow["permissions"] == {"contents": "read"}
    joined = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["fetch"]["steps"]
    )
    assert "read-prod-logs.py" in joined
    assert "python3" in joined
    assert "read-logs" in joined
    assert "printf '%q '" in joined
    assert "--session" in joined
    assert "--account" in joined
    assert "toJson(github.event)" in text
    fetch = next(
        item for item in workflow["jobs"]["fetch"]["steps"]
        if item.get("name") == "Fetch the timeline"
    )
    assert "deploy/read-prod-logs.py" in fetch["run"]
    assert 'case "${remote}" in' in fetch["run"]


def test_install_step_is_the_forced_command_inside_the_host_lock():
    workflow = yaml.safe_load(INSTALL.read_text(encoding="utf-8"))
    step = next(
        item for item in workflow["jobs"]["install"]["steps"]
        if item.get("name") == "Install read-only log reader"
    )
    run = step["run"]
    assert run.count("guard-host-mutation") == 1
    assert "--command-timeout" in run
    assert "/usr/local/libexec/tinyassets-read-logs" not in run
    assert "install-log-reader.py" in run
    assert "read-prod-logs.py" in run
    assert "DO_LOG_READ_SSH_PUBKEY" in yaml.safe_dump(step)

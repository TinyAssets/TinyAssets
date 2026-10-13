"""Fetch production logs is a read, on the deploy key, of one person's trail."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["fetch"]["steps"]


def test_only_the_sealed_timeline_leaves_the_runner():
    steps = _steps()
    names = [step.get("name", "") for step in steps]
    uploads = [step for step in steps if "upload-artifact" in str(step.get("uses", ""))]
    assert len(uploads) == 1
    assert uploads[0]["with"]["path"].endswith("production-log-timeline.enc")
    assert "prod-logs.jsonl" not in yaml.safe_dump(uploads[0])
    joined = "\n".join(step.get("run", "") for step in steps)
    assert "review_evidence.py validate-recipient" in joined
    assert "review_evidence.py encrypt" in joined
    assert "cryptography==" in joined
    assert "set -x" not in joined
    recipient = names.index("Reject a missing or placeholder recipient")
    seal = names.index("Seal the timeline to the recipient key")
    assert recipient < names.index("Fetch the timeline") < seal
    assert seal < names.index("Upload the sealed timeline")
    fetch = next(step for step in steps if step.get("name") == "Fetch the timeline")
    assert "cat prod-logs.summary" not in fetch["run"]
    assert 'allowed = ("errors_by_commit", "lines", "truncated")' in fetch["run"]
    assert '> "${RUNNER_TEMP}/timeline/prod-logs.jsonl"' in fetch["run"]


def _resolve(payload: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    step = next(step for step in _steps() if step.get("name") == "Resolve the log query")
    script = step["run"].split("python3 - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    env = {
        **os.environ,
        "EVENT_JSON": json.dumps({"inputs": payload}),
        "EVENT_NAME": "workflow_dispatch",
        "GITHUB_ENV": str(tmp_path / "github.env"),
    }
    return subprocess.run(
        [sys.executable, "-I", "-"], input=script, text=True, capture_output=True, env=env,
    )


def test_an_unfiltered_window_is_within_one_hour(tmp_path):
    base = {"since": "2026-10-12T17:00:00Z"}
    wide = _resolve({**base, "until": "2026-10-12T18:00:01Z"}, tmp_path)
    assert wide.returncode != 0
    assert "1 hour" in wide.stderr
    assert _resolve({**base, "until": "2026-10-12T18:00:00Z"}, tmp_path).returncode == 0
    week = {**base, "until": "2026-10-19T17:00:00Z"}
    assert _resolve({**week, "session_id": "s_" + "a" * 32}, tmp_path).returncode == 0
    assert _resolve({**week, "account_id": "user_01ABC"}, tmp_path).returncode == 0
    assert _resolve({**week, "trace_id": "trace-0001"}, tmp_path).returncode == 0
    assert _resolve(week, tmp_path).returncode != 0

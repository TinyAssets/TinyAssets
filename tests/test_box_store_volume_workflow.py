"""Provisioning stays manual, bounded, and quiet in public Actions logs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/box-store-volume.yml"
SCRIPT = ROOT / "scripts/ensure_box_store_volume.py"
sys.path.insert(0, str(ROOT / "scripts"))


def _module():
    spec = importlib.util.spec_from_file_location("ensure_box_store_volume", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_is_main_only_manual_and_does_not_echo_secrets() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    assert workflow.get("on", workflow.get(True)) == {"workflow_dispatch": None}
    assert "paths:" not in text and "pull_request:" not in text and "push:" not in text
    job = workflow["jobs"]["attach"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert "env" not in job  # secret is scoped to the action step
    step = job["steps"][-1]
    assert step["env"]["DO_TOKEN"] == "${{ secrets.DIGITALOCEAN_TOKEN }}"
    assert "scripts/ensure_box_store_volume.py" in step["run"]
    assert "echo $DO_TOKEN" not in text and "set -x" not in text
    assert "DELETE" not in SCRIPT.read_text() and "detach" not in SCRIPT.read_text()


def test_volume_creation_attach_poll_and_idempotent_rerun() -> None:
    module = _module()
    calls: list[tuple[str, str, dict | None]] = []
    attached = False

    def request(*, method, url, token, data):
        nonlocal attached
        assert token == "private-token"
        path = url.removeprefix(module.API)
        payload = json.loads(data) if data else None
        calls.append((method, path, payload))
        volume = {"id": "volume-uuid", "name": module.VOLUME_NAME,
                  "region": {"slug": "sfo3"}, "size_gigabytes": 20,
                  "droplet_ids": [module.DROPLET_ID] if attached else []}
        if path == f"/droplets/{module.DROPLET_ID}":
            response = {"droplet": {"id": module.DROPLET_ID,
                                    "name": module.DROPLET_NAME, "region": {"slug": "sfo3"}}}
        elif path.startswith("/volumes?"):
            response = {"volumes": [volume] if any(c[1] == "/volumes" for c in calls) else []}
        elif path == "/volumes" and method == "POST":
            assert payload == {"name": module.VOLUME_NAME, "region": "sfo3",
                               "size_gigabytes": 20}
            response = {"volume": volume}
        elif path == "/volumes/volume-uuid/actions" and method == "POST":
            assert payload == {"type": "attach", "droplet_id": module.DROPLET_ID,
                               "region": "sfo3"}
            response = {"action": {"id": 42}}
        elif path == "/volumes/volume-uuid/actions/42":
            attached = True
            response = {"action": {"status": "completed"}}
        elif path == "/volumes/volume-uuid":
            response = {"volume": volume}
        else:
            raise AssertionError(path)
        return json.dumps(response).encode()

    assert module.ensure_volume("private-token", request=request, sleep=lambda _: None) == (
        "volume-uuid", 20, "attached")
    actions = len([c for c in calls if c[0] == "POST"])
    assert actions == 2
    assert module.ensure_volume("private-token", request=request, sleep=lambda _: None) == (
        "volume-uuid", 20, "attached")
    assert len([c for c in calls if c[0] == "POST"]) == actions


def test_volume_refuses_other_attachment() -> None:
    module = _module()

    def request(*, method, url, token, data):
        path = url.removeprefix(module.API)
        if path.startswith("/droplets/"):
            return json.dumps({"droplet": {"id": module.DROPLET_ID,
                "name": module.DROPLET_NAME, "region": {"slug": "sfo3"}}}).encode()
        return json.dumps({"volumes": [{"id": "other", "name": module.VOLUME_NAME,
            "region": {"slug": "sfo3"}, "size_gigabytes": 20,
            "droplet_ids": [123]}]}).encode()

    try:
        module.ensure_volume("private-token", request=request)
    except ValueError as error:
        assert "another droplet" in str(error)
    else:
        raise AssertionError("other attachment was accepted")

"""Engine inheritance checked against deployment inputs, not the deny list."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tinyassets.platform_secrets import ENGINE_REQUIRED_SECRET_ENV, child_env

ROOT = Path(__file__).resolve().parents[1]

# Only non-credential configuration belongs here. Every newly injected name
# requires classification, even if its spelling does not look like a secret.
_CONFIG = {
    "HOME": "runtime home",
    "TINYASSETS_DATA_DIR": "state root",
    "TINYASSETS_REPO_ROOT": "community-pool root",
    "TINYASSETS_IMAGE": "public image reference",
    "TINYASSETS_CLOUD_DAEMON_SUBSCRIPTION_ONLY": "legacy no-op flag",
    "TINYASSETS_GOAL_POOL": "feature flag",
    "TINYASSETS_AUTO_SHIP_RUBRIC_MODE": "gate mode",
    "TINYASSETS_AUTO_SHIP_TRAJECTORY_MODE": "gate mode",
    "TINYASSETS_MCP_CANARY_URL": "public endpoint",
    "TINYASSETS_ONBOARDING_APP": "feature flag",
    "TINYASSETS_ALLOW_CLAUDE_SERVING": "feature flag",
    "UNIVERSE_SERVER_AUTH": "auth mode, not credential material",
    "UNIVERSE_SERVER_DEV_USER": "local operator name",
    "BACKUP_DEST": "rclone remote name and path; credentials live in rclone config",
    "BACKUP_RETAIN_DAILY": "retention count",
    "BACKUP_RETAIN_WEEKLY": "retention count",
    "BACKUP_RETAIN_MONTHLY": "retention count",
}


def _deployment_inventory() -> set[str]:
    daemon = yaml.safe_load((ROOT / "deploy/compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]["daemon"]
    environment = daemon.get("environment", {})
    names = set(environment) if isinstance(environment, dict) else {
        entry.split("=", 1)[0] for entry in environment
    }
    for entry in daemon["env_file"]:
        deployed = Path(entry if isinstance(entry, str) else entry["path"]).name
        # daemon.env is rendered from the shared store. Include its full source
        # inventory: child_env must also defend against an unsplit parent.
        template_name = (
            "tinyassets-env.template" if deployed == "daemon.env"
            else deployed.removesuffix(".env") + "-env.template"
        )
        template = ROOT / "deploy" / template_name
        assert template.is_file(), f"Uninventoried daemon env_file: {deployed}"
        names.update(re.findall(
            r"(?m)^\s*(?:#\s*)?([A-Z][A-Z0-9_]+)=",
            template.read_text(encoding="utf-8"),
        ))
    return names


@pytest.mark.parametrize("name", sorted(_deployment_inventory() - _CONFIG.keys()))
def test_compose_injected_credential_requires_engine_reason(name):
    result = child_env({name: "deployment-placeholder"})
    if name in ENGINE_REQUIRED_SECRET_ENV:
        assert ENGINE_REQUIRED_SECRET_ENV[name].strip()
        assert result == {name: "deployment-placeholder"}
    else:
        assert result == {}, f"Unclassified credential inherited by engine: {name}"


def test_deployment_inventory_reaches_engine_launch(monkeypatch):
    from tinyassets import engine_mcp_http

    inventory = _deployment_inventory()
    for name in inventory:
        monkeypatch.setenv(name, "deployment-placeholder")
    captured = {}

    def capture(argv, **kwargs):
        captured.update(kwargs["env"])
        return object()

    monkeypatch.setattr(engine_mcp_http.subprocess, "Popen", capture)
    assert engine_mcp_http._EngineServer("u", "user:owner", 8790, "/data").start()
    assert (set(captured) & inventory) <= (_CONFIG.keys() | ENGINE_REQUIRED_SECRET_ENV.keys())
    assert captured["TINYASSETS_ENGINE_MCP_HTTP_SECRET"]
    assert captured["TINYASSETS_DATA_DIR"] == "/data"


def test_catalogued_daemon_credentials_are_in_deployment_inventory():
    catalog = (ROOT / "docs/reference/environment-variables.md").read_text(encoding="utf-8")
    scope = catalog.split("## Credential scope at engine launch\n", 1)[1].split("\n## ", 1)[0]
    names = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)`", scope, re.M))
    assert names
    assert names <= _deployment_inventory()
    assert set(ENGINE_REQUIRED_SECRET_ENV) <= names


def test_daemon_auth_inventory_has_only_the_audited_readers():
    # This records the grep proof independently of CHILD_FORBIDDEN_ENV. Importing
    # a module is not a secret read: only admission minting / interchange import
    # consume the HMAC keys; the engine exposes neither operation.
    expected = {
        "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY": {
            "api/universe.py", "storage/request_admissions.py", "agent_interchange.py",
        },
        "TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY": {"agent_interchange.py"},
        "TINYASSETS_APP_INGRESS_HMAC_KEY": set(),
        "TINYASSETS_WIKI_CANARY_TOKEN": {"auth/wiki_canary.py"},
        "TINYASSETS_SESSION_SEAL_KEY": {"onboarding/session_store.py", "universe_server.py"},
    }
    for name, readers in expected.items():
        found = {
            path.relative_to(ROOT / "tinyassets").as_posix()
            for path in (ROOT / "tinyassets").rglob("*.py")
            if path.name != "platform_secrets.py" and name in path.read_text(encoding="utf-8")
        }
        assert found == readers, name

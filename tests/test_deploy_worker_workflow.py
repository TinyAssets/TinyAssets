"""Tests for .github/workflows/deploy-worker.yml and wrangler.toml correctness.

Covers:
  (a) YAML parses without error
  (b) deploy-worker.yml has expected structure (jobs, triggers, path filter)
  (c) wrangler.toml name matches the actual deployed Worker
  (d) dry-run condition fires on pull_request; live deploy fires on push
  (e) required secret names are referenced
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "deploy-worker.yml"
_WRANGLER = _REPO / "deploy" / "cloudflare-worker" / "wrangler.toml"

pytestmark = pytest.mark.skipif(
    not _YAML_AVAILABLE, reason="pyyaml not installed"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    # PyYAML parses the YAML `on:` key as Python bool True (YAML 1.1 spec).
    return wf.get(True, {}) or {}


def _load_wrangler_text() -> str:
    return _WRANGLER.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) YAML parses without error
# ---------------------------------------------------------------------------




def test_all_workflow_ymls_parse():
    """All workflows in .github/workflows/ must parse as valid YAML."""
    workflows_dir = _REPO / ".github" / "workflows"
    for path in workflows_dir.glob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name}: expected dict at top level"


# ---------------------------------------------------------------------------
# (b) TinyAssets structure
# ---------------------------------------------------------------------------


def test_workflow_has_push_trigger_on_main():
    wf = _load_workflow()
    triggers = _triggers(wf)
    assert "push" in triggers
    push = triggers["push"]
    assert "main" in push.get("branches", [])


def test_workflow_has_pull_request_trigger():
    wf = _load_workflow()
    assert "pull_request" in _triggers(wf)


def test_workflow_path_filter_covers_worker_dir():
    wf = _load_workflow()
    triggers = _triggers(wf)
    push_paths = (triggers.get("push") or {}).get("paths", [])
    pr_paths = (triggers.get("pull_request") or {}).get("paths", [])
    assert "deploy/cloudflare-worker/**" in push_paths, \
        "push trigger must path-filter on deploy/cloudflare-worker/**"
    assert "deploy/cloudflare-worker/**" in pr_paths, \
        "pull_request trigger must path-filter on deploy/cloudflare-worker/**"


def test_workflow_path_filter_covers_workflow_itself():
    wf = _load_workflow()
    triggers = _triggers(wf)
    expected = ".github/workflows/deploy-worker.yml"
    assert expected in (triggers.get("push") or {}).get("paths", [])
    assert expected in (triggers.get("pull_request") or {}).get("paths", [])


def test_workflow_has_workflow_dispatch():
    wf = _load_workflow()
    assert "workflow_dispatch" in _triggers(wf)


def test_workflow_has_deploy_worker_job():
    wf = _load_workflow()
    assert "deploy-worker" in wf.get("jobs", {})


def test_workflow_job_runs_on_ubuntu():
    wf = _load_workflow()
    job = wf["jobs"]["deploy-worker"]
    assert "ubuntu" in job.get("runs-on", "")


def test_workflow_uses_wrangler_supported_node_version():
    wf = _load_workflow()
    steps = wf["jobs"]["deploy-worker"]["steps"]
    setup = _get_step(steps, "Set up Node")
    assert setup is not None
    assert setup.get("with", {}).get("node-version") == "22"


# ---------------------------------------------------------------------------
# (c) wrangler.toml name matches actual Worker
# ---------------------------------------------------------------------------

EXPECTED_WORKER_NAME = "tinyassets-mcp-proxy"
STALE_WORKER_NAME = "tinyassets-mcp-router"


def test_wrangler_toml_name_is_correct():
    """wrangler.toml must use the actual deployed Worker name."""
    text = _load_wrangler_text()
    assert f'name = "{EXPECTED_WORKER_NAME}"' in text, \
        f"wrangler.toml name must be {EXPECTED_WORKER_NAME!r}"


def test_wrangler_toml_no_stale_name():
    """Stale name 'tinyassets-mcp-router' must not appear in wrangler.toml."""
    text = _load_wrangler_text()
    assert STALE_WORKER_NAME not in text, \
        f"Stale Worker name {STALE_WORKER_NAME!r} still in wrangler.toml"


def test_wrangler_toml_keeps_load_bearing_broad_route_and_runtime_settings():
    config = tomllib.loads(_load_wrangler_text())
    assert config["main"] == "worker.js"
    # TWO public regions since 2026-09-30: the connector, and the app after it
    # moved off `/mcp/app` to the apex `/app`.
    #
    # Both are SUFFIX WILDCARDS, and for `/app` that is load-bearing rather than
    # stylistic: a Cloudflare route is matched against the whole URL including
    # the query string, so an exact `tinyassets.io/app` route matches only a bare
    # `/app` and the sign-in return `/app?code=…&state=…` plus the Stripe return
    # `/app?subscribed=1` would match no route at all. Narrowing either entry
    # darks a public surface while the shell still loads.
    assert config["routes"] == [
        {"pattern": "tinyassets.io/mcp*", "zone_name": "tinyassets.io"},
        {"pattern": "tinyassets.io/app*", "zone_name": "tinyassets.io"},
    ]
    assert config["compatibility_date"] == "2025-10-01"
    assert config["compatibility_flags"] == []
    assert config["observability"] == {
        "enabled": True,
        "head_sampling_rate": 1.0,
    }


# ---------------------------------------------------------------------------
# (d) Dry-run vs live deploy conditions
# ---------------------------------------------------------------------------


def _get_step_condition(steps: list[dict], step_name_fragment: str) -> str | None:
    for step in steps:
        name = step.get("name", "") or ""
        if step_name_fragment.lower() in name.lower():
            return step.get("if")
    return None


def _get_step(steps: list[dict], step_name_fragment: str) -> dict | None:
    for step in steps:
        name = step.get("name", "") or ""
        if step_name_fragment.lower() in name.lower():
            return step
    return None


def test_dry_run_step_fires_on_pull_request():
    wf = _load_workflow()
    steps = wf["jobs"]["deploy-worker"]["steps"]
    cond = _get_step_condition(steps, "dry-run")
    assert cond is not None, "dry-run step must have an 'if' condition"
    assert "pull_request" in cond


def test_live_deploy_step_fires_on_push():
    wf = _load_workflow()
    steps = wf["jobs"]["deploy-worker"]["steps"]
    cond = _get_step_condition(steps, "live deploy")
    assert cond is not None, "live deploy step must have an 'if' condition"
    assert "push" in cond


def test_dry_run_does_not_fire_on_push():
    """dry-run step condition must NOT unconditionally include push."""
    wf = _load_workflow()
    steps = wf["jobs"]["deploy-worker"]["steps"]
    cond = _get_step_condition(steps, "dry-run")
    assert cond is not None
    # The condition fires on pull_request and workflow_dispatch+dry_run=true.
    # It must NOT contain a bare "push" that would run on every push.
    assert "event_name == 'push'" not in cond


def test_pull_requests_run_secretless_wrangler_dry_run():
    """PR validation must bundle the Worker without requiring deploy credentials."""
    wf = _load_workflow()
    steps = wf["jobs"]["deploy-worker"]["steps"]

    verify = _get_step(steps, "Verify secrets present")
    assert verify is not None, "workflow must keep a live-deploy secret check"
    assert verify.get("if") == "github.event_name != 'pull_request'"

    dry_run = _get_step(steps, "dry-run")
    assert dry_run is not None
    script = dry_run.get("run", "")
    assert "skipping Wrangler dry-run" not in script
    assert "wrangler deploy --dry-run" in script


def test_live_deploy_resolves_account_id_from_zone_when_secret_missing():
    """The API token has Zone:Read, so the workflow can derive account ID."""
    text = _workflow_text()
    assert "zones?name=tinyassets.io" in text
    assert "CLOUDFLARE_ACCOUNT_ID=${account_id}" in text
    assert "CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}" not in text


def test_live_deploy_proves_retired_route_matrix_is_ordinary_404():
    wf = _load_workflow()
    steps = wf["jobs"]["deploy-worker"]["steps"]
    probe = _get_step(steps, "Prove retired routes are ordinary 404s")

    assert probe is not None
    live_condition = (
        "github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && inputs.dry_run != 'true')\n"
    )
    assert probe.get("if") == live_condition
    script = probe.get("run", "")
    assert script.strip() == "python scripts/probe_retired_mcp_routes.py"


def test_live_deploy_probes_run_in_safety_order_under_the_same_condition():
    wf = _load_workflow()
    steps = wf["jobs"]["deploy-worker"]["steps"]
    live_condition = (
        "github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && inputs.dry_run != 'true')\n"
    )
    names = [step.get("name") for step in steps]
    ordered_names = (
        "Wrangler live deploy (push to main)",
        "Post-deploy canary",
        "Probe canonical URL after deploy",
        "Prove retired routes are ordinary 404s",
    )
    assert [names.index(name) for name in ordered_names] == sorted(
        names.index(name) for name in ordered_names
    )
    for name in ordered_names:
        assert steps[names.index(name)].get("if") == live_condition

    canonical_probe = steps[names.index("Probe canonical URL after deploy")]
    assert "--url https://tinyassets.io/mcp" in canonical_probe.get("run", "")
    assert "--assert-name" not in canonical_probe.get("run", "")


# ---------------------------------------------------------------------------
# (e) Required secrets referenced
# ---------------------------------------------------------------------------


def _workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def test_cloudflare_api_token_secret_referenced():
    assert "CLOUDFLARE_API_TOKEN" in _workflow_text()


def test_cloudflare_account_id_secret_referenced():
    assert "CLOUDFLARE_ACCOUNT_ID" in _workflow_text()


def test_wrangler_installed_in_workflow():
    """TinyAssets must install wrangler before deploying."""
    text = _workflow_text()
    assert "wrangler" in text.lower()

"""Task #10 — direct tests for `tinyassets.api.status` after decomp Step 3.

The legacy test files (test_get_status_primitive.py, test_sandbox_*, etc.)
still cover the chatbot-facing `tinyassets.universe_server` MCP wrapper. This
file exercises `tinyassets.api.status` directly to lock in the canonical
implementation surface.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.api import status as status_mod
from tinyassets.api.status import _policy_hash, get_status

# ── module surface ──────────────────────────────────────────────────────────


def test_module_exposes_expected_public_names():
    """The new submodule's contract surface — guards against silent removal."""
    expected = {"get_status", "_policy_hash"}
    missing = expected - set(dir(status_mod))
    assert not missing, f"status.py is missing public names: {missing}"


# ── _policy_hash unit ───────────────────────────────────────────────────────


def test_policy_hash_is_deterministic():
    """Same payload → same hash (ordering-independent)."""
    a = {"x": 1, "y": [1, 2, 3], "z": {"nested": True}}
    b = {"z": {"nested": True}, "y": [1, 2, 3], "x": 1}
    assert _policy_hash(a) == _policy_hash(b)


def test_policy_hash_differs_for_different_payloads():
    assert _policy_hash({"a": 1}) != _policy_hash({"a": 2})


def test_policy_hash_returns_sha256_hex():
    h = _policy_hash({"k": "v"})
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_policy_hash_handles_empty_dict():
    h = _policy_hash({})
    assert len(h) == 64


# ── get_status smoke shapes ─────────────────────────────────────────────────


@pytest.fixture
def status_env(tmp_path, monkeypatch):
    """Isolated data dir + universe so get_status touches no host files.

    The tests below name that universe explicitly. They used to rely on the
    legacy default resolution, which only an unauthenticated caller reached --
    and there is no unauthenticated caller (founder, 2026-09-02). An
    authenticated caller with no bound home gets the first-contact card, which
    is a different (and correct) surface.
    """
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "test-universe")
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "test-host")
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "test-user")
    universe = tmp_path / "test-universe"
    universe.mkdir()
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, universe.name)
    # Minimal dispatcher config so load_dispatcher_config doesn't error.
    (universe / "dispatcher.json").write_text("{}")
    return universe


def test_get_status_returns_str_json(status_env):
    raw = get_status("test-universe")
    assert isinstance(raw, str)
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)


def test_get_status_returns_versioned_contract_keys(status_env):
    """schema_version=2 contract — all top-level keys present."""
    parsed = json.loads(get_status("test-universe"))
    expected_keys = {
        "schema_version",
        "active_host",
        "tier_routing_policy",
        "evidence",
        "evidence_caveats",
        "caveats",
        "actionable_next_steps",
        "identity_evidence",
        "request_identity",
        "session_boundary",
        "storage_utilization",
        "per_provider_cooldown_remaining",
        "sandbox_status",
        "missing_data_files",
        "supervisor_liveness",
        "auto_ship_health",
        "open_brain",
        "release_state",
        "universe_id",
        "universe_exists",
    }
    assert expected_keys <= set(parsed.keys()), (
        f"missing keys: {expected_keys - set(parsed.keys())}"
    )
    assert parsed["schema_version"] == 3


def test_get_status_active_host_shape(status_env):
    parsed = json.loads(get_status("test-universe"))
    host = parsed["active_host"]
    assert set(host.keys()) >= {"host_id", "served_llm_type", "llm_endpoint_bound"}
    assert host["host_id"] == "test-host"


def test_get_status_evidence_includes_policy_hash(status_env):
    """Round-trip: the `evidence.policy_hash` field is a sha256 hex string."""
    parsed = json.loads(get_status("test-universe"))
    h = parsed["evidence"]["policy_hash"]
    assert isinstance(h, str)
    assert len(h) == 64


def test_get_status_release_state_reports_missing_receipt(status_env):
    parsed = json.loads(get_status("test-universe"))
    release_state = parsed["release_state"]

    assert release_state["receipt_available"] is False
    assert release_state["receipt_path"].endswith("release-state.json")
    assert "release_state_receipt_missing" in release_state["warnings"]


def test_get_status_release_state_reads_deploy_receipt(status_env):
    receipt = {
        "git_sha": "868b8d04abcdef",
        "image_tag": "ghcr.io/tinyassets/tinyassets-daemon:868b8d04abcd",
        "image_digest": "ghcr.io/tinyassets/tinyassets-daemon@sha256:abc123",
        "build_run_id": "111",
        "build_run_url": "https://github.com/TinyAssets/TinyAssets/actions/runs/111",
        "deploy_run_id": "222",
        "deploy_run_url": "https://github.com/TinyAssets/TinyAssets/actions/runs/222",
        "config_hash": "sha256:deadbeef",
        "config_version": "tinyassets-env-v1",
        "schema_migration_rev": "not_applicable",
        "canary_bundle_status": "passed",
        "deployed_at": "2026-05-28T12:00:00Z",
        "rollback_target": "ghcr.io/tinyassets/tinyassets-daemon:previous",
        "actor": "codex-wiki-patch",
        "repository": "TinyAssets/TinyAssets",
        "workflow_event": "workflow_run",
    }
    (status_env.parent / "release-state.json").write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )

    parsed = json.loads(get_status("test-universe"))
    release_state = parsed["release_state"]

    assert release_state["receipt_available"] is True
    assert release_state["git_sha"] == receipt["git_sha"]
    assert release_state["image_digest"] == receipt["image_digest"]
    assert release_state["canary_bundle_status"] == "passed"
    assert release_state["actor"] == "codex-wiki-patch"
    assert release_state["warnings"] == []


def test_get_status_includes_read_only_open_brain_surface(status_env, tmp_path):
    from tinyassets.daemon_brain import capture_daemon_memory
    from tinyassets.daemon_registry import create_daemon

    daemon = create_daemon(
        tmp_path,
        display_name="Status Brain Daemon",
        created_by="pytest",
        soul_mode="soul",
        soul_text="Status Brain Daemon observes memory cost.",
    )
    capture_daemon_memory(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        memory_kind="policy",
        content="Report open-brain token cost as status, not as a decision.",
        source_type="pytest",
        source_id="get-status-open-brain",
        reliability="test_observed",
        temporal_bounds={"valid_from": "2026-05-17"},
        language_type="policy",
    )

    parsed = json.loads(get_status("test-universe"))

    assert parsed["open_brain"]["read_only"] is True
    assert parsed["open_brain"]["daemon_count"] == 1
    assert parsed["open_brain"]["daemons"][0]["daemon_id"] == daemon["daemon_id"]
    assert (
        parsed["open_brain"]["daemons"][0]["cost_ledger"]["estimated_total_tokens"]
        > 0
    )


def test_get_status_explicit_universe_id_overrides_default(status_env, tmp_path):
    """Passing universe_id="other" should resolve to that universe id."""
    other = tmp_path / "other-universe"
    other.mkdir()
    from tests.conftest import own_universe
    own_universe(tmp_path, other.name)
    (other / "dispatcher.json").write_text("{}")
    parsed = json.loads(get_status(universe_id="other-universe"))
    assert parsed["universe_id"] == "other-universe"
    assert parsed["universe_exists"] is True


def test_get_status_nonexistent_universe_marks_universe_exists_false(status_env):
    parsed = json.loads(get_status(universe_id="not-real-universe"))
    assert parsed["universe_id"] == "not-real-universe"
    assert parsed["universe_exists"] is False
    # A nonexistent-universe caveat should be present.
    assert any("does not exist" in c for c in parsed["caveats"])


def test_get_status_session_boundary_uses_token_safe_fingerprint(status_env):
    parsed = json.loads(get_status("test-universe"))
    sb = parsed["session_boundary"]
    assert "account_user" not in sb
    assert sb["principal_fingerprint"] == parsed["request_identity"][
        "principal_fingerprint"
    ]
    # No prior activity log → prior_session_context_available is False.
    assert sb["prior_session_context_available"] is False


def test_get_status_caveats_warn_when_no_provider_bound(status_env, monkeypatch):
    """When no LLM env-var is set AND no CLI is on PATH, caveats warn unbound."""
    import shutil
    for var in (
        "OLLAMA_HOST", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
        "XAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    # Also stub which() so codex/claude CLI presence on the dev machine
    # doesn't flip endpoint_hint away from "unset".
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    parsed = json.loads(get_status("test-universe"))
    assert any("No default LLM provider detected" in c for c in parsed["caveats"])

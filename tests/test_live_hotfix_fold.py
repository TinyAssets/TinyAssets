"""Focused regressions for source recovery and subscription installation."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_bootstrap_reuses_newest_matching_confirmation(tmp_path, monkeypatch):
    from tinyassets.broker import ledger_queries
    from tinyassets.onboarding import model_bootstrap
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers import definition
    from tinyassets.storage import pending_requests

    access = {"source": ModelAccess("discovered").document()}
    requests = [{"request_id": "older", "created_at": 1, "action": {
        "type": "bind_model_access", "provider": "source", "model_access": access,
        "previous_membership": {}, "proposed_membership": access}},
        {"request_id": "newer", "created_at": 2, "action": {
            "type": "bind_model_access", "provider": "source", "model_access": access,
            "previous_membership": {}, "proposed_membership": access}}]
    monkeypatch.setattr(pending_requests, "list_pending", lambda _: requests)
    monkeypatch.setattr(definition, "get_definition", lambda *_: SimpleNamespace(
        owner_user_id="owner", access_method="api_key_http", ref="grant"))
    monkeypatch.setattr(ledger_queries, "query_ledger", lambda *_, **__: {"recovery": {
        "destination": "model:fixture", "descriptor": {"protocol": "fixture",
        "catalogue_url": "catalogue", "benchmark_url": "benchmark"}}})
    result = model_bootstrap._pending_confirmation(
        tmp_path, universe=tmp_path, uid="home", owner="owner",
        preset=SimpleNamespace(id="fixture", catalogue_url="catalogue",
                               benchmark_url="benchmark"))
    assert result["request_id"] == "newer"


def test_source_offer_omits_revoked_grant_and_uses_new_root(tmp_path, monkeypatch):
    from tinyassets import commons_catalog, provider_assignment
    from tinyassets.api import pending_requests
    from tinyassets.broker import ledger_queries
    from tinyassets.onboarding.source_connect import _offer_pool_access
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers import definition

    old = SimpleNamespace(provider="api_key_http:old", access=ModelAccess())
    assignment = SimpleNamespace(owner_user_id="owner", candidates=[old],
                                 binding_id="binding", provider="api_key_http:old")
    monkeypatch.setattr(provider_assignment, "load_provider_assignment",
                        lambda *_, **__: assignment)
    monkeypatch.setattr(commons_catalog, "list_bindings", lambda *_, **__: [{
        "created_by": "owner", "configuration": {"provider_ref": "binding"},
        "agent_binding_id": "agent", "revision": 1}])
    monkeypatch.setattr(definition, "get_definition", lambda *_: SimpleNamespace(
        owner_user_id="owner", access_method="api_key_http", ref="revoked"))
    monkeypatch.setattr(ledger_queries, "granted_resource_row", lambda *_, **__: (
        _ for _ in ()).throw(PermissionError("gone")))
    offered = {}
    def capture(**kwargs):
        offered.update(kwargs["payload"]["action"])
        return {"request_id": "request"}
    monkeypatch.setattr(pending_requests, "request_from_user", capture)
    _offer_pool_access(base=tmp_path, uid="home", owner="owner", preset={"name": "New",
        "offer": "Use this source."}, definition_id="new", models=["free"])
    assert offered["provider"] == "new"
    assert set(offered["model_access"]) == {"new"}


def test_access_capture_ignores_unresolvable_previous_member(tmp_path, monkeypatch):
    from tinyassets import provider_serving_binding
    from tinyassets.api import model_access_requests
    from tinyassets.provider_assignment_manifest import ModelAccess

    old = SimpleNamespace(provider="api_key_http:old", access=ModelAccess())
    assignment = SimpleNamespace(state="ready", candidates=[old],
                                 assignment_digest="digest", generation=1)
    monkeypatch.setattr(model_access_requests, "_scope", lambda _: (tmp_path, tmp_path, "owner"))
    monkeypatch.setattr(model_access_requests, "_observe", lambda *args: (
        {"revision": 1}, assignment))
    monkeypatch.setattr(model_access_requests, "_connection_incarnations", lambda *args: {})
    def resolve(_base, _uid, _actor, name, access):
        if name == "api_key_http:old":
            raise PermissionError("grant gone")
        return SimpleNamespace(provider=name, access=access)
    monkeypatch.setattr(provider_serving_binding, "_resolve_serving_source", resolve)
    action = {"agent_binding_id": "agent", "expected_revision": 1,
              "provider": "subscription_cli:new",
              "model_access": {"subscription_cli:new": ModelAccess().document()}}
    result = model_access_requests.capture_action("home", action)
    assert set(result["proposed_membership"]) == {"subscription_cli:new"}


def test_adding_source_drops_dead_members_and_replaces_dead_root(tmp_path, monkeypatch):
    from tinyassets import (
        commons_catalog,
        provider_assignment,
        provider_serving_binding,
        shared_self,
    )
    from tinyassets.api.connection_uses import _accept_additional_model_source
    from tinyassets.onboarding import serving
    from tinyassets.provider_assignment_manifest import ModelAccess

    assignment = SimpleNamespace(owner_user_id="owner", manifest_digest="digest",
        candidates=[SimpleNamespace(provider="api_key_http:old", access=ModelAccess())],
        provider="api_key_http:old", assignment_digest="digest")
    monkeypatch.setattr(provider_assignment, "load_provider_assignment",
                        lambda *_, **__: assignment)
    monkeypatch.setattr(commons_catalog, "serving_binding_candidates", lambda *_, **__: [{
        "agent_binding_id": "agent", "revision": 1, "updated_at": "now", "status": "stopped"}])
    monkeypatch.setattr(serving, "_gesture_lock", lambda _: nullcontext())
    monkeypatch.setattr(serving, "_require_current_admin", lambda *_, **__: None)
    monkeypatch.setattr(shared_self, "require_founder_home", lambda *_, **__: None)
    monkeypatch.setattr(provider_serving_binding, "_resolve_serving_source",
                        lambda *_, **__: (_ for _ in ()).throw(PermissionError("gone")))
    published = {}
    def bind(**kwargs):
        published.update(kwargs)
        return {"agent_binding": {"status": "stopped"}}
    monkeypatch.setattr(provider_serving_binding, "bind_serving_provider", bind)
    _accept_additional_model_source(base=tmp_path, uid="home", actor="owner",
                                    definition_id="new", model={"models": [{"id": "free"}]})
    assert published["provider"] == "new"
    assert set(published["model_access"]) == {"new"}


def test_stale_saved_default_uses_connected_models(monkeypatch):
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_policy import Catalog, Interaction, ModelPolicy, ModelRef
    from tinyassets.providers.served_model_plan import _recover_stale_default

    connected = ModelRef("live", "available")
    policy = ModelPolicy(1, "explicit", (), saved_default=ModelRef("gone", "missing"))
    plan = AgentModelPlan(Catalog("owner", "home", ()), policy,
                          Interaction(False, frozenset({"text"}), frozenset()))
    monkeypatch.setattr(AgentModelPlan, "next_candidate",
                        lambda self, *_: connected if self.policy.mode == "automatic" else None)
    recovered = _recover_stale_default(plan, [SimpleNamespace(models=(connected,))],
                                       "owner", "home")
    assert recovered.policy.mode == "automatic"
    assert recovered.policy.saved_default is None
    assert recovered.next_candidate("owner", "home") == connected


def test_subscription_failure_detail_keeps_only_fixed_text():
    from tinyassets.onboarding.subscription_connect import _failure_detail

    try:
        raise ValueError("invalid login request")
    except ValueError as exc:
        assert "ValueError@" in _failure_detail(exc)
        assert ": invalid login request" in _failure_detail(exc)
    try:
        raise ValueError("secret value from provider")
    except ValueError as exc:
        assert _failure_detail(exc).endswith(": connection failed")


def test_npm_failure_discards_partial_files_and_cache(tmp_path, monkeypatch):
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime

    runtime = object.__new__(SubscriptionRuntime)
    runtime.home = tmp_path
    runtime.directory = tmp_path / "source"
    runtime.directory.mkdir()
    runtime.cli_login = {"install": {"manager": "npm"}}
    runtime.binary = runtime.directory / "missing"
    runtime.lock = nullcontext
    runtime._require_space = lambda: None
    runtime._install_npm = lambda *_: (_ for _ in ()).throw(RuntimeError("install failed"))
    for name in ("node_modules", ".ta-cli/.npm-cache"):
        (tmp_path / name).mkdir(parents=True)
    for name in ("package-lock.json", "installed.json"):
        (runtime.directory / name).write_text("partial")
    with pytest.raises(RuntimeError, match="install failed"):
        runtime.install()
    assert not any((runtime.directory / name).exists() for name in (
        "node_modules", "package-lock.json", "installed.json"))
    assert not (tmp_path / ".ta-cli/.npm-cache").exists()


def test_npm_requires_one_gib_before_install(tmp_path, monkeypatch):
    import shutil

    from tinyassets.providers.subscription_runtime import SubscriptionError, SubscriptionRuntime

    runtime = object.__new__(SubscriptionRuntime)
    runtime.directory = tmp_path
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=1024**3 - 1))
    with pytest.raises(SubscriptionError, match="not enough free storage"):
        runtime._require_space()


def test_liveness_directory_allows_broker_group_writes():
    layout = (Path(__file__).resolve().parents[1] / "tinyassets/boxes/layout.py").read_text()
    assert "BROKER_READ_GID = 1102" in layout
    assert "LIVENESS_DIRECTORY_MODE = 0o2770" in layout
    deploy = (Path(__file__).resolve().parents[1] / "deploy/deploy_fail_safe.sh").read_text()
    assert 'chmod 2770 -- "$liveness"' in deploy


def test_compose_passes_same_outbound_flag_to_daemon_and_broker():
    compose = (Path(__file__).resolve().parents[1] / "deploy/compose.yml").read_text()
    assert compose.count("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED: "
                         "${TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED:-1}") == 2


def test_free_storage_default_is_five_gib():
    from tinyassets.usage_policy import _DEFAULT_FREE_STORAGE_GIB

    assert _DEFAULT_FREE_STORAGE_GIB == 5.0

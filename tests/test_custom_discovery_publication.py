"""Authenticated source publication -> real scoped discovery with synthetic IO.

No real accounts, grants or sockets. Configured metadata is not agent readiness.
"""

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests import test_model_discovery_capability as profiles
from tests.test_discovery_catalogue_shapes import BENCHMARK, NOW, payload
from tests.test_discovery_contract import descriptor
from tinyassets.providers import discovery_snapshot as snapshots
from tinyassets.providers.discovery_contract import SourceContract
from tinyassets.providers.model_policy import Catalog, ModelPolicy, order_models
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    CredentialBlindBroker,
    ScopedConnectionProxy,
    _enforce_endpoint_allowlist,
    _parse_canonical_https_url,
)

rig = profiles.rig


def custom_descriptor():
    contract = descriptor()
    contract["transport"].update(catalogue_path="/api/v1/models/user",
                                 catalogue_query="output_modalities=all",
                                 benchmark_path="/api/v1/benchmarks")
    for value in contract["prices"]["fields"].values():
        value["encoding"] = "number"
    return {"schema_version": 1, "catalogue_url": profiles.CATALOGUE,
            "benchmark_url": profiles.BENCHMARK, "contract": contract}


@pytest.fixture
def source(rig, monkeypatch):
    monkeypatch.setattr(snapshots, "_now", lambda: NOW)
    document = custom_descriptor()
    state = SimpleNamespace(document=document, calls=[], starts=[], closes=0,
                            after_response=lambda: None)
    wire = payload()
    wire["inventory"]["items"][0]["tariff"] = {"input": 1.25, "output": 10, "call": 0}
    state.catalogue_json = json.dumps(wire)
    state.benchmark_json = json.dumps({"measured_at": NOW.isoformat(), "evaluations": [{
        "subject": "evaluation/7", "measurement": {"source": BENCHMARK["source"],
                                                       "agent_score": 14.25,
                                                       "reason_score": 22.5},
    }]})
    broker_ledger = ConnectionLedger(rig.base / ".broker" / "outbound.db", data_root=rig.base,
                                      verify_authenticated_principal=lambda: "owner")

    def network(**kwargs):
        request = kwargs["request"]
        canonical = _parse_canonical_https_url(request["url"], allowed_ports=frozenset({443}))
        _enforce_endpoint_allowlist(canonical, kwargs["verb"], kwargs["allowed_endpoints"],
                                    kwargs["access_mode"])
        if kwargs.get("on_connect") is not None:
            kwargs["on_connect"](None)
        state.calls.append((kwargs["verb"], request))
        if kwargs["verb"] == "POST":
            return state.infer(request)
        raw = state.benchmark_json if "benchmarks" in request["url"] else state.catalogue_json
        state.after_response()
        return {"status": 200, "body": raw}

    from tinyassets.storage.agent_request_usage import resolve_inference_usage

    def accounting(resource, grant, verb, request, envelope, operation):
        return resolve_inference_usage(rig.base, "owner", "u-models", broker_ledger,
                                       resource, grant, verb, request, envelope, operation)

    broker = CredentialBlindBroker(broker_ledger, resolve_credential=lambda *_: "synthetic",
                                   network_request=network, resolve_inference_usage=accounting)

    class Channel:
        def request(self, verb, request, *, inference_usage=None):
            return broker.dispatch(
                "grant-models", verb, request,
                **({"inference_usage": inference_usage.document(),
                    "operation_id": inference_usage.operation_id}
                   if inference_usage is not None else {}),
            )

        def close(self):
            state.closes += 1

    def start(self, **kwargs):
        state.starts.append(kwargs)
        access_mode = rig.ledger.get_connection_view("conn-models").access_mode
        return ScopedConnectionProxy(grant_id=kwargs["grant_id"], provider=kwargs["provider"],
                                      destination=kwargs["destination"], scopes=kwargs["scopes"],
                                      _channel=Channel(), access_mode=access_mode)

    monkeypatch.setattr(ConnectionLedger, "_start_scoped_proxy", start)
    state.refresh = lambda: snapshots.refresh_model_discovery(
        owner_user_id="owner", universe_id="u-models", definition_id=rig.definition.id,
    )
    return state


def test_preview_and_publication_share_validation_without_grant_or_identity_changes(rig, source):
    view = rig.ledger.get_connection_view("conn-models")
    preview = rig.api(descriptor=source.document, preview=True)
    assert preview["status"] == "preview"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None
    configured = rig.api(descriptor=source.document)
    assert configured["status"] == "configured"
    assert configured["descriptor"] == source.document
    assert configured["descriptor_digest"] == preview["descriptor_digest"]
    assert configured["source_semantics"] == preview["source_semantics"]
    assert configured["source_semantics"]["grants_spending"] is False
    assert configured["source_semantics"]["independently_verified"] is False
    stored = rig.ledger.get_connection_capability("conn-models", "model_discovery")
    assert stored.protocol == "" and isinstance(stored.execution_contract(), SourceContract)
    assert stored.descriptor() == source.document
    source.document["contract"]["quantity_model"]["quantities"]["output_tokens"]["output"] = 9
    assert stored.execution_contract().quantities.bounds(1, 1) == (1, 8, 1)
    assert rig.ledger.get_connection_view("conn-models") == view
    assert rig.ledger.get_grant("grant-models") == rig.grant
    assert source.calls == [] and source.starts == []


def test_preview_cannot_replace_existing_metadata_or_revoke_it(rig, source):
    previous = rig.publish()
    assert rig.api(descriptor=source.document, preview=True)["status"] == "preview"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") == previous
    assert rig.api(enabled=False, preview=True)["error"] == "provider_capability_invalid"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") == previous


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("bad", ["version", "legacy_mix", "unknown", "trust", "endpoint",
                                "auth", "tariff", "preview_type"])
def test_configured_source_stays_inside_closed_schema_and_current_grants(rig, source, preview, bad):
    document = deepcopy(source.document)
    if bad == "version":
        document["schema_version"] = True
    elif bad == "legacy_mix":
        document["protocol"] = profiles.DESCRIPTOR["protocol"]
    elif bad == "unknown":
        document["future"] = True
    elif bad == "trust":
        document["contract"]["owner_filtered"] = True
    elif bad == "endpoint":
        document["catalogue_url"] = document["catalogue_url"].replace(
            "owned.example", "other.example",
        )
    elif bad == "auth":
        document["contract"]["transport"]["auth_scheme"] = "none"
    elif bad == "tariff":
        document["contract"]["price_bound_basis"] = "owner_tariff"
    else:
        preview = "true"
    expected = "not_found" if bad == "auth" else "provider_capability_invalid"
    assert rig.api(descriptor=document, preview=preview)["error"] == expected
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None
    assert source.calls == []


@pytest.mark.parametrize("preview", [False, True])
def test_revoke_between_api_read_and_transaction_refuses_preview_and_write(rig, source,
                                                                          monkeypatch, preview):
    original = ConnectionLedger.configure_capability

    def revoke(self, **kwargs):
        self.revoke_grant("grant-models")
        return original(self, **kwargs)

    monkeypatch.setattr(ConnectionLedger, "configure_capability", revoke)
    assert rig.api(descriptor=source.document, preview=preview) == {
        "error": "not_found", "resource": "connection",
    }
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None


def test_real_publication_snapshot_captures_exact_contract_not_a_provider_alias(rig, source):
    assert rig.api(descriptor=source.document)["status"] == "configured"
    snapshot = source.refresh()
    assert isinstance(snapshot.execution_contract, SourceContract)
    assert snapshot.execution_contract.descriptor_json == rig.ledger.get_connection_capability(
        "conn-models", "model_discovery",
    ).contract_json
    assert snapshot.models.provider_scope == "custom-http"
    assert snapshot.models.availability_basis == "owner_configured_contract"
    assert snapshot.models.owner_filtered is False
    assert snapshot.models.source_kind == "http"
    assert snapshot.models.authenticated_account_id is None
    assert snapshot.models.executor_tools is True
    model = snapshot.models.models[0]
    assert model.model_id == "unseen:model-v7" and model.scores.agentic == 14250
    assert {price.component: price.amount_micros for price in model.pricing.charges} == {
        "input_million_tokens_usd": 1_250_000,
        "output_million_tokens_usd": 10_000_000, "request_usd": 0,
    }
    assert source.calls == [("GET", {"url": profiles.CATALOGUE}),
                            ("GET", {"url": profiles.BENCHMARK})]
    assert source.closes == 2 and len(source.starts) == 2
    assert rig.ledger.get_grant("grant-models") == rig.grant
    snapshots.assert_discovery_snapshot_current(snapshot)
    # Discovery/configuration alone supplies no spending permission. The source
    # claims remain distinct from independently established privacy evidence.
    result = order_models(Catalog("owner", "u-models", (snapshot.models,)),
                          ModelPolicy(0, "automatic", ()), snapshot.execution_contract.interaction,
                          owner_id="owner", universe_id="u-models")
    assert not result.candidates


def test_changed_contract_during_fetch_cannot_publish_old_snapshot(rig, source):
    rig.api(descriptor=source.document)
    changed = deepcopy(source.document)
    changed["contract"]["quantity_model"]["quantities"]["output_tokens"]["output"] = 9
    source.after_response = lambda: rig.api(descriptor=changed)
    with pytest.raises(snapshots.ModelDiscoveryUnavailable) as error:
        source.refresh()
    assert error.value.reason == "source_revoked"


def test_changed_quantity_or_endpoint_invalidates_captured_snapshot(rig, source):
    rig.api(descriptor=source.document)
    snapshot = source.refresh()
    changed = deepcopy(source.document)
    changed["contract"]["quantity_model"]["quantities"]["output_tokens"]["output"] = 9
    rig.api(descriptor=changed)
    with pytest.raises(snapshots.ModelDiscoveryUnavailable):
        snapshots.assert_discovery_snapshot_current(snapshot)
    fresh = source.refresh()
    assert fresh.source_digest != snapshot.source_digest
    assert fresh.execution_contract.quantities.bounds(1, 1) == (1, 9, 1)


def test_exact_old_descriptor_serialization_and_legacy_execution_are_retained(rig, source):
    before = rig.publish()
    assert before.contract_json == "" and before.descriptor() == profiles.DESCRIPTOR
    assert before.execution_contract().account_filtered is True
    assert rig.api() == {"status": "configured", "capability_kind": "model_discovery",
                         "provider": f"api_key_http:{rig.definition.id}", "scope": "connection",
                         "descriptor": profiles.DESCRIPTOR}
    with pytest.raises(PermissionError):
        rig.publish(descriptor=source.document, preview=True,
                    expected_grant=replace(rig.grant, owner_user_id="other"))


def test_existing_graph_action_exposes_unpowered_preview_and_configuration(rig, source):
    from tinyassets.universe_server import write_graph

    document = {"capability_kind": "model_discovery", "definition_id": rig.definition.id,
                "enabled": True, "preview": True, "descriptor": source.document}
    preview = json.loads(write_graph(target="connection", operation="configure_provider_capability",
                                    graph_id="u-models", payload_json=json.dumps(document)))
    assert preview["status"] == "preview"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None
    del document["preview"]
    configured = json.loads(write_graph(target="connection",
                                       operation="configure_provider_capability",
                                       graph_id="u-models", payload_json=json.dumps(document)))
    assert configured["status"] == "configured"
    assert configured["descriptor_digest"] == preview["descriptor_digest"]
    assert source.calls == []


def test_no_read_or_write_when_authentication_or_admin_scope_is_missing(rig, source, monkeypatch):
    from tinyassets import daemon_server
    from tinyassets.api import permissions

    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: False)
    assert rig.api(descriptor=source.document, preview=True)["error"] == "authentication_required"
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(daemon_server, "list_universe_acl", lambda *a, **k: [])
    assert rig.api(descriptor=source.document, preview=True)["error"] == "not_found"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None
    assert source.calls == []

"""Current assignment -> fresh catalogue -> real HTTP executor, synthetic network.

No public activation or live account claim: fixtures establish the internal
serving intent because general v2 readiness is deliberately still gated.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from tests import test_discovery_snapshot as snapshot_tests
from tests.inference_usage_helpers import accounting_resolver
from tinyassets.auth import middleware as auth
from tinyassets.commons_catalog import (
    create_binding,
    publish_definition,
    set_binding_serving_in_transaction,
)
from tinyassets.config import load_universe_config
from tinyassets.provider_assignment import (
    authorize_served_provider_call,
)
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import bind_serving_provider
from tinyassets.providers import discovery_snapshot as snapshots
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import UniverseContext
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.router import ProviderRouter
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

rig = snapshot_tests.rig
reader = snapshot_tests.reader
MODEL = "new-company/new-model"




@pytest.fixture
def served(rig, reader, monkeypatch):
    definition = publish_definition(
        rig.base,
        author_id="owner",
        payload={
            "schema_version": 1,
            "name": "Selected",
            "description": "fixture",
            "tags": ["test"],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    agent = create_binding(
        rig.base,
        universe_id="u-models",
        definition_id=definition["agent_definition_id"],
        created_by="owner",
        payload={"schema_version": 1, "name": "Selected", "role": "writer"},
    )
    connected = bind_serving_provider(
        base_path=rig.base,
        universe_dir=rig.base / "u-models",
        owner_user_id="owner",
        universe_id="u-models",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=agent["revision"],
        provider=rig.definition.id,
        model_access={rig.definition.id: ModelAccess("discovered")},
    )
    with SQLiteProviderWorkAuthorityStore(rig.base).connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        agent = set_binding_serving_in_transaction(
            conn,
            universe_id="u-models",
            binding_id=agent["agent_binding_id"],
            expected_revision=connected["agent_binding"]["revision"],
            owner_user_id="owner",
            enabled=True,
        )
        conn.commit()
    reserve = auth.reserve_provider_request(
        principal_id="owner",
        session_id="selection-session",
        request_id="selection-request",
        tool_name="converse",
    )
    capability = auth.claim_provider_request(reserve, tool_name="converse")
    carrier = auth.mint_provider_request_carrier(
        universe_id="u-models",
        agent_binding_id=agent["agent_binding_id"],
        binding_revision=agent["revision"],
        operation="converse",
    )
    provider = f"api_key_http:{rig.definition.id}"
    context = UniverseContext(
        universe_dir=rig.base / "u-models",
        config=load_universe_config(rig.base / "u-models"),
        provider_request=carrier,
        model_selection=ModelRef(provider, MODEL),
    )
    wire = []

    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            wire.append((verb, document))
            return {
                "status": 200,
                "body": json.dumps(
                    {
                        "model": "actual-answer-model",
                        "choices": [{"message": {"content": "selected answer"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                    }
                ),
            }

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *args, **kwargs: Proxy()))
    result = SimpleNamespace(
        rig=rig,
        context=context,
        capability=capability,
        wire=wire,
        agent=agent,
        router=ProviderRouter({}),
    )
    yield result
    auth.revoke_provider_request(capability)


def _call(served, *, config=None, context=None):
    from tests.support.model_call import call as model_call

    return asyncio.run(
        model_call(served.router,
            "writer",
            "hello",
            "be terse",
            config,
            operation="converse",
            universe_context=context or served.context,
        )
    )


def _authorize(served, selection=None):
    return authorize_served_provider_call(
        served.rig.base,
        universe_dir=served.context.universe_dir,
        request_carrier=served.context.provider_request,
        role="writer",
        operation="converse",
        model_selection=selection or served.context.model_selection,
    )






























def _record_allowlist(universe_dir, allowlist):
    from tinyassets import provider_authority

    current = provider_authority.authority_for(universe_dir, None)
    provider_authority.write_record(universe_dir, {**current, "allowed_providers": allowlist})










@pytest.fixture
def blocked_discovery(served, reader, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    catalogue_reads = 0

    def read(**kwargs):
        nonlocal catalogue_reads
        calls.append(kwargs)
        if "models/user" in kwargs["url"]:
            catalogue_reads += 1
            if catalogue_reads == 2:
                entered.set()
                assert release.wait(5), "test failed to release discovery"
        return reader[1](**kwargs)

    monkeypatch.setattr(snapshots, "read_http_discovery_document", read)
    yield entered, release, calls
    release.set()


def _async_call(served):
    from tests.support.model_call import call as model_call

    return model_call(served.router,
        "writer", "hello", "system", operation="converse", universe_context=served.context
    )

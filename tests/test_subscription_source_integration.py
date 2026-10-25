"""Native sources share model admission, discovery and the engine reply contract."""
import asyncio
import json

import pytest

from tests.support.broker_ipc import broker  # noqa: F401
from tests.test_owner_broker_egress import box  # noqa: F401
from tests.test_subscription_box_runtime import fixture_package
from tinyassets.providers.subscription_lease import custody


@pytest.fixture
def connected(box):  # noqa: F811
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime

    root = box.root
    (root / "center/soul.md").write_text("Fixture owner agent")
    set_founder_home(root, founder_sub="owner", universe_id="center", platform_generated=True)
    grant_universe_access(root, universe_id="center", actor_id="owner", permission="admin")
    doc = fixture_package(root)
    doc["models"] = [{"id": "default", "context": 200000}]
    (root / ".ta-cli/sources/fake-cli.json").write_text(json.dumps(doc))
    runtime = SubscriptionRuntime("fake-cli")
    runtime.install()
    (root / ".fake-login").write_text("fixture-login")
    runtime.accept()
    with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
        yield runtime


@pytest.mark.verifies("box-subscription-sources:Generic invocation and selection")
def test_publish_discover_select_and_invoke(connected):
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.discovery_snapshot import refresh_model_discovery
    from tinyassets.providers.model_selection import prepare_selected_model
    from tinyassets.providers.provider_resolver import provider_for_definition
    from tinyassets.providers.served_model_plan import connected_sources

    root = connected.home
    definition = register_definition(universe_id="center", owner_user_id="owner",
        access_method="subscription_cli", protocol="cli_json", model="default", ref="fake-cli")
    provider = provider_for_definition(definition)
    assert provider.name in connected_sources(root, root / "center", "owner")
    snapshot = refresh_model_discovery(owner_user_id="owner", universe_id="center",
                                       definition_id=definition.id)
    assert snapshot.models.source_kind == "subscription_cli"
    selected, recheck = prepare_selected_model(base_path=root, owner_user_id="owner",
        universe_id="center", provider=provider.name, model_id="default",
        access=ModelAccess("explicit", ("default",), None), needs_tools=True)
    recheck()
    reply = asyncio.run(provider.complete("fixture reply", "", ModelConfig(selected_model=selected),
                                         universe_dir=root / "center"))
    assert reply.text == "fixture reply"
    (connected.directory / "connection").write_text("replacement-incarnation")
    from tinyassets.providers.discovery_http import ModelDiscoveryUnavailable
    with pytest.raises(ModelDiscoveryUnavailable):
        recheck()


@pytest.mark.verifies("box-subscription-sources:Native login and durable isolation")
def test_finish_enables_owner_agent(connected):
    from tinyassets.onboarding.subscription_connect import finish
    from tinyassets.provider_assignment import load_provider_assignment

    result = finish("fake-cli")
    assert result["status"] == "connected", result
    assignment = load_provider_assignment(connected.home, universe_id="center")
    assert assignment.state == "ready"
    assert assignment.provider == result["provider"]
    from tinyassets.api.model_options import _collect
    options = _collect(connected.home, 'owner', 'center')
    source, = options['sources']
    assert source['access_method'] == 'subscription_cli'
    assert source['display_name'] == 'Fixture'
    assert source['bind_key'] in options['accepted_model_access']
    custody(connected, 'delete')
    disconnected = _collect(connected.home, 'owner', 'center')
    assert disconnected['sources'][0]['display_name'] == 'Fixture'
    assert disconnected['source_failures']


def test_disconnect_reanchors_assignment_and_allows_later_access_change(connected):
    from tinyassets.api.model_access_requests import capture_action
    from tinyassets.commons_catalog import list_bindings
    from tinyassets.onboarding.subscription_connect import disconnect, finish
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import bind_serving_provider
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime

    root = connected.home
    first = finish("fake-cli")["provider"]
    doc = connected.source.document()
    doc["id"] = "second-cli"
    (root / ".ta-cli/sources/second-cli.json").write_text(json.dumps(doc))
    second = SubscriptionRuntime("second-cli")
    second.install()
    (root / ".fake-login").write_text("fixture-login")
    second.accept()
    assert second.authenticated()
    definition = register_definition(universe_id="center", owner_user_id="owner",
        access_method="subscription_cli", protocol="cli_json", model="default", ref="second-cli")
    survivor = "subscription_cli:" + definition.id
    def agent():
        return next(b for b in list_bindings(root, universe_id="center", limit=100)
                    if b["configuration"].get("provider_ref"))
    binding = agent()
    access = ModelAccess("explicit", ("default",), None)
    bind_serving_provider(base_path=root, universe_dir=root / "center", owner_user_id="owner",
        universe_id="center", agent_binding_id=binding["agent_binding_id"],
        expected_revision=binding["revision"], provider=first,
        model_access={first: access, survivor: access})
    disconnect("fake-cli")
    assignment = load_provider_assignment(root, universe_id="center")
    assert assignment.provider == survivor
    assert [m.provider for m in assignment.candidates] == [survivor]
    binding = agent()
    assert binding["configuration"]["provider_ref"] == assignment.binding_id
    action = {"type": "bind_model_access", "agent_binding_id": binding["agent_binding_id"],
        "expected_revision": binding["revision"], "provider": survivor,
        "model_access": {survivor: access.document()}}
    assert capture_action("center", action)["previous_membership"] == action["model_access"]
    bind_serving_provider(base_path=root, universe_dir=root / "center", owner_user_id="owner",
        universe_id="center", agent_binding_id=binding["agent_binding_id"],
        expected_revision=binding["revision"], provider=survivor, model_access={survivor: access})
    disconnect("second-cli")
    assignment = load_provider_assignment(root, universe_id="center")
    assert assignment.state == "unassigned" and not assignment.candidates

@pytest.mark.verifies("box-subscription-sources:Definition-generated connect controls")
def test_protected_connect_door(connected):
    import time

    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.onboarding import app_config
    from tinyassets.onboarding.subscription_connect import handle

    app = Starlette(routes=[
        Route('/app/subscription-connect/{operation}', handle, methods=['POST'])])
    origin = app_config()['resource'].split('/mcp')[0].rstrip('/')
    with TestClient(app, base_url=origin, headers={'Origin': origin}) as client:
        def post(action, **data):
            return client.post('/app/subscription-connect/' + action, json=data)
        assert post('begin', source_id='fake-cli').status_code == 403
        proof = {'identity_json': json.dumps({'user_id': 'owner'}), 'expires_at': time.time() + 60}
        identity = Identity(user_id='owner', username='owner', capabilities=['write'],
                            metadata={'router_owner_session': proof})
        with identity_context(identity):
            custody(connected, 'delete')
            (connected.home / '.fake-login').unlink(missing_ok=True)
            state = post('begin', source_id='fake-cli').json()
            flow = state['flow']
            deadline = time.monotonic() + 20
            while not state['authorize_url'] and time.monotonic() < deadline:
                time.sleep(.05)
                state = post('poll', flow=flow).json()
            assert state['authorize_url'].startswith('https://login.example.test/')
            assert post('finish', flow=flow).status_code == 409
            assert post('code', flow=flow, code='approved').status_code == 200
            while state['status'] != 'connected' and time.monotonic() < deadline:
                time.sleep(.05)
                state = post('poll', flow=flow).json()
            assert state['status'] == 'connected', state
            assert post('finish', flow=flow).json()['status'] == 'connected'
            assert client.post('/app/subscription-connect/poll', json={'flow': flow},
                               headers={'Origin': 'https://other.example'}).status_code == 403
        assert post('poll', flow=flow).status_code == 403


@pytest.mark.verifies("box-subscription-sources:Native login and durable isolation")
def test_descriptor_publication_is_owner_scoped(connected, box):  # noqa: F811
    from tinyassets.broker.owner_sources import publish, read
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ConnectionLedger

    definition = register_definition(universe_id='center', owner_user_id='owner',
        access_method='subscription_cli', protocol='cli_json', model='default', ref='fake-cli')
    assert [item['id'] for item in read(box.ledger, 'ignored')] == [definition.id]
    other = ConnectionLedger(box.ledger._db_path, data_root=box.shared,
                             verify_authenticated_principal=lambda: 'other')
    assert read(other, 'owner') == []
    with pytest.raises(ValueError):
        publish(other, 'other', 'owner', definition.as_dict())

@pytest.mark.verifies("box-subscription-sources:Generic invocation and selection")
def test_engine_tool_protocol_and_large_stdin(connected):
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.agent_inference import AgentInferenceRequest
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.model_selection import prepare_selected_model
    from tinyassets.providers.provider_resolver import provider_for_definition

    doc = connected.source.document()
    doc['invoke']['cli']['argv'] = ['{bin}', 'engine']
    doc['invoke']['cli']['stdin'] = '{prompt}'
    (connected.home / '.ta-cli/sources/fake-cli.json').write_text(json.dumps(doc))
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime

    SubscriptionRuntime('fake-cli').accept()
    definition = register_definition(universe_id='center', owner_user_id='owner',
        access_method='subscription_cli', protocol='cli_json', model='default', ref='fake-cli')
    provider = provider_for_definition(definition)
    selection, _ = prepare_selected_model(base_path=connected.home, owner_user_id='owner',
        universe_id='center', provider=provider.name, model_id='default',
        access=ModelAccess('explicit', ('default',), None), needs_tools=True)
    request = AgentInferenceRequest(tools=[{'type': 'function', 'function': {
        'name': 'read', 'description': 'Read an owner file', 'parameters': {'type': 'object'}}}])
    prompt = 'a' * 150000
    response = asyncio.run(provider.complete(prompt, 'system',
        ModelConfig(agent_request=request, selected_model=selection),
        universe_dir=connected.home / 'center'))
    call, = response.agent_reply.tool_requests
    assert call.name == 'read'
    assert call.arguments() == {'text': prompt}
    assert response.text == ''

@pytest.mark.verifies("box-subscription-sources:Owner-box subscription runtime")
def test_pip_hash_locked_owner_install_and_invoke(connected):
    import hashlib
    import zipfile

    from tinyassets.providers.subscription_runtime import SubscriptionRuntime

    wheelhouse = connected.home / 'wheels'
    wheelhouse.mkdir()
    wheel = wheelhouse / 'fixture_pip-1.0.0-py3-none-any.whl'
    files = {
        'fixture_pip.py': 'def main():\n    import sys\n    print("fixture pip reply")\n',
        'fixture_pip-1.0.0.dist-info/METADATA':
            'Metadata-Version: 2.1\nName: fixture-pip\nVersion: 1.0.0\n',
        'fixture_pip-1.0.0.dist-info/WHEEL':
            'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        'fixture_pip-1.0.0.dist-info/entry_points.txt':
            '[console_scripts]\nfixture-pip = fixture_pip:main\n',
    }
    files['fixture_pip-1.0.0.dist-info/RECORD'] = ''.join(name + ',,\n' for name in files)
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    doc = connected.source.document()
    doc['id'] = 'fixture-pip'
    doc['sign_in']['cli_login']['install'] = {
        'manager': 'pip', 'package': 'fixture-pip', 'version': '1.0.0',
        'bin': 'fixture-pip', 'wheelhouse': 'wheels', 'requirements': [{'package': 'fixture-pip',
        'version': '1.0.0',
        'hashes': ['sha256:' + hashlib.sha256(wheel.read_bytes()).hexdigest()]}]}
    doc['invoke']['cli'] = {'argv': ['{bin}'], 'format': 'text'}
    (connected.home / '.ta-cli/sources/fixture-pip.json').write_text(json.dumps(doc))
    runtime = SubscriptionRuntime('fixture-pip')
    (connected.home / '.fake-login').write_text('fixture-pip-login')
    runtime.install()
    runtime.accept()
    assert runtime.invoke('hello') == 'fixture pip reply'
    previous = runtime.binary.stat().st_mtime_ns
    runtime.install()
    assert runtime.binary.stat().st_mtime_ns == previous
    (runtime.directory / 'installed.json').unlink()
    wheel.write_bytes(wheel.read_bytes() + b'tampered')
    # Force pip to verify the wheel again rather than accepting installed metadata.
    doc['sign_in']['cli_login']['install']['requirements'][0]['hashes'] = ['sha256:' + '0' * 64]
    (connected.home / '.ta-cli/sources/fixture-pip.json').write_text(json.dumps(doc))
    from tinyassets.providers.subscription_runtime import SubscriptionError
    with pytest.raises(SubscriptionError):
        SubscriptionRuntime('fixture-pip').install()

@pytest.mark.verifies("box-subscription-sources:Evidence-based default selection")
def test_connect_strongest_declared_source_and_keep_explicit_preference(connected):
    from datetime import datetime, timedelta, timezone

    from tinyassets.commons_catalog import serving_binding_candidates
    from tinyassets.onboarding.subscription_connect import finish
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.providers.served_model_plan import prepare_owned_model_plan
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime
    from tinyassets.storage.model_preferences import ModelPreferenceStore

    doc = connected.source.document()
    doc['ranking'] = {'source': 'fixture-benchmark',
        'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        'scores': {'default': {'agentic': 50}}}
    (connected.home / '.ta-cli/sources/fake-cli.json').write_text(json.dumps(doc))
    SubscriptionRuntime('fake-cli').accept()
    first = finish('fake-cli')
    assert first['status'] == 'connected'
    stronger = json.loads(json.dumps(doc))
    stronger['id'] = 'stronger-cli'
    stronger['ranking']['scores']['default']['agentic'] = 100
    (connected.home / '.ta-cli/sources/stronger-cli.json').write_text(json.dumps(stronger))
    runtime = SubscriptionRuntime('stronger-cli')
    (connected.home / '.fake-login').write_text('stronger-cli-login')
    runtime.install()
    runtime.accept()
    second = finish('stronger-cli')
    assert second['status'] == 'connected'
    agent, = serving_binding_candidates(connected.home, universe_id='center', owner_user_id='owner')
    def plan():
        return prepare_owned_model_plan(base=connected.home, universe=connected.home / 'center',
            owner='owner', agent=agent, needs_tools=False)
    chosen = plan()
    assert chosen.plan.next_candidate('owner', 'center').connection_id == second['provider']
    store = ModelPreferenceStore(connected.home)
    store.save('owner', 'center', expected_generation=0,
        policy=ModelPreferences('explicit', ModelRef(first['provider'], 'default'), ()),
        require_current_home=True)
    assert plan().plan.next_candidate('owner', 'center').connection_id == first['provider']

@pytest.mark.verifies("box-subscription-sources:Generic invocation and selection")
def test_authorized_router_calls_subscription(connected):
    from tinyassets.auth import middleware as auth
    from tinyassets.commons_catalog import serving_binding_candidates
    from tinyassets.config import load_universe_config
    from tinyassets.onboarding.subscription_connect import finish
    from tinyassets.providers.base import ModelConfig, UniverseContext
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.router import ProviderRouter

    result = finish('fake-cli')
    agent, = serving_binding_candidates(connected.home, universe_id='center', owner_user_id='owner')
    reserved = auth.reserve_provider_request(principal_id='owner', session_id='native-test',
        request_id='native-request', tool_name='converse')
    capability = auth.claim_provider_request(reserved, tool_name='converse')
    carrier = auth.mint_provider_request_carrier(universe_id='center',
        agent_binding_id=agent['agent_binding_id'], binding_revision=agent['revision'],
        operation='converse')
    context = UniverseContext(universe_dir=connected.home / 'center',
        config=load_universe_config(connected.home / 'center'), provider_request=carrier,
        model_selection=ModelRef(result['provider'], 'default'))
    from tinyassets.request_budget import TurnRequestBudget
    budget = TurnRequestBudget('owner', 'center')
    budget.persist(connected.home)
    try:
        response = asyncio.run(ProviderRouter({}).call('writer', 'routed subscription reply', '',
            ModelConfig(request_budget=budget), operation='converse', universe_context=context))
        assert response.text == 'routed subscription reply'
        assert response.provider == result['provider']
    finally:
        budget.close()
        auth.revoke_provider_request(capability)


def test_disconnect_removes_declared_state_and_owner_install_only(connected, tmp_path, monkeypatch):
    from tinyassets.onboarding.subscription_connect import connected_sources, disconnect
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime

    assert connected_sources() == {"sources": [{"id": "fake-cli", "name": "Fixture"}]}
    image = tmp_path / "shared-image" / "fake-cli"
    image.mkdir(parents=True)
    binary = image / "binary"
    binary.write_bytes(connected.binary.read_bytes())
    installed = binary.read_bytes()
    monkeypatch.setattr(SubscriptionRuntime, "IMAGE_ROOT", image.parent)
    other = connected.home / ".other-login"
    other.write_text("keep")
    assert disconnect("fake-cli")["status"] == "disconnected"
    assert not (connected.home / ".fake-login").exists()
    assert not (connected.directory / "connection").exists()
    assert not connected.directory.exists()
    assert binary.read_bytes() == installed
    assert other.read_text() == "keep"
    assert connected_sources() == {"sources": []}
    assert disconnect("fake-cli")["status"] == "disconnected"


@pytest.mark.verifies("box-subscription-sources:Disconnect connected sources")
def test_disconnect_removes_the_candidate_the_account_can_no_longer_serve(connected):
    from tinyassets.onboarding.subscription_connect import disconnect
    from tinyassets.providers.definition import list_definitions, register_definition
    from tinyassets.providers.served_model_plan import connected_sources

    root = connected.home
    kept = register_definition(universe_id="center", owner_user_id="owner",
        access_method="subscription_cli", protocol="cli_json", model="default", ref="fake-cli")
    # A candidate registered and never assigned has no assignment row at all; its
    # definition still has to go, or the account keeps a broken source forever.
    assert connected_sources(root, root / "center", "owner") == frozenset(
        {"subscription_cli:" + kept.id})
    assert disconnect("fake-cli")["status"] == "disconnected"
    assert list_definitions("center") == []
    assert connected_sources(root, root / "center", "owner") == frozenset()


@pytest.mark.verifies("box-subscription-sources:Disconnect connected sources")
def test_disconnect_retracts_the_broker_descriptor_for_this_owner_only(connected, box):  # noqa: F811
    from tinyassets.broker.owner_sources import publish, read
    from tinyassets.onboarding.subscription_connect import disconnect
    from tinyassets.providers.definition import (
        ProviderDefinition,
        _definition_id,
        register_definition,
    )
    from tinyassets.storage.outbound_connections import ConnectionLedger

    mine = register_definition(universe_id="center", owner_user_id="owner",
        access_method="subscription_cli", protocol="cli_json", model="default", ref="fake-cli")
    fields = dict(universe_id="center", owner_user_id="other", access_method="subscription_cli",
                  protocol="cli_json", ref="fake-cli", model="default")
    theirs = ProviderDefinition(id=_definition_id(**fields), **fields, visibility="private",
                                created_at="2026-10-24T00:00:00+00:00")
    other = ConnectionLedger(box.ledger._db_path, data_root=box.shared,
                             verify_authenticated_principal=lambda: "other")
    publish(other, "other", "other", theirs.as_dict())
    assert [item["id"] for item in read(box.ledger, "ignored")] == [mine.id]

    assert disconnect("fake-cli")["status"] == "disconnected"
    assert read(box.ledger, "ignored") == []
    # The same source id under another principal was never this owner's to withdraw.
    assert [item["id"] for item in read(other, "other")] == [theirs.id]
    # Idempotent: a repeated disconnect finds nothing left to withdraw.
    assert disconnect("fake-cli")["status"] == "disconnected"


def test_changed_record_invalidates_acceptance_before_running_cli(connected, monkeypatch):
    from tinyassets.onboarding.subscription_connect import finish
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime

    doc = connected.source.document()
    doc["invoke"]["cli"]["argv"].append("changed")
    (connected.home / ".ta-cli/sources/fake-cli.json").write_text(json.dumps(doc))
    changed = SubscriptionRuntime("fake-cli")
    monkeypatch.setattr(SubscriptionRuntime, "run", lambda *a, **k: pytest.fail("unaccepted CLI"))
    with pytest.raises(PermissionError, match="reconnect required"):
        changed.invoke("hello")
    with pytest.raises(PermissionError, match="reconnect required"):
        finish("fake-cli")


pytestmark = [
    pytest.mark.verifies(
        'box-subscription-sources:Generic invocation and selection',
        'box-subscription-sources:Broker custody and one-run native leases',
    ),
]

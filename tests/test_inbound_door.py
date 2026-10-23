"""N1 ingress proof and installation identity guards."""

import hashlib
import hmac
import json
import time

import pytest

from tinyassets.broker.inbound_installations import InstallationAuthority
from tinyassets.broker.inbound_verification import (
    InvalidProfile,
    extract_single_installation,
    verify,
)
from tinyassets.owner_router import _headers
from tinyassets.storage.inbound_events import accept, pending

pytestmark = pytest.mark.verifies(
    'n1-inbound-door:One verified ingress maps positively to one box',
    'n1-inbound-door:Installation authority owns shared profiles and mappings',
    'n1-inbound-door:Verification custody and forwarding have one authority',
    'n1-inbound-door:Durable accepted events enter session start inside the box',
)


def _slack(body, *, timestamp=None, secret=b"signing-secret"):
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = "v0=" + hmac.new(
        secret, b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256
    ).hexdigest()
    headers = {"x-slack-request-timestamp": timestamp, "x-slack-signature": signature}
    profile = {
        "method": "hmac-sha256", "signature_header": "x-slack-signature",
        "timestamp_header": "x-slack-request-timestamp", "version": "v0",
        "encoding": "hex", "prefix": "v0=", "max_skew_seconds": 300,
    }
    return raw, headers, profile, secret


def _envelope(*, enterprise=False, shared=False, authorizations=None):
    return {
        "type": "event_callback", "is_ext_shared_channel": shared,
        "authorizations": authorizations if authorizations is not None else [{
            "is_enterprise_install": enterprise,
            "enterprise_id": "E-one", "team_id": "T-one",
        }],
        "event": {"type": "message", "text": "hello"},
    }


def test_slack_hmac_precedes_fixed_workspace_or_org_extraction():
    for enterprise, expected in ((False, ("workspace", "T-one")),
                                 (True, ("enterprise", "E-one"))):
        raw, headers, profile, secret = _slack(_envelope(enterprise=enterprise))
        assert verify(profile, headers, raw, secret=secret)
        assert extract_single_installation(raw, {"content-type": "application/json"}) == expected
        assert not verify(profile, headers, raw + b" ", secret=secret)


def test_slack_ambiguous_and_unsupported_envelopes_are_no_ops():
    examples = [
        _envelope(shared=True),
        _envelope(authorizations=[]),
        _envelope(authorizations=[{"is_enterprise_install": False, "team_id": "T"}] * 2),
        _envelope(authorizations=[{"is_enterprise_install": "false", "team_id": "T"}]),
        _envelope(authorizations=[{"is_enterprise_install": True, "team_id": "T"}]),
    ]
    for body in examples:
        raw, headers, profile, secret = _slack(body)
        assert verify(profile, headers, raw, secret=secret)
        assert extract_single_installation(raw, {"content-type": "application/json"}) is None
    assert extract_single_installation(
        b'{"is_ext_shared_channel":false,"authorizations":[],"x":{"a":1,"a":2}}',
        {"content-type": "application/json"},
    ) is None
    raw, _, _, _ = _slack(_envelope())
    assert extract_single_installation(
        raw, {"content-type": "application/x-www-form-urlencoded"}) is None


def test_slack_rejects_stale_or_oversized_proof_without_parsing():
    raw, headers, profile, secret = _slack(_envelope(), timestamp=int(time.time()) - 301)
    assert not verify(profile, headers, raw, secret=secret)
    assert extract_single_installation(b" " * (1024 * 1024 + 1),
                                       {"content-type": "application/json"}) is None


def test_generic_capability_and_profile_schema():
    token = "capability"
    profile = {"method": "capability", "digest": hashlib.sha256(token.encode()).hexdigest()}
    assert verify(profile, {}, b"any", token=token)
    assert not verify(profile, {}, b"any", token="wrong")
    with pytest.raises(InvalidProfile):
        verify({"method": "expression", "source": "body.owner"}, {}, b"{}")
    with pytest.raises(InvalidProfile):
        verify({"method": "rs256", "key_url": "https://attacker.invalid/key"}, {}, b"{}")


def test_generic_raw_hmac_base64_recipe():
    import base64

    raw = b"{\"message\":\"hello\"}"
    signature = base64.b64encode(hmac.new(b"key", raw, hashlib.sha256).digest()).decode()
    profile = {"method": "hmac-sha256", "signature_header": "x-signature",
               "encoding": "base64", "prefix": "sha256="}
    assert verify(profile, {"X-Signature": "sha256=" + signature}, raw, secret=b"key")
    assert not verify(profile, {"X-Signature": "sha256=" + signature}, raw + b" ", secret=b"key")


def test_install_uses_same_session_and_server_inspected_identity(tmp_path):
    authority = InstallationAuthority(tmp_path / "installations.db")
    state = authority.begin("alice", "browser-a")
    with pytest.raises(PermissionError):
        authority.complete(state, "alice", "browser-b", lambda: ("workspace", "T", "cred"),
                           lambda _: "revoked")
    record = authority.complete(state, "alice", "browser-a",
                                lambda: ("workspace", "T", "cred"), lambda _: "revoked")
    assert record["owner"] == "alice"
    assert record["installation_id"] == "T"
    with pytest.raises(PermissionError):
        authority.complete(state, "alice", "browser-a", lambda: ("workspace", "T", "cred"),
                           lambda _: "revoked")


def test_install_conflicts_preserve_live_and_defer_unknown(tmp_path):
    authority = InstallationAuthority(tmp_path / "installations.db")

    def install(owner, status):
        state = authority.begin(owner, owner + "-browser")
        return authority.complete(state, owner, owner + "-browser",
                                  lambda: ("workspace", "T", owner + "-credential"),
                                  lambda _: status)

    first = install("alice", "revoked")
    with pytest.raises(PermissionError):
        install("bob", "live")
    # The refused callback consumes state; it cannot be replayed after a
    # provider-side credential status change.
    refused_state = authority.begin("bob", "browser")
    with pytest.raises(PermissionError):
        authority.complete(refused_state, "bob", "browser",
                           lambda: ("workspace", "T", "new-ref"), lambda _: "live")
    with pytest.raises(PermissionError):
        authority.complete(refused_state, "bob", "browser",
                           lambda: ("workspace", "T", "new-ref"), lambda _: "revoked")
    with pytest.raises(TimeoutError):
        install("bob", "unknown")
    assert authority.lookup("workspace", "T") == first
    replacement = install("bob", "revoked")
    assert replacement["revision"] > first["revision"]
    assert authority.lookup("workspace", "T") == replacement
    assert authority.for_owner("alice") == []


def test_retirement_is_owner_scoped_and_requires_provider_revocation(tmp_path):
    authority = InstallationAuthority(tmp_path / "installations.db")
    state = authority.begin("alice", "browser")
    record = authority.complete(state, "alice", "browser",
                                lambda: ("workspace", "T", "credential"),
                                lambda _: "revoked")
    with pytest.raises(PermissionError):
        authority.retire("bob", record["binding"], lambda _: "revoked")
    with pytest.raises(PermissionError):
        authority.retire("alice", record["binding"], lambda _: "live")
    authority.retire("alice", record["binding"], lambda _: "revoked")
    assert authority.lookup("workspace", "T") is None


def test_forward_strips_proof_and_caller_identity_case_insensitively():
    incoming = [(b"X-Slack-Signature", b"secret"), (b"x-Hub-Signature-256", b"secret"),
                (b"PROXY-Authorization", b"secret"), (b"Cookie", b"secret"),
                (b"X-Custom-Proof", b"secret"), (b"X-Owner", b"bob"),
                (b"Content-Type", b"application/json")]
    result = _headers(incoming, private=True,
                      proof_headers={b"x-custom-proof", b"x-slack-signature",
                                     b"x-hub-signature-256"})
    assert result == [(b"Content-Type", b"application/json")]


def test_box_inbox_atomic_receipt_replay_and_freshness(tmp_path):
    now = 1_800_000_000
    first = accept(tmp_path, binding="binding", revision=3, event_id="event-1",
                   event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now)
    assert first["accepted"] and first["receipt"]
    replay = accept(tmp_path, binding="binding", revision=3, event_id="event-1",
                    event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now)
    assert replay == {**first, "accepted": False}
    assert len(pending(tmp_path)) == 1
    with pytest.raises(ValueError, match="event_id_conflict"):
        accept(tmp_path, binding="binding", revision=3, event_id="event-1",
               event_at=now, body=b"different", owner="alice", agent_ref="main", now=now)
    with pytest.raises(ValueError, match="stale_event"):
        accept(tmp_path, binding="binding", revision=3, event_id="event-1",
               event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now + 86401)
    second = accept(tmp_path, binding="binding", revision=4, event_id="event-1",
                    event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now)
    assert second["receipt"] != first["receipt"]
    distinct = accept(tmp_path, binding="binding", revision=3, event_id="event-2",
                      event_at=now, body=b"hello", owner="alice", agent_ref="main", now=now)
    assert distinct["receipt"] not in {first["receipt"], second["receipt"]}


def test_receipt_admission_survives_commit_ack_crash(tmp_path, monkeypatch):
    import base64
    import os
    from types import SimpleNamespace

    from tinyassets import conversation_store, owner_runtime
    from tinyassets import webhook_inbound as receiver
    from tinyassets.control_plane import session
    from tinyassets.storage import inbound_events as inbox

    home = tmp_path / 'center'
    home.mkdir()
    monkeypatch.setattr(owner_runtime, '_runtime', owner_runtime.OwnerRuntime(
        'alice', 1001, tmp_path, os.getpid(), None))
    monkeypatch.setattr(session, 'load_agent', lambda *a, **kw: SimpleNamespace(agent_id='main'))
    inbox.configure(tmp_path, 'binding', 1, 'center', 'main')
    now = int(time.time())
    body = b'\x00\xffverbatim'
    proof = {'binding': 'binding', 'revision': 1, 'digest': hashlib.sha256(body).hexdigest()}
    headers = {'Idempotency-Key': 'one', 'X-Event-Timestamp': str(now),
               'X-Signing-Secret': 'super-secret-value'}
    mark = inbox.mark_started
    monkeypatch.setattr(inbox, 'mark_started', lambda *a: (_ for _ in ()).throw(OSError('crash')))
    status, _ = receiver.handle_verified(proof=proof, body=body, headers=headers,
                                         base=tmp_path, owner='alice', now=now)
    assert status == 503
    saved = conversation_store.owner_send_records(home)
    assert len(saved) == 1
    monkeypatch.setattr(inbox, 'mark_started', mark)
    receiver.recover_pending(tmp_path)
    status, result = receiver.handle_verified(proof=proof, body=body, headers=headers,
                                              base=tmp_path, owner='alice', now=now)
    assert status == 202 and result['deduped']
    assert conversation_store.owner_send_records(home) == saved
    assert inbox.receipt(tmp_path, result['receipt'])['body'] == body
    assert saved[0][1] == result['receipt']
    assert base64.b64encode(body).decode('ascii') in saved[0][2]
    assert 'super-secret-value' not in saved[0][2]
    assert receiver.ready_for_send(tmp_path, result['receipt'])
    inbox.configure(tmp_path, 'binding', 1, 'center', 'main', paused=True)
    assert not receiver.ready_for_send(tmp_path, result['receipt'])
    inbox.configure(tmp_path, 'binding', 2, 'center', 'main')
    assert not receiver.ready_for_send(tmp_path, result['receipt'])


def test_breaker_rolling_window_is_durable_and_installation_scoped(tmp_path):
    from tinyassets.inbound_routing import DeliveryBreakers
    breaker = DeliveryBreakers(tmp_path)
    breaker.failure('alice', 'one', 1, now=100)
    breaker.failure('alice', 'one', 1, now=110)
    assert not breaker.tripped('one', 1)
    # Successful requests do not reset failures still in the window.
    breaker.failure('alice', 'one', 1, now=150)
    assert DeliveryBreakers(tmp_path).tripped('one', 1)
    assert not breaker.tripped('two', 1)
    assert breaker.for_owner('bob') == []
    with pytest.raises(PermissionError):
        breaker.ready('bob', 'one', 1)
    breaker.ready('alice', 'one', 1)
    assert not breaker.tripped('one', 1)
    for at in (200, 261, 322):
        breaker.failure('alice', 'one', 1, now=at)
    assert not breaker.tripped('one', 1)


def test_inbox_concurrent_delivery_has_one_receipt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    now = int(time.time())
    def deliver(_):
        return accept(tmp_path, binding='one', revision=1, event_id='one', event_at=now,
                      body=b'data', owner='alice', agent_ref='main')
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(deliver, range(16)))
    assert len({row['receipt'] for row in rows}) == 1
    assert sum(row['accepted'] for row in rows) == 1


def test_migration_requires_drained_legacy_work_and_explicit_agent(tmp_path):
    from tinyassets.storage import inbound_events, webhook_hooks
    with webhook_hooks._connect(tmp_path) as db:
        db.execute("INSERT INTO webhook_hooks VALUES "
                   "('digest','prefix','center','branch',1,NULL,NULL,'alice')")
        db.execute("INSERT INTO webhook_inflight VALUES ('reservation','center',NULL,1)")
    with pytest.raises(RuntimeError, match='drain'):
        inbound_events.migrate_hooks(tmp_path, {'branch': 'main'})
    with webhook_hooks._connect(tmp_path) as db:
        db.execute("UPDATE webhook_inflight SET run_id='finished'")
    assert inbound_events.migrate_hooks(tmp_path, {}, terminal_runs={'finished'}) == ['prefix']
    assert inbound_events.migrate_hooks(
        tmp_path, {'branch': 'main'}, terminal_runs={'finished'}) == []
    assert inbound_events.configuration(tmp_path, 'digest')['agent_ref'] == 'main'


def test_authenticated_router_no_ops_precede_maintenance_and_breaker(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    import httpx
    from starlette.responses import Response

    from tinyassets import owner_router, router_traffic
    from tinyassets.broker import inbound_verification
    from tinyassets.inbound_routing import DeliveryBreakers
    from tinyassets.owner_router import OwnerRouter
    from tinyassets.router_credentials import routing_dispatch
    from tinyassets.routing_snapshot import snapshot

    root = Path(tmp_path).resolve()
    monkeypatch.setenv('TINYASSETS_DATA_DIR', str(root))
    monkeypatch.setenv('TINYASSETS_INBOUND_ENABLED', '1')
    from tinyassets.storage import data_dir
    snap = snapshot(data_dir())
    snap.install(snap.instance, snap.installed()['revision'] + 1, {
        hashlib.sha256(b'alice').hexdigest(): {
            'state': 'admitted', 'socket': '/unused', 'secret': 's' * 43}})
    routing_dispatch(root, {'operation': 'inbound_endpoint', 'binding': 'shared'})
    monkeypatch.setattr(router_traffic, 'admits', lambda *a: False)
    mapped = {'proof': 'mapped', 'owner': 'alice', 'binding': 'installation', 'revision': 3}
    proof = {'proof': 'no_op'}
    monkeypatch.setattr(inbound_verification, 'request_shared', lambda *a: dict(proof))
    async def forbidden(*a, **kw):
        raise AssertionError('no-op or maintenance must not forward')
    monkeypatch.setattr(owner_router, 'forward', forbidden)
    app = OwnerRouter(Response())
    async def exercise():
        nonlocal proof
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            assert (await client.post('/hooks/inbound/shared', content=b'raw')).status_code == 200
            proof = {'proof': 'setup', 'challenge': 'abc'}
            setup = await client.post('/hooks/inbound/shared', content=b'challenge')
            assert setup.status_code == 200 and setup.json()['challenge'] == 'abc'
            proof = {**mapped, 'owner': 'carol'}
            gone = await client.post('/hooks/inbound/shared', content=b'raw')
            assert gone.status_code == 200 and gone.json()['error'] == 'no_op'
            proof = mapped
            for _ in range(3):
                response = await client.post('/hooks/inbound/shared', content=b'raw')
                assert response.status_code == 503
            assert (await client.post('/hooks/inbound/shared', content=b'raw')).status_code == 200
            proof = {**mapped, 'binding': 'other'}
            assert (await client.post('/hooks/inbound/shared', content=b'raw')).status_code == 503
    asyncio.run(exercise())
    assert DeliveryBreakers(root).tripped('installation', 3)
    assert not (root / '.tinyassets.db').exists()


def test_inbound_source_has_no_direct_dispatcher():
    import inspect

    from tinyassets import webhook_inbound
    source = inspect.getsource(webhook_inbound)
    for old in ('enqueue_universe_branch_run', 'emit_event', '_delivery_key', 'handle_hook'):
        assert old not in source


def test_completed_receipts_prune_but_unfinished_sends_do_not(tmp_path):
    from tinyassets.storage import inbound_events as inbox
    now = int(time.time())
    first = accept(tmp_path, binding='b', revision=1, event_id='1', event_at=now,
                   body=b'1', owner='alice', agent_ref='main', now=now)
    inbox.mark_started(tmp_path, first['receipt'], 'thread')
    second = accept(tmp_path, binding='b', revision=1, event_id='2', event_at=now,
                    body=b'2', owner='alice', agent_ref='main', now=now)
    inbox.mark_started(tmp_path, second['receipt'], 'thread')
    inbox.finish(tmp_path, second['receipt'])
    later = now + 87000
    accept(tmp_path, binding='b', revision=1, event_id='3', event_at=later,
           body=b'3', owner='alice', agent_ref='main', now=later)
    assert inbox.receipt(tmp_path, first['receipt'])['completed_at'] is None
    with pytest.raises(LookupError):
        inbox.receipt(tmp_path, second['receipt'])


def _runtime(tmp_path, monkeypatch):
    import os

    from tinyassets import owner_runtime

    monkeypatch.setattr(owner_runtime, '_runtime', owner_runtime.OwnerRuntime(
        'alice', 1001, tmp_path, os.getpid(), None))


def test_generic_identity_freshness_and_capacity(tmp_path):
    from tinyassets import webhook_inbound as receiver
    from tinyassets.storage import inbound_events as inbox

    now = int(time.time())
    inbox.configure(tmp_path, 'binding', 1, 'center', 'main')
    proof = {'binding': 'binding', 'revision': 1, 'digest': hashlib.sha256(b'hello').hexdigest()}
    missing, payload = receiver.handle_verified(
        proof=proof, body=b'hello', headers={}, base=tmp_path, owner='alice', now=now)
    assert (missing, payload['error']) == (400, 'event_identity_required')
    stale, payload = receiver.handle_verified(
        proof=proof, body=b'hello', headers={'Idempotency-Key': 'one',
                                             'X-Event-Timestamp': str(now - 86401)},
        base=tmp_path, owner='alice', now=now)
    assert (stale, payload['error']) == (400, 'stale_event')
    mismatch, payload = receiver.handle_verified(
        proof=proof, body=b'other', headers={'Idempotency-Key': 'one',
                                             'X-Event-Timestamp': str(now)},
        base=tmp_path, owner='alice', now=now)
    assert (mismatch, payload['error']) == (400, 'body_digest_mismatch')
    assert inbox.pending(tmp_path) == []
    huge = b'x' * (1024 * 1024 + 1)
    status, payload = receiver.handle_verified(
        proof={'binding': 'binding', 'revision': 1, 'digest': hashlib.sha256(huge).hexdigest()},
        body=huge, headers={'Idempotency-Key': 'big', 'X-Event-Timestamp': str(now)},
        base=tmp_path, owner='alice', now=now)
    assert (status, payload['error']) == (413, 'too_large')
    for index in range(600):
        accept(tmp_path, binding='binding', revision=1, event_id=f'n{index}', event_at=now,
               body=b'hello', owner='alice', agent_ref='main', now=now)
    limited, payload = receiver.handle_verified(
        proof=proof, body=b'hello', headers={'Idempotency-Key': 'overflow',
                                             'X-Event-Timestamp': str(now)},
        base=tmp_path, owner='alice', now=now)
    assert (limited, payload['error']) == (429, 'rate_limited')


def test_missing_agent_is_a_durable_refusal(tmp_path, monkeypatch):
    from tinyassets import conversation_store
    from tinyassets import webhook_inbound as receiver
    from tinyassets.control_plane import session
    from tinyassets.storage import inbound_events as inbox

    _runtime(tmp_path, monkeypatch)
    (tmp_path / 'center').mkdir()
    inbox.configure(tmp_path, 'binding', 1, 'center', 'main')
    monkeypatch.setattr(session, 'load_agent', lambda *a, **kw: (_ for _ in ()).throw(
        LookupError('missing')))
    now = int(time.time())
    body = b'hello'
    proof = {'binding': 'binding', 'revision': 1, 'digest': hashlib.sha256(body).hexdigest()}
    headers = {'Idempotency-Key': 'one', 'X-Event-Timestamp': str(now)}
    status, payload = receiver.handle_verified(
        proof=proof, body=body, headers=headers, base=tmp_path, owner='alice', now=now)
    assert status == 200 and payload['state'] == 'refused'
    assert payload['error'] == 'agent_unavailable'
    assert conversation_store.owner_send_records(tmp_path / 'center') == []
    assert inbox.pending(tmp_path) == []
    again, replay = receiver.handle_verified(
        proof=proof, body=body, headers=headers, base=tmp_path, owner='alice', now=now)
    assert again == 200 and replay['deduped'] and replay['state'] == 'refused'


def _shared_body(event_id='Ev1', event_time=None):
    when = int(time.time()) if event_time is None else event_time
    return json.dumps({'event_id': event_id, 'event_time': when,
                       'type': 'event_callback'}).encode(), when


def test_shared_withdrawn_binding_is_a_durable_refusal(tmp_path, monkeypatch):
    from tinyassets import conversation_store
    from tinyassets import webhook_inbound as receiver
    from tinyassets.control_plane import session
    from tinyassets.storage import inbound_events as inbox

    _runtime(tmp_path, monkeypatch)
    (tmp_path / 'center').mkdir()
    inbox.configure(tmp_path, 'binding', 1, 'center', 'main', enabled=False)
    starts = []
    monkeypatch.setattr(session, 'load_agent', lambda *a, **kw: starts.append(a))
    body, now = _shared_body()
    proof = {'binding': 'binding', 'revision': 1, 'shared': True,
             'digest': hashlib.sha256(body).hexdigest()}
    status, payload = receiver.handle_verified(
        proof=proof, body=body, headers={}, base=tmp_path, owner='alice', now=now)
    assert status == 200 and payload['state'] == 'refused'
    assert payload['error'] == 'binding_withdrawn' and payload['receipt'].startswith('inb_')
    assert starts == []
    assert conversation_store.owner_send_records(tmp_path / 'center') == []
    assert inbox.pending(tmp_path) == []
    again, replay = receiver.handle_verified(
        proof=proof, body=body, headers={}, base=tmp_path, owner='alice', now=now)
    assert again == 200 and replay['deduped'] and replay['receipt'] == payload['receipt']
    assert starts == []


def test_shared_missing_or_stale_binding_does_not_fail_the_delivery(tmp_path):
    from tinyassets import webhook_inbound as receiver
    from tinyassets.storage import inbound_events as inbox

    body, now = _shared_body()
    proof = {'binding': 'binding', 'revision': 1, 'shared': True,
             'digest': hashlib.sha256(body).hexdigest()}
    status, payload = receiver.handle_verified(
        proof=proof, body=body, headers={}, base=tmp_path, owner='alice', now=now)
    assert status == 200 and payload == {'state': 'refused', 'error': 'binding_withdrawn'}
    assert inbox.pending(tmp_path) == []
    inbox.configure(tmp_path, 'binding', 2, 'center', 'main')
    moved, payload = receiver.handle_verified(
        proof=proof, body=body, headers={}, base=tmp_path, owner='alice', now=now)
    assert moved == 200 and payload['error'] == 'binding_withdrawn'
    assert 'receipt' not in payload
    malformed = b'{}'
    bad, payload = receiver.handle_verified(
        proof={'binding': 'binding', 'revision': 2, 'shared': True,
               'digest': hashlib.sha256(malformed).hexdigest()},
        body=malformed, headers={}, base=tmp_path, owner='alice', now=now)
    assert bad == 200 and payload['error'] == 'event_identity_required'
    stale_body, _ = _shared_body(event_time=now - 86401)
    stale, payload = receiver.handle_verified(
        proof={'binding': 'binding', 'revision': 2, 'shared': True,
               'digest': hashlib.sha256(stale_body).hexdigest()},
        body=stale_body, headers={}, base=tmp_path, owner='alice', now=now)
    assert stale == 200 and payload['error'] == 'stale_event'
    assert inbox.pending(tmp_path) == []


def test_shared_redelivery_after_withdrawal_does_not_open_another_session(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets import conversation_store
    from tinyassets import webhook_inbound as receiver
    from tinyassets.control_plane import session
    from tinyassets.storage import inbound_events as inbox

    _runtime(tmp_path, monkeypatch)
    (tmp_path / 'center').mkdir()
    inbox.configure(tmp_path, 'binding', 1, 'center', 'main')
    monkeypatch.setattr(session, 'load_agent', lambda *a, **kw: SimpleNamespace(agent_id='main'))
    body, now = _shared_body()
    proof = {'binding': 'binding', 'revision': 1, 'shared': True,
             'digest': hashlib.sha256(body).hexdigest()}
    status, payload = receiver.handle_verified(
        proof=proof, body=body, headers={}, base=tmp_path, owner='alice', now=now)
    assert status == 202 and payload['state'] == 'admitted'
    saved = conversation_store.owner_send_records(tmp_path / 'center')
    assert len(saved) == 1
    inbox.configure(tmp_path, 'binding', 1, 'center', 'main', enabled=False)
    again, replay = receiver.handle_verified(
        proof=proof, body=body, headers={}, base=tmp_path, owner='alice', now=now)
    assert again == 202 and replay['deduped'] and replay['receipt'] == payload['receipt']
    assert conversation_store.owner_send_records(tmp_path / 'center') == saved
    inbox.configure(tmp_path, 'binding', 4, 'center', 'other')
    moved, replay = receiver.handle_verified(
        proof=proof, body=body, headers={}, base=tmp_path, owner='alice', now=now)
    assert moved == 202 and replay['receipt'] == payload['receipt']
    assert conversation_store.owner_send_records(tmp_path / 'center') == saved


def test_per_binding_withdrawn_callback_stays_not_found(tmp_path):
    from tinyassets import webhook_inbound as receiver
    from tinyassets.storage import inbound_events as inbox

    now = int(time.time())
    inbox.configure(tmp_path, 'binding', 1, 'center', 'main', enabled=False)
    body = b'hello'
    status, payload = receiver.handle_verified(
        proof={'binding': 'binding', 'revision': 1,
               'digest': hashlib.sha256(body).hexdigest()},
        body=body, headers={'Idempotency-Key': 'one', 'X-Event-Timestamp': str(now)},
        base=tmp_path, owner='alice', now=now)
    assert (status, payload['error']) == (404, 'binding_not_configured')
    assert inbox.pending(tmp_path) == []


def test_inbound_read_timeout_counts_and_other_doors_keep_streaming():
    from tinyassets.owner_router import _forward_timeout

    inbound = _forward_timeout({'inbound_revalidate': lambda: None})
    streamed = _forward_timeout({})
    assert inbound.read == 30 and inbound.connect == 5
    assert streamed.read is None and streamed.connect == 5


def test_subscription_challenge_admits_nothing(tmp_path):
    from tinyassets.storage import inbound_events as inbox
    from tinyassets.webhook_inbound import handle_challenge

    inbox.configure(tmp_path, 'binding', 1, 'center', 'main')
    proof = {'binding': 'binding', 'revision': 1}
    status, payload = handle_challenge(
        proof=proof, query='hub.challenge=abc&token=secret', base=tmp_path, owner='alice')
    assert status == 200 and payload == {'challenge': 'abc'}
    assert inbox.pending(tmp_path) == []
    assert handle_challenge(proof=proof, query='', base=tmp_path, owner='alice')[0] == 404
    assert handle_challenge(
        proof={'binding': 'binding', 'revision': 2}, query='challenge=abc',
        base=tmp_path, owner='alice')[0] == 404


def test_hook_mint_records_the_box_local_agent(tmp_path, monkeypatch):
    from tinyassets.storage import inbound_events, webhook_hooks

    _runtime(tmp_path, monkeypatch)

    def credential(operation, **kwargs):
        assert operation in {'hook_mint', 'hook_revoke'}
        return {'token': 'minted-token-value-0123456789'}

    monkeypatch.setattr('tinyassets.owner_runtime.router_credential', credential)
    token = webhook_hooks.mint(tmp_path, universe_id='center', branch_def_id='main',
                               owner_principal_id='request-ignored', agent_ref='main')
    assert token == 'minted-token-value-0123456789'
    row = inbound_events.configuration(tmp_path, hashlib.sha256(token.encode()).hexdigest())
    assert row['agent_ref'] == 'main' and row['revision'] == 1 and row['enabled']


def test_shared_proof_setup_mapping_and_coverage_conflict(tmp_path):
    from tinyassets.broker.inbound_installations import InstallationAuthority
    from tinyassets.broker.inbound_verification import setup_challenge, verify_shared
    from tinyassets.credential_vault import http_credential_record

    vault = tmp_path / '.broker' / 'platform' / 'credentials'
    vault.mkdir(parents=True)
    (vault / '.credential-vault.json').write_text(json.dumps([
        http_credential_record(destination='platform-signing', token='signing-secret')]))
    authority = InstallationAuthority(tmp_path / '.broker' / 'inbound-installations.db')
    _, _, profile, _ = _slack(_envelope())
    authority.publish_profile('slack', profile, 'platform-signing')
    challenge_body = {'type': 'url_verification', 'challenge': 'plain-challenge'}
    raw, headers, _, _ = _slack(challenge_body)
    headers = {**headers, 'content-type': 'application/json'}
    setup = verify_shared(tmp_path, 'slack', headers, raw)
    assert setup['proof'] == 'setup' and setup['challenge'] == 'plain-challenge'
    assert 'signing-secret' not in json.dumps(setup)
    bad, bad_headers, _, _ = _slack(challenge_body, secret=b'other')
    assert verify_shared(tmp_path, 'slack', {**bad_headers, 'content-type': 'application/json'},
                         bad)['proof'] == 'invalid'
    ambiguous = b'{"type":"url_verification","challenge":"one","challenge":"two"}'
    assert setup_challenge(ambiguous, {'content-type': 'application/json'}) is None
    state = authority.begin('alice', 'browser')
    record = authority.complete(state, 'alice', 'browser',
                                lambda: ('workspace', 'T-one', 'credential'), lambda _: 'revoked')
    event, event_headers, _, _ = _slack(_envelope())
    mapped = verify_shared(tmp_path, 'slack', {**event_headers, 'content-type': 'application/json'},
                           event)
    assert mapped['proof'] == 'mapped' and mapped['owner'] == 'alice'
    assert mapped['binding'] == record['binding'] and mapped['revision'] == record['revision']
    assert 'credential' not in json.dumps(mapped)
    inspected = []
    overlap = authority.begin('alice', 'browser-2')
    with pytest.raises(PermissionError, match='coverage'):
        authority.complete(overlap, 'alice', 'browser-2',
                           lambda: ('enterprise', 'T-one', 'other'),
                           lambda ref: inspected.append(ref))
    assert inspected == []
    assert authority.lookup('enterprise', 'T-one') is None
    assert authority.lookup('workspace', 'T-one') == record


def test_erasure_keeps_other_owners_and_the_signing_profile(tmp_path):
    from tinyassets.broker.inbound_installations import InstallationAuthority

    authority = InstallationAuthority(tmp_path / 'installations.db')
    _, _, profile, _ = _slack(_envelope())
    authority.publish_profile('slack', profile, 'platform-signing')

    def install(owner):
        state = authority.begin(owner, owner + '-browser')
        return authority.complete(
            state, owner, owner + '-browser',
            lambda: ('workspace', owner, owner + '-cred'), lambda _: 'revoked')

    alice = install('alice')
    bob = install('bob')
    authority.erase_owner('alice')
    assert authority.lookup('workspace', 'alice') is None
    assert authority.for_owner('bob') == [bob]
    assert authority.profile('slack')['secret_ref'] == 'platform-signing'
    assert alice['owner'] == 'alice'


def test_wake_reconciliation_is_owner_filtered_and_reports_readiness(tmp_path, monkeypatch):
    from pathlib import Path

    from tinyassets import webhook_inbound as receiver
    from tinyassets.broker.inbound_installations import InstallationAuthority, owner_operation
    from tinyassets.inbound_routing import DeliveryBreakers
    from tinyassets.router_credentials import routing_dispatch
    from tinyassets.routing_snapshot import snapshot
    from tinyassets.storage import inbound_events as inbox

    authority = InstallationAuthority(tmp_path / '.broker' / 'inbound-installations.db')

    def install(owner, kind, installation_id):
        state = authority.begin(owner, owner + kind)
        return authority.complete(
            state, owner, owner + kind, lambda: (kind, installation_id, owner + '-cred'),
            lambda _: 'revoked', app='slack')

    alice = install('alice', 'workspace', 'T-alice')
    bob = install('bob', 'workspace', 'T-bob')
    root = Path(tmp_path).resolve()
    snap = snapshot(root)
    snap.install(snap.instance, snap.installed()['revision'] + 1, {
        hashlib.sha256(name.encode()).hexdigest(): {
            'state': 'admitted', 'socket': '/unused', 'secret': 's' * 43}
        for name in ('alice', 'bob')})
    calls = []

    def routing(root, request):
        calls.append(request)
        if request['operation'] == 'inbound_state':
            return {'op': 'ROUTING_SNAPSHOT_IS', 'result': {'breakers': [
                {'binding': alice['binding'], 'revision': alice['revision'], 'tripped': 1}]}}
        assert request == {'operation': 'inbound_ready', 'owner': 'alice',
                           'binding': alice['binding'], 'revision': alice['revision']}
        return {'op': 'ROUTING_SNAPSHOT_IS', 'result': {'ready': True}}

    monkeypatch.setattr('tinyassets.router_credentials.routing_request', routing)
    view = owner_operation(tmp_path, 'alice', {'operation': 'snapshot'})
    assert [row['binding'] for row in view['installations']] == [alice['binding']]
    assert view['installations'][0]['degraded'] is True
    assert 'credential' not in json.dumps(view)
    with pytest.raises(PermissionError):
        owner_operation(tmp_path, 'alice', {'operation': 'ready', 'binding': bob['binding'],
                                            'revision': bob['revision']})
    with pytest.raises(ValueError):
        owner_operation(tmp_path, 'alice', {'operation': 'publish_profile'})
    assert owner_operation(tmp_path, 'alice', {
        'operation': 'ready', 'binding': alice['binding'],
        'revision': alice['revision']}) == {'ready': True}

    breaker = DeliveryBreakers(root)
    for at in (1, 2, 3):
        breaker.failure('alice', alice['binding'], alice['revision'], now=at)
    assert routing_dispatch(root, {
        'operation': 'inbound_ready', 'owner': 'alice', 'binding': alice['binding'],
        'revision': alice['revision']}) == {'ready': True}
    assert not breaker.tripped(alice['binding'], alice['revision'])
    for at in (10, 11, 12):
        breaker.failure('alice', alice['binding'], alice['revision'], now=at)
    with pytest.raises(PermissionError):
        routing_dispatch(root, {
            'operation': 'inbound_ready', 'owner': 'bob', 'binding': alice['binding'],
            'revision': alice['revision']})

    inbox.configure(tmp_path, alice['binding'], alice['revision'], 'center', 'main')
    inbox.configure(tmp_path, 'generic', 1, 'center', 'main')
    inbox.record_shared(tmp_path, alice['binding'])
    woken = []

    def channel(center, document):
        woken.append(document['request'])
        if document['request']['operation'] == 'snapshot':
            return {'installations': [{**view['installations'][0], 'degraded': True}]}
        return {'ready': True}

    monkeypatch.setattr('tinyassets.owner_channel.request', channel)
    receiver.reconcile_on_wake(tmp_path)
    assert [item['operation'] for item in woken] == ['snapshot', 'ready']
    assert inbox.configuration(tmp_path, alice['binding'])['enabled']
    receiver.reconcile_on_wake(tmp_path)
    woken.clear()

    def empty(center, document):
        woken.append(document['request']['operation'])
        return {'installations': []}

    monkeypatch.setattr('tinyassets.owner_channel.request', empty)
    receiver.reconcile_on_wake(tmp_path)
    assert woken == ['snapshot']
    assert inbox.configuration(tmp_path, alice['binding'])['enabled'] == 0
    assert inbox.configuration(tmp_path, 'generic')['enabled'] == 1
    inbox.configure(tmp_path, alice['binding'], alice['revision'], 'center', 'main')
    monkeypatch.setattr('tinyassets.owner_channel.request', empty)
    receiver.reconcile_on_wake(tmp_path)
    assert inbox.configuration(tmp_path, alice['binding'])['enabled'] == 0


def test_capability_forward_survives_when_secret_verification_is_down(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    import httpx
    from starlette.responses import JSONResponse, Response

    from tinyassets import owner_router, router_traffic
    from tinyassets.broker import inbound_verification
    from tinyassets.owner_router import OwnerRouter
    from tinyassets.router_credentials import HookOwners, routing_dispatch
    from tinyassets.routing_snapshot import snapshot

    root = Path(tmp_path).resolve()
    monkeypatch.setenv('TINYASSETS_DATA_DIR', str(root))
    monkeypatch.setenv('TINYASSETS_INBOUND_ENABLED', '1')
    from tinyassets.storage import data_dir
    snap = snapshot(data_dir())
    snap.install(snap.instance, snap.installed()['revision'] + 1, {
        hashlib.sha256(b'alice').hexdigest(): {
            'state': 'admitted', 'socket': '/unused', 'secret': 's' * 43}})
    HookOwners(root).publish(hashlib.sha256(b'capability').hexdigest(), 'alice')
    routing_dispatch(root, {'operation': 'inbound_endpoint', 'binding': 'shared'})
    monkeypatch.setattr(router_traffic, 'admits', lambda *a: True)
    verify_calls = []

    def down(*args, **kwargs):
        verify_calls.append(args)
        raise OSError('broker down')

    monkeypatch.setattr(inbound_verification, 'request_shared', down)
    forwarded = []

    async def fake_forward(scope, receive, send, identity, route):
        forwarded.append((scope['method'], scope['query_string'], identity.user_id))
        await JSONResponse({'ok': True}, status_code=202)(scope, receive, send)

    monkeypatch.setattr(owner_router, 'forward', fake_forward)

    def resolve(owner):
        return owner_router.BoxRoute(Path('/unused'), 's' * 43)

    app = OwnerRouter(Response(), resolver=resolve)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            assert (await client.get('/hooks/inbound/shared')).status_code == 404
            assert verify_calls == []
            posted = await client.post('/hooks/inbound/capability', content=b'raw',
                                       params={'token': 'capability'})
            assert posted.status_code == 202
            assert forwarded == [('POST', b'', 'alice')]
            assert (await client.post('/hooks/inbound/shared', content=b'raw')).status_code == 503
            assert len(verify_calls) == 1 and len(forwarded) == 1
            monkeypatch.setattr(inbound_verification, 'request_shared',
                                lambda *a, **k: {'proof': 'setup', 'challenge': 'abc'})
            setup = await client.post('/hooks/inbound/shared', content=b'challenge')
            assert setup.status_code == 200 and setup.json() == {'challenge': 'abc'}
            assert len(forwarded) == 1
            challenge = await client.get('/hooks/inbound/capability', params={
                'hub.challenge': 'box-challenge', 'token': 'capability'})
            assert challenge.status_code == 202
            method, query, owner = forwarded[-1]
            assert (method, owner) == ('GET', 'alice')
            assert b'hub.challenge=box-challenge' in query and b'capability' not in query
            assert (await client.put('/hooks/inbound/capability')).status_code == 405
            huge = await client.post('/hooks/inbound/capability', content=b'x' * (1024 * 1024 + 1))
            assert huge.status_code == 413

    asyncio.run(exercise())


def test_only_the_router_role_verifies_inbound(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from tinyassets import rpc_frames as rf
    from tinyassets.broker.admission import require_admitted
    from tinyassets.broker.server import BOX, OWNER, _Connection

    class Peer:
        def __init__(self, role):
            self._role = role
            self._box_owner = 'alice'
            self._server = SimpleNamespace(_owner_identities=None, _data_root=tmp_path)
            self.sent = []

        async def send_async(self, frame):
            self.sent.append(frame)

    async def exercise():
        box = Peer(BOX)
        await _Connection._connection_op(box, 'INBOUND_VERIFY', {
            'op': 'INBOUND_VERIFY', 'binding': 'slack', 'headers': {}, 'body': ''})
        assert b'BOX_REFUSED' in box.sent[0] and b'INBOUND_VERIFY_IS' not in box.sent[0]
        monkeypatch.setattr('tinyassets.broker.admission.require_admitted', lambda *a, **k: None)
        admitted = Peer(BOX)
        with pytest.raises(rf.FrameError):
            await _Connection._connection_op(admitted, 'INBOUND_VERIFY', {
                'op': 'INBOUND_VERIFY', 'binding': 'slack', 'headers': {}, 'body': ''})
        assert admitted.sent == []
        router = Peer(OWNER)
        await _Connection._connection_op(router, 'INBOUND_VERIFY', {
            'op': 'INBOUND_VERIFY', 'binding': 'slack', 'headers': {}, 'body': ''})
        assert b'INBOUND_VERIFY_IS' in router.sent[0] and b'unknown' in router.sent[0]
        await _Connection._connection_op(router, 'INBOUND_VERIFY', {
            'op': 'INBOUND_VERIFY', 'binding': 'slack', 'headers': {}, 'body': '',
            'profile': {'method': 'capability', 'digest': 'ab' * 32}})
        assert b'INBOUND_VERIFY_UNAVAILABLE' in router.sent[1]

    del require_admitted
    asyncio.run(exercise())


def test_one_door_and_deferred_transports_have_no_shared_code():
    import inspect
    from pathlib import Path

    from tinyassets import inbound_routing, owner_router, webhook_inbound
    from tinyassets.broker import inbound_verification

    route = Path('tinyassets/universe_server.py').read_text()
    template = '_HookRoute("/hooks/inbound/{binding}", _hooks_endpoint, methods=["GET", "POST"])'
    assert template in route
    start = route.index('async def _hooks_endpoint')
    body = route[start:route.index('_inbound_routes', start)]
    assert 'handle_verified' in body and 'handle_challenge' in body
    assert 'enqueue_universe_branch_run' not in body
    assert '_inbound_event_run_fn' not in route
    shared = '\n'.join(inspect.getsource(module) for module in (
        inbound_verification, inbound_routing, owner_router))
    box = inspect.getsource(webhook_inbound)
    for banned in ('whatsapp', 'teams', 'graph.microsoft', 'mail.google'):
        assert banned not in shared.lower() and banned not in box.lower()
    for store in ('conversation_store', 'inbound_events', 'webhook_hooks'):
        assert store not in shared
    with pytest.raises(inbound_verification.InvalidProfile):
        inbound_verification.verify(
            {'method': 'rs256', 'key_url': 'https://attacker.invalid/key'}, {}, b'{}')

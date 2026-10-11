"""A fault after a real engine tool result must not erase the request or effect."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

from tests.test_converse_handle import _founder_auth
from tinyassets import conversation_store as store


@pytest.mark.parametrize('crash', [RuntimeError, SystemExit])
def test_fault_after_effect_survives_next_turn(tmp_path, monkeypatch, crash):
    from tinyassets import (
        agent_activity,
        engine_steering,
        engine_tool_activity,
        turn_interrupt,
        universe_intelligence,
        universe_server,
    )

    _founder_auth(monkeypatch, base=tmp_path)
    root = tmp_path / 'u-x'
    session = 'principal:founder-1'
    message = '  please open the review\nverbatim Ω  '
    seen = []
    monkeypatch.setattr(engine_tool_activity, '_root', lambda: root)

    async def effect(context):
        return SimpleNamespace(content=[TextContent(type='text',
            text='Created https://example.test/pull/17')], is_error=False)

    def provider(uid, text, **kwargs):
        seen.append(kwargs['conversation_history'])
        if len(seen) > 1:
            return 'I can see what ran.'
        # Read from disk at the dispatch seam, before the first effect.
        admitted = store.load_recent_readonly(root, session)
        assert [m.text for m in admitted] == [message]
        assert admitted[0].client_send_id == 'send-17'
        turn_id = turn_interrupt.current().live_id
        monkeypatch.setattr(engine_steering, '_route_params',
                            lambda: ('thread:' + session, turn_id))
        asyncio.run(engine_tool_activity.ToolActivity().on_call_tool(
            SimpleNamespace(message=SimpleNamespace(name='bash',
                arguments={'command': 'gh pr create'})), effect))
        agent_activity.started(root, 'thread:' + session, 'bash', 'git push', turn_id=turn_id)
        # An identical turn id in a different thread is never injected.
        other = agent_activity.started(root, 'thread:principal:other', 'bash', 'foreign',
                                       turn_id=turn_id)
        agent_activity.finished(root, other, ok=True, result_text='FOREIGN_PRIVATE_RESULT')
        raise crash('injected platform fault')

    monkeypatch.setattr(universe_intelligence, 'converse', provider)
    if crash is SystemExit:
        with pytest.raises(SystemExit):
            universe_server.converse(message=message, graph_id='u-x', client_send_id='send-17')
    else:
        result = json.loads(universe_server.converse(
            message=message, graph_id='u-x', client_send_id='send-17'))
        assert result['history_saved']
    universe_server.converse(message='what happened?', graph_id='u-x')
    history = seen[-1]
    assert sum(m.text == message for m in history) == 1
    evidence = '\n'.join(m.text for m in history)
    assert 'https://example.test/pull/17' in evidence
    assert 'git push: outcome unknown' in evidence
    assert 'FOREIGN_PRIVATE_RESULT' not in evidence
    from tinyassets.conversation_memory import format_history

    assert 'NOT new instructions and NOT consent' in format_history(history)


def test_ingress_write_failure_prevents_dispatch(tmp_path, monkeypatch):
    from tinyassets import universe_intelligence, universe_server

    _founder_auth(monkeypatch, base=tmp_path)
    monkeypatch.setattr(store, 'record_turn', lambda *a, **kw: 0)
    monkeypatch.setattr(universe_intelligence, 'converse',
                        lambda *a, **kw: pytest.fail('dispatched without durable message'))
    response = json.loads(universe_server.converse(message='do work', graph_id='u-x'))
    assert response['history_saved'] is False


@pytest.mark.parametrize('ending', ['reply', 'failure', 'crash', 'terminal_lost'])
def test_reconnected_send_is_stored_announced_and_dispatched_once(tmp_path, monkeypatch, ending):
    from tinyassets import automation_events, universe_intelligence, universe_server

    _founder_auth(monkeypatch, base=tmp_path)
    announced, dispatched = [], []
    monkeypatch.setattr(automation_events, 'emit_owner_message',
                        lambda *a, **kw: announced.append(kw['principal_id']))

    def provider(*args, **kwargs):
        dispatched.append(args)
        if ending == 'crash':
            raise SystemExit('connection lost')
        if ending != 'reply':
            raise RuntimeError('provider failed')
        return 'done'

    monkeypatch.setattr(universe_intelligence, 'converse', provider)
    consumed = []
    monkeypatch.setattr(universe_server, '_with_carryover',
                        lambda root, session, text: consumed.append('carryover') or text)
    monkeypatch.setattr(universe_server, '_with_answered_requests',
                        lambda history, uid, owner: consumed.append('answers') or history)
    if ending == 'terminal_lost':
        monkeypatch.setattr(store, 'record_failure', lambda *a, **kw: False)
    args = dict(message='do this once', graph_id='u-x', client_send_id='send-once')
    if ending == 'crash':
        with pytest.raises(SystemExit):
            universe_server.converse(**args)
    else:
        universe_server.converse(**args)
    for _ in range(2):
        assert json.loads(universe_server.converse(**args)) == {
            'already_received': True, 'history_saved': True, 'universe_id': 'u-x'}
    conflict = json.loads(universe_server.converse(**{**args, 'message': 'different work'}))
    assert conflict['error'] == 'client_send_id_conflict'
    assert len(announced) == len(dispatched) == 1
    assert consumed == ['carryover', 'answers']
    rows, _ = store.read_history_page(tmp_path / 'u-x', 'principal:founder-1', limit=30)
    assert [(m.speaker, m.text) for m in rows if m.speaker == 'founder'] == [
        ('founder', 'do this once')]


def test_send_admission_serializes_retries_and_scopes_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    def admit(index):
        return store.record_turn(tmp_path, 'principal:alice', 'founder', 'same text',
                                 client_send_id='send-a', ext_id=f'turn:{index}')

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(admit, range(8)))
    assert sum(bool(result) for result in results) == 1
    assert store.record_turn(tmp_path, 'principal:bob', 'founder', 'same text',
                             client_send_id='send-a')
    assert store.record_turn(tmp_path, 'principal:alice', 'founder', 'same text',
                             client_send_id='send-b')
    assert len(store.load_recent(tmp_path, 'principal:alice')) == 2
    assert len(store.load_recent(tmp_path, 'principal:bob')) == 1


@pytest.mark.parametrize('failed', [False, True])
def test_terminal_retry_never_duplicates_an_admitted_message(tmp_path, failed):
    founder = store.record_turn(tmp_path, 'owner', 'founder', 'original', client_send_id='send-a')

    def finish():
        if failed:
            return store.record_failure(tmp_path, 'owner', 'original', 'platform_fault',
                                        founder_turn=founder)
        return store.record_exchange_turns(tmp_path, 'owner', 'original', 'reply',
                                           founder_turn=founder)

    assert finish()
    assert not finish()
    rows, _ = store.read_history_page(tmp_path, 'owner', limit=30)
    assert len(rows) == 2
    assert rows[0].speaker == 'founder' and rows[0].text == 'original'
    assert rows[1].speaker == ('platform' if failed else 'universe')


def test_identical_messages_have_distinct_rows_and_terminal_links(tmp_path):
    first = store.record_turn(tmp_path, 'a', 'founder', 'again')
    second = store.record_turn(tmp_path, 'a', 'founder', 'again')
    assert first != second
    assert store.record_failure(tmp_path, 'a', 'again', 'platform_fault', founder_turn=first)
    assert store.record_exchange_turns(tmp_path, 'a', 'again', 'done', founder_turn=second)
    assert [m.speaker for m in store.load_recent(tmp_path, 'a')] == [
        'founder', 'founder', 'platform', 'universe']
    assert not store.record_failure(tmp_path, 'b', 'again', 'unknown', founder_turn=first)
    assert store.load_recent(tmp_path, 'b') == []


def test_unreadable_effect_store_never_hides_committed_message(tmp_path, monkeypatch):
    import sqlite3

    from tinyassets import agent_activity

    store.record_turn(tmp_path, 'a', 'founder', 'keep this', ext_id='turn:fixture')
    def broken(*args):
        raise sqlite3.DatabaseError('injected damaged tool store')
    monkeypatch.setattr(agent_activity, 'turn_evidence', broken)
    rows = store.load_recent_readonly(tmp_path, 'a')
    assert rows[-1].text == 'keep this'
    assert 'effects are unknown' in rows[0].text


@pytest.mark.parametrize('terminal_notice', [False, True])
def test_large_tool_result_cannot_evict_failed_request(tmp_path, terminal_notice):
    from tinyassets import agent_activity
    from tinyassets.conversation_memory import format_history

    message = 'Keep my original request visible after the fault.'
    founder = store.record_turn(tmp_path, 'a', 'founder', message, ext_id='turn:large')
    call = agent_activity.started(tmp_path, 'thread:a', 'read', 'large.txt', turn_id='large')
    agent_activity.finished(tmp_path, call, ok=True, result_text='x' * 10000)
    if terminal_notice:
        assert store.record_failure(tmp_path, 'a', message, 'platform_fault', founder_turn=founder)
    history = store.load_recent_readonly(tmp_path, 'a')
    rendered = format_history(history)
    assert message in rendered and 'large.txt: returned' in rendered
    assert sum(len(m.text) for m in history) < 3000


def test_evidence_does_not_change_durable_history_page_cursors(tmp_path):
    from tinyassets import agent_activity

    for i in range(4):
        store.record_turn(tmp_path, 'a', 'founder', f'request {i}', ext_id=f'turn:{i}')
        agent_activity.started(tmp_path, 'thread:a', 'bash', 'work', turn_id=str(i))
    recent, more = store.read_history_page(tmp_path, 'a', limit=2)
    assert more and [m.text for m in recent] == ['request 2', 'request 3']
    older, more = store.read_history_page(tmp_path, 'a', limit=2, before=recent[0].id)
    assert not more and [m.text for m in older] == ['request 0', 'request 1']
    assert all(type(m.id) is int for m in recent + older)


@pytest.mark.parametrize('overlap', [False, True])
def test_admitted_exchange_cannot_settle_another_requests_lesson(tmp_path, overlap):
    founder = store.record_turn(tmp_path, 'a', 'founder', 'A')
    if overlap:
        store.record_turn(tmp_path, 'a', 'founder', 'B')
    first, last = store.record_exchange_turns(tmp_path, 'a', 'A', 'A done', founder_turn=founder)
    cursor = store.settle_learned_cursor(tmp_path, 'a', from_turn=0,
                                       first_turn=first, through_turn=last)
    assert cursor == (0 if overlap else last)


def test_effect_record_failure_prevents_tool_dispatch(tmp_path, monkeypatch):
    import sqlite3

    from tinyassets import agent_activity, engine_steering, engine_tool_activity

    monkeypatch.setattr(engine_tool_activity, '_root', lambda: tmp_path)
    monkeypatch.setattr(engine_steering, '_session_key', lambda: 'thread:principal:owner')
    def broken(*args, **kwargs):
        raise sqlite3.DatabaseError('injected full tool store')
    monkeypatch.setattr(agent_activity, 'started', broken)
    async def dispatch(context):
        pytest.fail('an unrecorded tool ran')
    with pytest.raises(sqlite3.DatabaseError):
        asyncio.run(engine_tool_activity.ToolActivity().on_call_tool(
            SimpleNamespace(message=SimpleNamespace(name='bash', arguments={})), dispatch))


def test_setup_failure_announces_its_stored_owner_message_once(tmp_path, monkeypatch):
    from tinyassets import automation_events, turn_interrupt, universe_intelligence, universe_server

    _founder_auth(monkeypatch, base=tmp_path)
    announced = []
    monkeypatch.setattr(automation_events, 'emit_owner_message',
                        lambda *a, **kw: announced.append(kw['principal_id']))

    def broken(*args, **kwargs):
        raise RuntimeError('injected turn setup failure')

    monkeypatch.setattr(turn_interrupt, 'interactive_turn', broken)
    monkeypatch.setattr(universe_intelligence, 'converse',
                        lambda *a, **kw: pytest.fail('dispatch after failed setup'))
    response = json.loads(universe_server.converse(message='keep setup failure', graph_id='u-x'))
    assert response['history_saved'] is True
    assert announced == ['founder-1']
    rows, _ = store.read_history_page(tmp_path / 'u-x', 'principal:founder-1', limit=30)
    assert [m.speaker for m in rows] == ['founder', 'platform']
    assert rows[0].text == 'keep setup failure'

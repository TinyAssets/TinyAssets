"""Real SQLite admission and concurrent/crashed box dispatch, without providers."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tinyassets import conversation_store as store
from tinyassets.owner_door.send import deliver_owner_send

pytestmark = pytest.mark.verifies("owner-daemon-in-box:One durable owner send intake (S24)")


def rows(home):
    with sqlite3.connect(home / '.conversation_memory.db') as conn:
        return conn.execute('SELECT content,client_send_id FROM conversation_turns').fetchall()


def test_commit_before_dispatch_and_identity_replay(tmp_path):
    calls = []

    def dispatch(number):
        assert rows(tmp_path) == [('verbatim\nmessage', 'one')]
        calls.append(number)
        return {'reply': 'done'}

    first = deliver_owner_send(tmp_path, 'thread', 'one', 'verbatim\nmessage', {}, dispatch)
    assert deliver_owner_send(tmp_path, 'thread', 'one', 'verbatim\nmessage', {}, dispatch) == first
    assert calls == [1]
    assert deliver_owner_send(tmp_path, 'thread', 'one', 'different', {}, dispatch)['error'] == (
        'client_send_id_conflict')
    assert deliver_owner_send(tmp_path, 'thread', 'one', 'verbatim\nmessage', {'changed': True},
                   dispatch)['error'] == 'client_send_id_conflict'


def test_busy_send_commits_independently_then_dispatches(tmp_path):
    entered, finish, second_stored = Event(), Event(), Event()
    order = []

    def first(number):
        entered.set()
        assert finish.wait(10)
        order.append('first')
        return {'reply': 'first'}

    def second(number):
        order.append('second')
        return {'reply': 'second'}

    with ThreadPoolExecutor(3) as pool:
        one = pool.submit(deliver_owner_send, tmp_path, 'thread', 'one', 'first', {}, first)
        assert entered.wait(10)
        two = pool.submit(deliver_owner_send, tmp_path, 'thread', 'two', 'second', {}, second)
        # Observe the real database while dispatch is blocked, not a patched writer.
        import time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if len(rows(tmp_path)) == 2:
                second_stored.set()
                break
            time.sleep(.01)
        try:
            assert second_stored.is_set()
            assert two.result(timeout=2)['state'] == 'pending'
        finally:
            finish.set()
        assert one.result()['reply'] == 'first'
        replay = deliver_owner_send(tmp_path, 'thread', 'two', 'second', {}, second)
        assert replay['reply'] == 'second'
    assert order == ['first', 'second']
    assert rows(tmp_path) == [('first', 'one'), ('second', 'two')]


def test_process_death_before_and_after_dispatch(tmp_path):
    script = '''
import os, sys
from pathlib import Path
from tinyassets import conversation_store as store
from tinyassets.owner_door.send import deliver_owner_send
home=Path(sys.argv[1])
if sys.argv[2]=='pending':
    store.admit_owner_send(home,'thread','one','message',{})
    os._exit(17)
deliver_owner_send(home,'thread','one','message',{},lambda n: os._exit(17))
'''
    for state in ('pending', 'running'):
        home = tmp_path / state
        home.mkdir()
        result = subprocess.run([sys.executable, '-c', script, str(home), state],
                                env=dict(os.environ), check=False)
        assert result.returncode == 17
        calls = []
        receipt = deliver_owner_send(home, 'thread', 'one', 'message', {},
                          lambda n: calls.append(n) or {'reply': 'resumed'})
        assert calls == ([1] if state == 'pending' else [])
        assert receipt.get('reply') == ('resumed' if state == 'pending' else None)
        assert len(rows(home)) == 1


def test_storage_failure_never_dispatches(tmp_path):
    calls = []
    with pytest.raises(sqlite3.OperationalError):
        deliver_owner_send(tmp_path / 'absent', 'thread', 'one', 'message', {}, calls.append)
    assert calls == []


def test_setup_resume_reuses_admission_and_publishes_once(tmp_path):
    published = []
    first = deliver_owner_send(tmp_path, 'thread', 'one', 'message', {},
                               lambda n: {'retryable_setup': True},
                               publish=lambda: published.append('event'))
    assert first['retryable_setup']
    second = deliver_owner_send(tmp_path, 'thread', 'one', 'message', {},
                                lambda n: {'reply': 'connected'},
                                publish=lambda: published.append('event'))
    assert second['reply'] == 'connected'
    assert published == ['event']
    assert rows(tmp_path) == [('message', 'one')]


def test_queued_messages_are_not_previous_context(tmp_path):
    store.admit_owner_send(tmp_path, 'thread', 'one', 'first', {})
    store.admit_owner_send(tmp_path, 'thread', 'two', 'next', {})
    assert all(m.speaker == 'platform' for m in store.load_send_history(tmp_path, 'thread'))
    store.owner_send_state(tmp_path, 'thread', 'one', state='completed', result={'reply': 'ok'})
    history = store.load_send_history(tmp_path, 'thread', current_send_id='two')
    assert [m.text for m in history] == ['first']


def test_reply_names_its_send_even_with_an_intervening_admission(tmp_path):
    first = store.admit_owner_send(tmp_path, 'thread', 'one', 'first', {})
    store.admit_owner_send(tmp_path, 'thread', 'two', 'next', {})
    store.record_exchange_turns(tmp_path, 'thread', 'first', 'reply', founder_turn=first)
    assert store.load_recent(tmp_path, 'thread')[-1].reply_to_client_send_id == 'one'


def test_terminal_commit_survives_missing_wire_receipt(tmp_path):
    number = store.admit_owner_send(tmp_path, 'thread', 'one', 'message', {})
    store.owner_send_state(tmp_path, 'thread', 'one', state='running')
    store.bind_send_execution(tmp_path, 'thread', number, 'live-turn')
    store.record_exchange_turns(tmp_path, 'thread', 'message', 'saved', founder_turn=number)
    calls = []
    result = deliver_owner_send(tmp_path, 'thread', 'one', 'message', {}, calls.append)
    assert result['reply'] == 'saved'
    assert calls == []


def test_selected_run_projects_onto_admitted_row(tmp_path):
    from types import SimpleNamespace

    from tinyassets.storage.conversation_run_admissions import _write_pair

    number = store.admit_owner_send(tmp_path, 'thread', 'one', 'message', {})
    scope = SimpleNamespace(home=tmp_path, session='thread', check=lambda: None)
    row = {'admission_id': 'a' * 32, 'projection_state': 'pending',
           'intent_json': json.dumps({'message': 'message'}),
           'context_json': json.dumps({'owner_send_turn': number}),
           'terminal_json': json.dumps({'speaker': 'universe', 'content': 'reply',
                                        'execution': None, 'failure': None})}
    assert _write_pair(scope, row) == number
    assert _write_pair(scope, row) == number
    assert rows(tmp_path) == [('message', 'one'), ('reply', '')]


def test_startup_resumes_pending_without_browser_exactly_once(tmp_path):
    from tinyassets.owner_door.send import resume_pending_sends

    store.admit_owner_send(tmp_path, 'thread', 'one', 'saved', {})
    calls = []
    def replay(session, send_id, message, options):
        return deliver_owner_send(tmp_path, session, send_id, message, options,
                                  lambda n: calls.append(n) or {'reply': 'done'})
    resume_pending_sends(tmp_path, replay)
    resume_pending_sends(tmp_path, replay)
    assert calls == [1]


@pytest.mark.parametrize('shutdown', ['drain', 'stop'])
def test_recovery_drain_leaves_later_send_pending_for_next_start(tmp_path, monkeypatch,
                                                                  shutdown):
    from types import SimpleNamespace

    from tinyassets import universe_server
    from tinyassets.owner_activity import Activity
    from tinyassets.owner_centers import Centers
    from tinyassets.owner_door.send import start_recovery

    centers = Centers(tmp_path)
    centers.create('home')
    home = tmp_path / 'home'
    home.mkdir(exist_ok=True)
    for ident in ('one', 'two'):
        store.admit_owner_send(home, 'principal:owner', ident, ident, {})
    gate = Activity()
    monkeypatch.setattr('tinyassets.owner_activity.activity', gate)
    calls = []
    finished = Event()

    def converse(message, **kwargs):
        ident = kwargs['client_send_id']
        def dispatch(number):
            calls.append(ident)
            if ident == 'one':
                if shutdown == 'drain':
                    gate.status(snapshot=True)
                else:
                    stop.set()
            return {'reply': ident}
        deliver_owner_send(home, 'principal:owner', ident, message, {}, dispatch)
        if ident == 'one':
            finished.set()

    monkeypatch.setattr(universe_server, 'converse', converse)
    stop = Event()
    worker = start_recovery(SimpleNamespace(root=tmp_path, principal='owner'), stop)
    try:
        assert finished.wait(5)
        assert store.owner_send_state(home, 'principal:owner', 'two')[0] == 'pending'
    finally:
        stop.set()
        worker.join(5)
    assert calls == ['one']
    assert store.owner_send_state(home, 'principal:owner', 'two')[0] == 'pending'
    gate.draining = False
    from tinyassets.owner_door.send import resume_pending_sends
    resume_pending_sends(home, lambda thread, ident, message, options:
                         deliver_owner_send(home, thread, ident, message, options,
                                            lambda number: calls.append(ident) or {'reply': ident}))
    resume_pending_sends(home, lambda *args: pytest.fail('replayed completed send'))
    assert calls == ['one', 'two']


def test_history_saved_is_not_overridden(tmp_path):
    result = deliver_owner_send(tmp_path, 'thread', 'one', 'saved', {},
                                lambda n: {'error': 'failed', 'history_saved': False})
    assert result['history_saved'] is False


def test_busy_thread_does_not_block_other_thread_or_waiter(tmp_path):
    entered, finish = Event(), Event()
    def blocked(number):
        entered.set()
        assert finish.wait(10)
        return {'reply': 'done'}
    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(deliver_owner_send, tmp_path, 'a', '1', 'first', {}, blocked)
        assert entered.wait(5)
        try:
            other = pool.submit(deliver_owner_send, tmp_path, 'b', '2', 'other', {},
                                lambda n: {'reply': 'independent'})
            assert other.result(timeout=2)['reply'] == 'independent'
            waiter = pool.submit(deliver_owner_send, tmp_path, 'a', '3', 'queued', {},
                                 lambda n: {'reply': 'later'})
            assert waiter.result(timeout=2)['state'] == 'pending'
        finally:
            finish.set()
        first.result()


def test_unfinished_history_has_honest_status(tmp_path):
    for state in ('pending', 'held', 'running'):
        store.admit_owner_send(tmp_path, 'thread', state, state + ' message', {})
        store.owner_send_state(tmp_path, 'thread', state, state=state)
    history = store.load_send_history(tmp_path, 'thread')
    assert len(history) == 3
    assert all(m.speaker == 'platform' for m in history)
    assert 'pending' in history[0].text
    assert 'held' in history[1].text
    assert 'uncertain' in history[2].text

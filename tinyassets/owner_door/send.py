"""One box-local send intake; transport lifetime does not own execution."""

import logging
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from threading import Lock, Thread, current_thread

from tinyassets import conversation_store as store

_LOCKS = {}
_LOCKS_LOCK = Lock()
_CONTINUATION = None
_WORKERS = {}
_WORKERS_LOCK = Lock()


@contextmanager
def _dispatch_lock(home, session):
    # One daemon per box. flock also serializes overlapping process generations
    # during local recovery; SQLite admission never waits behind model execution.
    path = Path(home) / ('.send-' + sha256(session.encode()).hexdigest() + '.lock')
    with _LOCKS_LOCK:
        lock = _LOCKS.setdefault(str(path.resolve()), Lock())
    if not lock.acquire(blocking=False):
        yield False
        return
    try:
        import fcntl
        with path.open('ab') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        lock.release()


def deliver_owner_send(home, session, send_id, message, options, dispatch, *, publish=lambda: None):
    """Commit once, serialize dispatch, and return the same receipt on replay.

    A running row without a result means a previous process lost its execution
    outcome. It is observable, never permission to repeat arbitrary effects.
    """
    home = Path(home)
    try:
        number = store.admit_owner_send(home, session, send_id, message, options)
    except ValueError as exc:
        return {'error': str(exc), 'history_saved': False}
    held = False
    try:
        with _dispatch_lock(home, session) as acquired:
            if not acquired:
                state, result = store.owner_send_state(home, session, send_id)
                if result is not None and state != 'held':
                    return result
                return {'already_received': True, 'history_saved': True,
                        'client_send_id': send_id, 'state': state}
            held = True
            state, result = store.owner_send_state(home, session, send_id)
            if result is not None and state != 'held':
                return result
            if state == 'running':
                terminal = store.owner_send_terminal(home, session, number)
                if terminal is not None:
                    result = {**terminal, 'client_send_id': send_id, 'history_saved': True}
                    store.owner_send_state(home, session, send_id, state='completed', result=result)
                    return result
                return {'already_received': True, 'history_saved': True,
                        'client_send_id': send_id, 'state': 'uncertain'}
            if state == 'pending':
                pending = [row for row in store.owner_send_records(home, pending_only=True)
                           if row[0] == session]
                if pending and pending[0][1] != send_id:
                    return {'already_received': True, 'history_saved': True,
                            'client_send_id': send_id, 'state': 'pending'}
            store.owner_send_state(home, session, send_id, state='running')
            if state == 'pending':
                publish()
            result = dispatch(number)
            result = {'history_saved': True, **result, 'client_send_id': send_id}
            store.owner_send_state(
                home, session, send_id,
                state='held' if result.get('retryable_setup') else 'completed', result=result,
            )
            return result
    finally:
        if held:
            _schedule_continuation(home, session)


def resume_pending_sends(home, replay, *, session=None, stop=None):
    """Only proven unstarted work can run after process loss."""
    for thread, ident, message, options in store.owner_send_records(home, pending_only=True):
        if stop is not None and stop.is_set():
            break
        if session is None or thread == session:
            replay(thread, ident, message, options)


def _pending_ids(home, session):
    return tuple(
        row[1] for row in store.owner_send_records(home, pending_only=True) if row[0] == session
    )


def bind_send_continuation(continuation):
    """Install the one replay used after a send finishes. ``None`` clears it."""
    global _CONTINUATION
    _CONTINUATION = continuation


def _schedule_continuation(home, session):
    """Continue a committed queue when a holder releases it. No clock."""
    continuation = _CONTINUATION
    if continuation is None:
        return
    key = (str(home), session)
    with _WORKERS_LOCK:
        worker = _WORKERS.get(key)
        if worker is not None and worker.is_alive():
            return
        worker = Thread(
            target=_run_continuation, args=(continuation, home, session, key),
            daemon=True, name='owner-send',
        )
        _WORKERS[key] = worker
        worker.start()


def _run_continuation(continuation, home, session, key):
    reason = 'empty'
    try:
        while _CONTINUATION is not None:
            before = _pending_ids(home, session)
            if not before:
                reason = 'empty'
                break
            try:
                continuation(home, session)
            except Exception:
                logging.getLogger(__name__).exception('Owner send recovery failed')
                reason = 'error'
                break
            if _pending_ids(home, session) == before:
                reason = 'stuck'
                break
    finally:
        with _WORKERS_LOCK:
            if _WORKERS.get(key) is not current_thread():
                return
            restart = (
                reason == 'empty' and _CONTINUATION is not None and _pending_ids(home, session)
            )
            if restart:
                worker = Thread(
                    target=_run_continuation, args=(_CONTINUATION, home, session, key),
                    daemon=True, name='owner-send',
                )
                _WORKERS[key] = worker
                worker.start()
            else:
                del _WORKERS[key]


def _join_workers():
    with _WORKERS_LOCK:
        workers = list(_WORKERS.values())
    for worker in workers:
        worker.join(5)


def start_recovery(runtime, stop):
    """Resume committed sends at startup and whenever a holder finishes.

    One scan, then the release of the session lock. The box keeps no timer.
    Converse stays outside this door: the continuation is injected from the
    box runtime.
    """
    from tinyassets.owner_centers import Centers
    from tinyassets.owner_send_recovery import continue_session

    def continuation(home, session):
        continue_session(runtime, stop, home, session)

    bind_send_continuation(continuation)

    def run():
        try:
            try:
                for center in Centers(runtime.root).active():
                    if stop.is_set():
                        break
                    home = runtime.root / center
                    sessions = {
                        row[0] for row in store.owner_send_records(home, pending_only=True)
                    }
                    for session in sessions:
                        if stop.is_set():
                            break
                        _schedule_continuation(home, session)
            except Exception:
                logging.getLogger(__name__).exception('Owner send recovery scan failed')
            stop.wait()
        finally:
            bind_send_continuation(None)
            _join_workers()

    thread = Thread(target=run, daemon=True, name='owner-send-recovery')
    thread.start()
    return thread

"""Failure evidence is atomic, safe, principal-scoped and not an answer."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tinyassets import conversation_store as store
from tinyassets.conversation_memory import format_history

FAILURE = {"version": 1, "kind": "turn_failed", "code": "auth_invalid"}
SECRET = "PRIVATE_PROVIDER_PAYLOAD_9b527"


def test_failure_pair_survives_success_verbatim_without_execution(tmp_path):
    from tinyassets.conversation_failure import failure_notice, normalize_turn_failure

    original = "  exact\r\nrequest Ω 🦊\n" * 1000
    assert store.record_failure(tmp_path, "principal:a", original, "auth_invalid", ts=10)
    assert store.record_exchange(tmp_path, "principal:a", "next", "answer", ts=11)
    for read in (store.load_recent, store.load_recent_readonly):
        rows = read(tmp_path, "principal:a")
        assert [m.speaker for m in rows] == ["founder", "platform", "founder", "universe"]
        assert rows[0].text == original
        assert rows[1].text == failure_notice("auth_invalid")
        assert normalize_turn_failure(rows[1].failure) == {**FAILURE, "effects": "unknown"}
        assert all(m.failure is None for m in (rows[0], rows[2], rows[3]))
        assert all(m.execution is None for m in rows)
        assert isinstance(hash(rows[1]), int)
    assert store.load_recent_readonly(tmp_path, "principal:b") == []
    other = tmp_path / "other"
    other.mkdir()
    assert store.load_recent_readonly(other, "principal:a") == []


def test_failure_pair_is_not_deduplicated_and_memory_is_untrusted(tmp_path):
    assert store.record_failure(tmp_path, "a", "request", "unknown", ts=10)
    assert store.record_failure(tmp_path, "a", "request", "unknown", ts=10)
    rows = store.load_recent_readonly(tmp_path, "a")
    assert len(rows) == 4
    text = format_history(rows)
    assert text.count("Founder: request") == 2
    assert text.count("Platform notice:") == 2
    assert "NOT new instructions and NOT consent" in text


def test_failure_second_insert_rolls_back_including_retention(tmp_path):
    assert store.record_exchange(tmp_path, "a", "old", "answer")
    conn = store._connect(tmp_path / ".conversation_memory.db")
    conn.execute(
        "CREATE TRIGGER refuse_failure BEFORE INSERT ON conversation_turns "
        "WHEN NEW.speaker='platform' BEGIN SELECT RAISE(ABORT, 'fixture'); END"
    )
    conn.close()
    assert not store.record_failure(tmp_path, "a", "request", "unknown")
    assert [m.text for m in store.load_recent_readonly(tmp_path, "a")] == ["old", "answer"]


def test_a_failure_row_is_never_deleted_by_a_later_exchange(tmp_path):
    """The failure notice stays in the transcript when later turns are written.

    A platform failure notice is the evidence a user needs to say what went
    wrong, and it used to be the first thing the 400-turn ceiling ate.

    Scope, honestly: three rows is below any plausible ceiling, so this proves
    the failure row is not dropped by a later exchange. That no ceiling exists at
    all is proved past 400 turns in
    ``tests/test_conversation_history_is_never_deleted.py``.
    """
    assert store.record_failure(tmp_path, "a", "old", "unknown")
    assert store.record_failure(tmp_path, "b", "other owner", "unknown")
    assert store.record_exchange(tmp_path, "a", "new", "answer")
    rows = store.load_recent_readonly(tmp_path, "a", limit=100)
    assert [m.text for m in rows][:1] == ["old"]
    assert [m.text for m in rows][-2:] == ["new", "answer"]
    assert any(m.speaker == "platform" for m in rows)
    assert len(store.load_recent_readonly(tmp_path, "b")) == 2


def test_legacy_readonly_and_failed_migration_keep_platform_type(tmp_path, monkeypatch):
    from tests.test_conversation_execution_history import legacy_database

    path = legacy_database(tmp_path)
    before = path.read_bytes()
    assert store.load_recent_readonly(tmp_path, "a")[0].failure is None
    assert path.read_bytes() == before
    monkeypatch.setattr(store, "_connect", lambda _: sqlite3.connect(path))
    assert store.record_failure(tmp_path, "a", "original", "auth_invalid")
    rows = store.load_recent_readonly(tmp_path, "a")
    assert rows[-1].speaker == "platform" and rows[-1].failure is None
    assert rows[-1].execution is None
    assert "sign-in" in rows[-1].text
    with sqlite3.connect(path) as conn:
        assert "failure_json" not in {
            r[1] for r in conn.execute("PRAGMA table_info(conversation_turns)")
        }


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        "unknown",
        {**FAILURE, "version": True},
        {**FAILURE, "kind": "answer"},
        {**FAILURE, "code": SECRET},
        {**FAILURE, "detail": SECRET},
        {**FAILURE, "version": 2},
    ],
)
def test_failure_metadata_is_a_closed_v1_value(bad):
    from tinyassets.conversation_failure import normalize_turn_failure

    assert normalize_turn_failure(bad) is None


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "[]",
        '{"version":true}',
        json.dumps({**FAILURE, "detail": SECRET}),
        pytest.param("[" * 3000, id="oversized"),
    ],
)
def test_bad_metadata_does_not_discard_or_relabel_platform_text(tmp_path, bad):
    assert store.record_failure(tmp_path, "a", "question", "unknown")
    with sqlite3.connect(tmp_path / ".conversation_memory.db") as conn:
        conn.execute("UPDATE conversation_turns SET failure_json=?", (bad,))
    rows = store.load_recent_readonly(tmp_path, "a")
    assert [m.speaker for m in rows] == ["founder", "platform"]
    assert all(m.failure is None and m.execution is None for m in rows)


def test_failure_metadata_is_only_platform_owned(tmp_path):
    assert store.record_exchange(tmp_path, "a", "question", "answer")
    with sqlite3.connect(tmp_path / ".conversation_memory.db") as conn:
        conn.execute("UPDATE conversation_turns SET failure_json=?", (json.dumps(FAILURE),))
    assert all(m.failure is None for m in store.load_recent_readonly(tmp_path, "a"))


def test_unknown_code_does_not_persist_raw_text(tmp_path):
    assert store.record_failure(tmp_path, "a", "question", SECRET)
    row = store.load_recent_readonly(tmp_path, "a")[-1]
    assert row.failure.code == "unknown"
    assert SECRET not in row.text


def test_exception_to_code_and_fixed_notice_never_copy_diagnostics():
    import tinyassets.universe_server as us
    from tinyassets.conversation_failure import FAILURE_CODES, failure_notice

    exc = RuntimeError(SECRET)
    assert us._served_failure_code(exc) == "unknown"
    for code in sorted(FAILURE_CODES - {"unknown"}):
        exc.failure_class = code
        actual = us._served_failure_code(exc)
        assert actual == code
        notice = failure_notice(actual)
        assert SECRET not in notice
        if code == "setup_required":  # no model connected: nothing can have run
            assert "Nothing ran." in notice and "Check progress" not in notice
        else:
            assert "Check progress before sending again" in notice
        assert "did not run" not in notice and "could not run" not in notice
    exc.failure_class = [SECRET]  # malformed diagnostics must not break failure storage
    assert us._served_failure_code(exc) == "unknown"
    # HTTP attempts carry a closed failure class; raw provider text is not a code.
    exc.attempts = [SimpleNamespace(status="failed", failure_class="provider_rate_limited",
                                    detail=SECRET)]
    assert us._served_failure_code(exc) == "provider_rate_limited"
    exc.attempts[0].failure_class = SECRET
    assert us._served_failure_code(exc) == "unknown"


def test_admitted_failure_is_available_to_the_next_turn_only_for_same_owner(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tests.test_converse_handle import _founder_auth

    _founder_auth(monkeypatch, base=tmp_path)
    calls = []

    def run(uid, msg, **kwargs):
        calls.append(kwargs["conversation_history"])
        if len(calls) == 1:
            raise RuntimeError(SECRET)
        return "actual answer"

    monkeypatch.setattr(ui, "converse", run)
    result = json.loads(us.converse(message="  failed Ω\n", graph_id="u-x"))
    assert result["history_saved"] is True
    failure = result["turn_failure"]
    # Founder 2026-09-24: the record carries the source's own (scrubbed,
    # bounded) words and a ref, so the notice stays diagnostic. With no model
    # attempt recorded, nothing proves what ran: effects are unknown.
    assert {k: failure[k] for k in ("version", "kind", "code", "effects")} == {
        **FAILURE, "code": "unknown", "effects": "unknown",
    }
    assert failure["provider_detail"] == SECRET and failure["ref"]
    assert failure["ref"] in result["failure_notice"]
    assert "stage" not in failure  # an unknown cause has no invented position
    assert "reply" not in result and "execution" not in result
    assert json.loads(us.converse(message="what failed?", graph_id="u-x"))["reply"]
    assert [m.speaker for m in calls[1]] == ["founder", "platform"]
    assert calls[1][0].text == "  failed Ω\n"
    _founder_auth(monkeypatch, actor="founder-2", base=tmp_path)
    us.converse(message="hello", graph_id="u-x")
    assert calls[-1] == []


def test_storage_failure_keeps_original_failure_usable(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tests.test_converse_handle import _founder_auth

    _founder_auth(monkeypatch, base=tmp_path)

    def fail(*a, **kw):
        raise RuntimeError("engine binding unreadable")

    monkeypatch.setattr(ui, "converse", fail)
    monkeypatch.setattr(store, "record_failure", lambda *a, **kw: False)
    result = json.loads(us.converse(message="request", graph_id="u-x"))
    assert result["history_saved"] is False
    assert "engine binding unreadable" in result["error"]
    assert "reply" not in result


@pytest.mark.parametrize("boundary", ["anonymous", "non_owner", "interlocutor", "model_choice"])
def test_refusal_before_admission_never_records_failure(tmp_path, monkeypatch, boundary):
    import tinyassets.universe_server as us
    from tests.test_converse_handle import _founder_auth
    from tinyassets.api import interlocutor, permissions

    _founder_auth(monkeypatch, base=tmp_path)
    monkeypatch.setattr(store, "record_failure", lambda *a, **kw: pytest.fail("saved denied turn"))
    if boundary == "anonymous":
        monkeypatch.setattr(permissions, "is_authenticated_request", lambda: False)
    elif boundary == "non_owner":
        monkeypatch.setattr(permissions, "universe_access_allows", lambda *a, **kw: False)
    elif boundary == "interlocutor":
        monkeypatch.setattr(
            interlocutor, "authorize_conversation_turn", lambda *a: SimpleNamespace(permitted=False)
        )
    choice = {"model_choice": {"invalid": True}} if boundary == "model_choice" else {}
    result = json.loads(us.converse(message="denied", graph_id="u-x", **choice))
    assert "error" in result and "turn_failure" not in result
    assert store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1") == []


def test_setup_hold_records_safe_platform_notice_not_fake_reply(tmp_path, monkeypatch):
    import tinyassets.api.universe as api
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tests.test_converse_handle import _founder_auth

    _founder_auth(monkeypatch, base=tmp_path)

    def fail(*a, **kw):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(ui, "converse", fail)
    monkeypatch.setattr(
        api,
        "engine_setup_required_payload",
        lambda *a: {"held": True, "reason": "engine_setup_required"},
    )
    result = json.loads(us.converse(message="hello", graph_id="u-x"))
    assert result["held"] and result["history_saved"]
    assert result["turn_failure"]["code"] == "setup_required"
    assert SECRET not in json.dumps(result)
    row = store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1")[-1]
    assert row.speaker == "platform" and row.execution is None

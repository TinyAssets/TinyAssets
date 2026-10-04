"""Historical answer provenance is optional, reply-owned and non-authorizing."""

import json
import sqlite3

import pytest

from tinyassets import conversation_store as store
from tinyassets.conversation_memory import Msg, format_history

RECEIPT = {"provider": "owned-provider", "model": "actual-model", "model_status": "reported"}


def test_exchange_receipt_is_paired_and_hashable(tmp_path):
    from tinyassets.providers.execution_receipt import normalize_execution_receipt

    assert store.record_exchange(tmp_path, "a", "question", "answer", execution=RECEIPT)
    for read in (store.load_recent, store.load_recent_readonly):
        founder, answer = read(tmp_path, "a")
        assert founder.execution is None
        assert normalize_execution_receipt(answer.execution) == RECEIPT
        assert isinstance(hash(answer), int)
        assert "actual-model" not in format_history([founder, answer])
    assert store.load_recent_readonly(tmp_path, "b") == []
    assert Msg("universe", "old", 12).execution is None


def legacy_database(tmp_path):
    path = tmp_path / ".conversation_memory.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY, session_id TEXT, "
            "turn_no INTEGER, speaker TEXT, content TEXT, ts REAL, ext_id TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO conversation_turns VALUES (1, 'a', 1, 'universe', ?, 12, '')",
            ("verbatim\ntext",),
        )
    return path


def test_legacy_read_does_not_migrate_or_drop_text(tmp_path):
    path = legacy_database(tmp_path)
    before = path.read_bytes()
    result = store.load_recent_readonly(tmp_path, "a")
    assert [(m.text, getattr(m, "execution", None)) for m in result] == [("verbatim\ntext", None)]
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        columns = conn.execute("PRAGMA table_info(conversation_turns)")
        assert "execution_json" not in [row[1] for row in columns]


def test_writer_migrates_legacy_without_rewriting_old_turn(tmp_path):
    legacy_database(tmp_path)
    assert store.record_exchange(tmp_path, "a", "new question", "new answer", execution=RECEIPT)
    rows = store.load_recent_readonly(tmp_path, "a")
    assert rows[0].text == "verbatim\ntext" and rows[0].execution is None
    assert rows[-1].execution.model == "actual-model"


@pytest.mark.parametrize("bad", [
    "not JSON", "[]", json.dumps({**RECEIPT, "secret": "forbidden"}),
    json.dumps({**RECEIPT, "provider": "x" * 401}),
    json.dumps({**RECEIPT, "model": "bad\nlabel"}),
    json.dumps({**RECEIPT, "model_status": "unknown"}),
])
def test_bad_stored_receipt_cannot_drop_message_text(tmp_path, bad):
    assert store.record_exchange(tmp_path, "a", "q", "unchanged", execution=RECEIPT)
    with sqlite3.connect(tmp_path / ".conversation_memory.db") as conn:
        conn.execute("UPDATE conversation_turns SET execution_json=?", (bad,))
    for read in (store.load_recent, store.load_recent_readonly):
        messages = read(tmp_path, "a")
        assert [m.text for m in messages] == ["q", "unchanged"]
        assert all(m.execution is None for m in messages)


def test_an_old_receipt_survives_later_exchanges(tmp_path):
    """The 400-turn retention delete is gone: the oldest pair and its receipt stay.

    This used to assert the opposite -- that the store dropped everything beyond
    a monkeypatched ``RETENTION_TURNS`` of 2. Founder, 2026-09-30: stored turns
    are bytes charged to tier storage, not their own limit, and nothing on the
    platform deletes a user's transcript.

    Scope, honestly: two exchanges is below any plausible ceiling, so this proves
    the RECEIPT survives a later write, not that the ceiling is gone. The ceiling
    is proved gone past 400 turns in
    ``tests/test_conversation_history_is_never_deleted.py``.
    """
    assert store.record_exchange(tmp_path, "a", "old q", "old a", execution=RECEIPT)
    assert store.record_exchange(tmp_path, "a", "new q", "new a")
    rows = store.load_recent_readonly(tmp_path, "a", limit=100)
    assert [m.text for m in rows] == ["old q", "old a", "new q", "new a"]
    assert rows[1].execution is not None, "the old receipt is still readable"


def test_failed_optional_migration_still_stores_text(tmp_path, monkeypatch):
    path = legacy_database(tmp_path)
    monkeypatch.setattr(store, "_connect", lambda _: sqlite3.connect(path))
    assert store.record_exchange(tmp_path, "a", "new q", "new a", execution=RECEIPT)
    rows = store.load_recent_readonly(tmp_path, "a")
    assert [m.text for m in rows][-2:] == ["new q", "new a"]
    assert all(m.execution is None for m in rows)


def test_second_insert_failure_rolls_back_pair_and_receipt(tmp_path):
    path = tmp_path / ".conversation_memory.db"
    conn = store._connect(path)
    conn.execute("CREATE TRIGGER refuse_answer BEFORE INSERT ON conversation_turns "
                 "WHEN NEW.speaker = 'universe' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
    conn.close()
    assert not store.record_exchange(tmp_path, "a", "q", "a", execution=RECEIPT)
    assert store.load_recent_readonly(tmp_path, "a") == []


@pytest.mark.parametrize("bad", [
    None, [], "model", {}, {**RECEIPT, "extra": "no"},
    {**RECEIPT, "provider": ""}, {**RECEIPT, "provider": " space "},
    {**RECEIPT, "provider": "bad\n"}, {**RECEIPT, "model": "x" * 201},
    {**RECEIPT, "model": True}, {**RECEIPT, "model_status": "unknown"},
    {**RECEIPT, "model_status": "invalid"}, {**RECEIPT, "model": ""},
])
def test_receipt_normalizer_is_closed(bad):
    from tinyassets.providers.execution_receipt import normalize_execution_receipt

    assert normalize_execution_receipt(bad) is None


def test_receipt_normalizer_copies_valid_unicode_and_unknown():
    from tinyassets.providers.execution_receipt import normalize_execution_receipt

    for receipt in (RECEIPT, {**RECEIPT, "model": "", "model_status": "unknown"},
                    {**RECEIPT, "model": "模型 <literal>"}):
        result = normalize_execution_receipt(receipt)
        assert result == receipt and result is not receipt


def test_usage_only_stop_round_trips_without_answer_labels_or_delivery_changes(tmp_path):
    from tinyassets.providers.execution_receipt import normalize_execution_receipt

    usage = dict(reserved=0, dispatched=0, closed=True, sources=[], sources_omitted=0,
                 quota_authoritative=False, count_basis="local_provider_dispatch",
                 usage_id="a" * 32)
    assert store.record_exchange(tmp_path, "a", "question", "bounded stop",
                                 execution={"usage": usage})
    for read in (store.load_recent, store.load_recent_readonly):
        founder, answer = read(tmp_path, "a")
        assert founder.execution is None
        assert answer.text == "bounded stop"
        assert normalize_execution_receipt(answer.execution) == {"usage": usage}
        assert answer.execution.provider == answer.execution.model == ""
        assert isinstance(hash(answer), int)

"""Work progress lineage only: no inference permission, provider calls or effects."""

from dataclasses import asdict, replace

import pytest

from tests import test_agent_turn_journal as chat
from tinyassets.storage import agent_turn_records as records
from tinyassets.storage.agent_turn_journal import JournalUnavailable, reset_blockers

journal = chat.journal


def work_root(journal, receipt="work-receipt"):
    return journal.create(
        "owner", "home", prompt="exact work", system="system",
        authority_kind="work_invocation", work_receipt_id=receipt,
    )


def work_candidate(kind, receipt="work-receipt"):
    original = chat.candidate()
    return replace(original, authority_kind="work_invocation", work_receipt_id=receipt)


def begin_work(journal, turn, kind="http", receipt="work-receipt"):
    return journal.begin_round(
        "owner", "home", turn.turn_id, expected_generation=turn.generation,
        candidate=work_candidate(kind, receipt),
    ).snapshot


@pytest.mark.parametrize("kind", ["http"])
def test_work_rounds_are_distinct_and_roundtrip_without_rewriting_chat(journal, kind):
    original = chat.candidate()
    legacy_fields = asdict(original)
    legacy_fields.pop("authority_kind")
    legacy_fields.pop("work_receipt_id")
    expected = records.dump({"version": 1, **legacy_fields})
    assert original.canonical_json() == expected
    assert type(original).from_json(expected) == original

    turn = begin_work(journal, work_root(journal), kind)
    raw = turn.rounds[0].candidate.canonical_json()
    value = records.document(raw)
    assert value["version"] == 3
    assert value["kind"] == "engine_inference"
    assert type(original).from_json(raw) == work_candidate(kind)
    assert turn.authority_kind == "work_invocation" and turn.work_receipt_id == "work-receipt"
    assert journal.get("owner", "home", turn.turn_id) == turn
    assert journal.get("someone-else", "home", turn.turn_id) is None


@pytest.mark.parametrize("kind", ["http"])
@pytest.mark.parametrize("root_kind,round_kind", [
    ("chat", "work"), ("work", "chat"), ("work", "another_work"),
])
def test_round_cannot_change_root_lineage(journal, kind, root_kind, round_kind):
    turn = chat.new(journal) if root_kind == "chat" else work_root(journal)
    if round_kind == "chat":
        candidate = chat.candidate()
    else:
        candidate = work_candidate(
            kind, "other" if round_kind == "another_work" else "work-receipt",
        )
    with pytest.raises(ValueError):
        journal.begin_round("owner", "home", turn.turn_id,
                            expected_generation=turn.generation, candidate=candidate)
    assert journal.get("owner", "home", turn.turn_id) == turn


@pytest.mark.parametrize("kind", ["http"])
@pytest.mark.parametrize("target", ["header", "round"])
def test_read_refuses_persisted_lineage_mismatch(journal, kind, target):
    turn = begin_work(journal, work_root(journal), kind)
    with journal._ledger.connection() as conn:
        if target == "header":
            raw = conn.execute("SELECT input_json FROM agent_turns").fetchone()[0]
            value = records.document(raw)
            value["work_receipt_id"] = "different-receipt"
            conn.execute("UPDATE agent_turns SET input_json = ?", (records.dump(value),))
        else:
            conn.execute("UPDATE agent_turn_rounds SET candidate_json = ?",
                         (work_candidate(kind, "different-receipt").canonical_json(),))
    with pytest.raises(JournalUnavailable):
        journal.get("owner", "home", turn.turn_id)


@pytest.mark.parametrize("kind", ["http"])
@pytest.mark.parametrize("changes", [
    {"authority_kind": "served_request"}, {"authority_kind": "invented"},
    {"work_receipt_id": ""}, {"work_receipt_id": None}, {"work_receipt_id": True},
    {"version": 2}, {"version": 3.0}, {"extra": "unrecognized"},
    {"kind": "unrecognized"},
])
def test_strict_work_payload_version_and_lineage(kind, changes):
    candidate = work_candidate(kind)
    payload = records.document(candidate.canonical_json()) | changes
    with pytest.raises(ValueError):
        type(candidate).from_json(records.dump(payload))


@pytest.mark.parametrize("authority,receipt", [
    ("served_request", "work"), ("work_invocation", ""), (None, "work"),
    ("invented", "work"), ("work_invocation", None),
])
def test_invalid_work_root_does_not_insert(journal, authority, receipt):
    with pytest.raises(ValueError):
        journal.create("owner", "home", prompt="p", system="s",
                       authority_kind=authority, work_receipt_id=receipt)
    with journal._ledger.connection() as conn:
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='agent_turns'"
        ).fetchone()[0] == 0  # Refused before even creating the journal schema.


def test_work_tools_keep_exact_results_and_unknown_effect_reset_hold(journal):
    turn = chat.receive(journal, begin_work(journal, work_root(journal)))
    turn = chat.start(journal, turn).snapshot
    turn = chat.finish(journal, turn, result=chat.result("retained exact work result")).snapshot
    assert turn.state == "ready"
    turn = chat.receive(journal, begin_work(journal, turn))
    turn = chat.start(journal, turn).snapshot
    turn = chat.finish(journal, turn, failure="unknown").snapshot
    assert turn.state == "held_tool_unknown"
    assert "retained exact work result" in turn.rounds[0].tools[0].result_json
    with journal._ledger.connection() as conn:
        assert reset_blockers(conn, "owner", "home")
    assert journal.get("owner", "home", turn.turn_id) == turn



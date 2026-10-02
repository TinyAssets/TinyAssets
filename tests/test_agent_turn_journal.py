"""Synthetic durable progress: no provider or tool call, no execution authority."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from tinyassets.providers.agent_chat_codec import decode_openai_chat_agent
from tinyassets.storage import agent_turn_records as records
from tinyassets.storage.agent_turn_journal import (
    AgentTurnJournal,
    JournalUnavailable,
    ensure_schema,
)


def candidate(source="owned:future", model="opaque-llm"):
    return records.RoundInput(
        source,
        model,
        records.dump(
            {
                "version": 1,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "tool",
                            "description": "exact 🪐",
                            "parameters": {"type": "object"},
                        },
                    },
                ],
            }
        ),
        "binding",
        "reservation",
        1,
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
    )


def reply(*, count=1, text=None, finish=None, refusal=None):
    message = {"role": "assistant", "content": text}
    if refusal is not None:
        message["refusal"] = refusal
    if count:
        message["tool_calls"] = [
            {
                "id": f"call{i}",
                "type": "function",
                "function": {
                    "name": "tool",
                    "arguments": '{ "i": ' + str(i) + " }",
                },
            }
            for i in range(count)
        ]
    return decode_openai_chat_agent(
        {
            "choices": [
                {"message": message, "finish_reason": finish or ("tool_calls" if count else "stop")}
            ],
            "usage": {"prompt_tokens": 10},
        },
        source_ref="owned:future",
        requested_model="opaque-llm",
        tool_names=frozenset({"tool"}),
    )


@pytest.fixture
def journal(tmp_path):
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(tmp_path, founder_sub="owner", universe_id="home", platform_generated=True)
    return AgentTurnJournal(tmp_path)


def new(journal, *, owner="owner", universe="home"):
    return journal.create(owner, universe, prompt="exact\nuser 🪐", system="system\n")


@pytest.mark.parametrize("source", ["unknown", "current", "saved", "automatic"])
def test_policy_provenance_survives_reload(journal, source):
    turn = journal.create(
        "owner", "home", prompt="exact", system="system",
        policy_generation=0, policy_source=source,
    )
    loaded = journal.get("owner", "home", turn.turn_id)
    assert loaded == turn
    assert loaded.policy_source == source and loaded.policy_generation == 0


def test_legacy_header_remains_unknown_and_is_not_rewritten(journal):
    turn = new(journal)
    raw = records.dump({
        "version": 1, "prompt": "legacy", "system": "system", "policy_generation": 7,
    })
    with journal._ledger.connection() as conn:
        conn.execute("UPDATE agent_turns SET input_json = ?", (raw,))
    loaded = journal.get("owner", "home", turn.turn_id)
    assert loaded.policy_generation == 7 and loaded.policy_source == "unknown"
    with journal._ledger.connection() as conn:
        assert conn.execute("SELECT input_json FROM agent_turns").fetchone()[0] == raw


@pytest.mark.parametrize("source,generation", [
    ("inferred", 7), (None, 7), ([], 7), (True, 7), ("saved", None),
])
def test_invalid_policy_provenance_is_not_persisted(journal, source, generation):
    with journal._ledger.connection() as conn:
        ensure_schema(conn)
    with pytest.raises(ValueError):
        journal.create(
            "owner", "home", prompt="exact", system="system",
            policy_generation=generation, policy_source=source,
        )
    with journal._ledger.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_turns").fetchone()[0] == 0


@pytest.mark.parametrize("changes", [
    {"version": 3}, {"version": True}, {"version": 1}, {"policy_source": None},
    {"policy_source": "inferred"}, {"policy_generation": None}, {"extra": "ignored"},
])
def test_corrupt_policy_provenance_is_held(journal, changes):
    turn = new(journal)
    value = {
        "version": 2, "prompt": "exact", "system": "system",
        "policy_generation": 7, "policy_source": "current",
    } | changes
    with journal._ledger.connection() as conn:
        conn.execute("UPDATE agent_turns SET input_json = ?", (records.dump(value),))
    with pytest.raises(JournalUnavailable):
        journal.get("owner", "home", turn.turn_id)


def begin(journal, turn):
    return journal.begin_round(
        "owner", "home", turn.turn_id, expected_generation=turn.generation, candidate=candidate()
    ).snapshot


def receive(journal, turn, response=None):
    return journal.finish_inference(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
        ordinal=len(turn.rounds),
        reply=reply() if response is None else response,
    ).snapshot


def start(journal, turn, call=1):
    return journal.start_tool(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
        ordinal=len(turn.rounds),
        call_ordinal=call,
    )


def finish(journal, turn, call=1, **kwargs):
    return journal.finish_tool(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
        ordinal=len(turn.rounds),
        call_ordinal=call,
        request=turn.rounds[-1].tools[call - 1].request,
        **kwargs,
    )


def result(text="exact\nresult 🪐", error=False):
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={"exact": [1, None]},
        isError=error,
    )


def test_full_two_round_history_and_reused_wire_id(journal):
    initial = new(journal)
    assert initial.prompt == "exact\nuser 🪐" and initial.generation == 1
    assert initial.created_at.endswith("Z")
    turn = initial
    for _ in range(2):
        turn = receive(journal, begin(journal, turn))
        turn = start(journal, turn).snapshot
        turn = finish(journal, turn, result=result(error=True)).snapshot
        assert turn.state == "ready"
    turn = receive(journal, begin(journal, turn), reply(count=0, text="finished"))
    assert turn.state == "completed" and len(turn.rounds) == 3
    assert turn.rounds[0].tools[0].request.call_id == turn.rounds[1].tools[0].request.call_id
    assert turn.rounds[0].reply.input_tokens == 10 and turn.rounds[0].reply.output_tokens is None
    assert turn.rounds[0].cost_microusd is None
    assert journal.get("owner", "home", turn.turn_id) == turn
    assert journal.get("other", "home", turn.turn_id) is None
    assert journal.get("owner", "other", turn.turn_id) is None
    assert "exact" not in repr(turn) and "exact" not in repr(turn.rounds[0])


def test_whole_batch_order_and_only_one_start_winner(journal):
    turn = receive(journal, begin(journal, new(journal)), reply(count=2))
    assert start(journal, turn, 2).status == "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: start(journal, turn), range(2)))
    assert sorted(item.status for item in outcomes) == ["applied", "conflict"]
    started = journal.get("owner", "home", turn.turn_id)
    assert start(journal, started).status == "conflict"
    assert start(journal, started, 2).status == "conflict"
    completed = finish(journal, started, result=result()).snapshot
    assert completed.state == "tools_pending"
    assert start(journal, completed, 2).status == "applied"


def test_exact_finalize_repeats_ignore_stale_generation_but_different_result_conflicts(journal):
    turn = receive(journal, begin(journal, new(journal)))
    started = start(journal, turn).snapshot
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: finish(journal, started, result=result()), range(2)))
    assert sorted(item.status for item in outcomes) == ["already_applied", "applied"]
    latest = journal.get("owner", "home", turn.turn_id)
    assert latest.generation == started.generation + 1
    assert finish(journal, started, result=result("different")).status == "conflict"
    assert finish(journal, started, failure="unknown").status == "conflict"
    assert journal.get("owner", "home", turn.turn_id) == latest


def test_inference_finalize_is_idempotent_whole_batch(journal):
    turn = begin(journal, new(journal))
    args = dict(
        expected_generation=turn.generation, ordinal=1, reply=reply(count=2), cost_microusd=7
    )
    first = journal.finish_inference("owner", "home", turn.turn_id, **args)
    assert first.status == "applied"
    assert (
        journal.finish_inference("owner", "home", turn.turn_id, **args).status == "already_applied"
    )
    args["cost_microusd"] = 8
    assert journal.finish_inference("owner", "home", turn.turn_id, **args).status == "conflict"
    assert journal.get("owner", "home", turn.turn_id) == first.snapshot


@pytest.mark.parametrize("failure", ["unknown", "not_sent"])
def test_held_effect_never_replanned(journal, failure):
    started = start(journal, receive(journal, begin(journal, new(journal)))).snapshot
    held = finish(journal, started, failure=failure).snapshot
    assert held.state == "held_tool_" + failure
    assert start(journal, held).status == "conflict"
    assert (
        journal.begin_round(
            "owner",
            "home",
            held.turn_id,
            expected_generation=held.generation,
            candidate=candidate(),
        ).status
        == "conflict"
    )
    assert finish(journal, started, failure=failure).status == "already_applied"
    assert finish(journal, started, result=result()).status == "conflict"


def test_crash_after_intent_reopens_as_started_not_a_dispatch_right(journal, tmp_path):
    started = start(journal, receive(journal, begin(journal, new(journal)))).snapshot
    recovered = AgentTurnJournal(tmp_path).get("owner", "home", started.turn_id)
    assert recovered == started and recovered.rounds[0].tools[0].state == "started"
    assert start(journal, recovered).status == "conflict"


def test_nontext_result_is_known_and_preserved_not_unknown(journal):
    turn = start(journal, receive(journal, begin(journal, new(journal)), reply(count=2))).snapshot
    raw = CallToolResult(
        content=[ImageContent(type="image", data="YWJj", mimeType="image/png")],
        isError=False,
        _meta={"private_transport": "excluded"},
    )
    held = finish(journal, turn, result=raw).snapshot
    assert held.state == "held_unsupported_result"
    tool = held.rounds[0].tools[0]
    assert tool.state == "completed" and tool.content_kind == "non_text"
    assert "YWJj" in tool.result_json and "private_transport" not in tool.result_json
    assert start(journal, held, 2).status == "conflict"


@pytest.mark.parametrize(
    "stop,finish_reason,text,refusal",
    [
        ("held_refusal", "stop", None, "no"),
        ("held_filter", "content_filter", "part", None),
        ("held_truncated", "length", "part", None),
        ("held_unknown_stop", "future", "part", None),
        ("completed", "stop", "all", None),
    ],
)
def test_semantic_stops_not_promoted(journal, stop, finish_reason, text, refusal):
    turn = receive(
        journal,
        begin(journal, new(journal)),
        reply(count=0, finish=finish_reason, text=text, refusal=refusal),
    )
    assert turn.state == stop


def test_inference_transport_failure_never_automatically_retried(journal):
    turn = begin(journal, new(journal))
    args = dict(expected_generation=turn.generation, ordinal=1, reply=None)
    held = journal.finish_inference("owner", "home", turn.turn_id, **args).snapshot
    assert held.state == "held_transport"
    assert (
        journal.finish_inference("owner", "home", turn.turn_id, **args).status == "already_applied"
    )
    assert (
        journal.begin_round(
            "owner",
            "home",
            held.turn_id,
            expected_generation=held.generation,
            candidate=candidate(),
        ).status
        == "conflict"
    )


def test_schema_does_not_commit_callers_work(journal):
    with journal._ledger.connection() as conn:
        conn.execute("CREATE TABLE test_atomic (value TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO test_atomic VALUES ('pending')")
        with pytest.raises(JournalUnavailable):
            ensure_schema(conn)
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM test_atomic").fetchone()[0] == 0


def test_fault_on_second_insert_rolls_back_entire_reply_batch(journal):
    turn = begin(journal, new(journal))
    with journal._ledger.connection() as conn:
        conn.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON agent_turn_tools "
            "WHEN NEW.ordinal = 2 BEGIN SELECT RAISE(ABORT, 'test fault'); END"
        )
    with pytest.raises(Exception, match="test fault"):
        receive(journal, turn, reply(count=2))
    assert journal.get("owner", "home", turn.turn_id) == turn


def test_fault_before_start_commit_rolls_back_dispatch_intent(journal):
    turn = receive(journal, begin(journal, new(journal)))
    with journal._ledger.connection() as conn:
        conn.execute(
            "CREATE TRIGGER fail_advance BEFORE UPDATE ON agent_turns "
            "BEGIN SELECT RAISE(ABORT, 'test fault'); END"
        )
    with pytest.raises(Exception, match="test fault"):
        start(journal, turn)
    assert journal.get("owner", "home", turn.turn_id) == turn


@pytest.mark.parametrize(
    "column,value",
    [
        ("input_json", '{"version":1,"version":1}'),
        ("input_json", '{"x":NaN}'),
        ("input_json", '{"x":1e9999}'),
        ("state", "completed"),
        ("round_ordinal", 5),
        ("created_at", "invalid"),
        ("generation", "not-an-int"),
    ],
)
def test_corruption_held_not_defaulted(journal, column, value):
    turn = new(journal)
    with journal._ledger.connection() as conn:
        conn.execute(f"UPDATE agent_turns SET {column} = ?", (value,))
    with pytest.raises(JournalUnavailable, match="^agent turn record unavailable$"):
        journal.get("owner", "home", turn.turn_id)


def test_invalid_late_tool_never_partially_exposed(journal):
    turn = begin(journal, new(journal))
    valid = reply(count=2)
    bad = replace(
        valid,
        tool_requests=(
            valid.tool_requests[0],
            replace(valid.tool_requests[1], arguments_json="bad"),
        ),
    )
    with pytest.raises(ValueError):
        receive(journal, turn, bad)
    assert journal.get("owner", "home", turn.turn_id) == turn


def test_no_budget_foreign_key_and_root_cascade(journal):
    turn = receive(journal, begin(journal, new(journal)), reply(count=2))
    with journal._ledger.connection() as conn:
        assert all(
            "reservation" not in row[2]
            for table in ("agent_turn_rounds", "agent_turn_tools")
            for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        )
        conn.execute("DELETE FROM provider_invocation_reservations")
        conn.execute("DELETE FROM agent_turns WHERE turn_id = ?", (turn.turn_id,))
        assert conn.execute("SELECT COUNT(*) FROM agent_turn_rounds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_turn_tools").fetchone()[0] == 0


@pytest.mark.parametrize(
    "stage",
    ["ready", "inference_started", "tools_pending", "started", "unknown", "not_sent", "completed"],
)
def test_reset_preserves_terminal_evidence_and_blocks_only_matching_active_scope(journal, stage):
    from tinyassets.scoped_reset import MAIN_DB_TABLE_CLASSIFICATIONS
    from tinyassets.storage.agent_turn_journal import reset_blockers

    turn = new(journal)
    if stage != "ready":
        turn = begin(journal, turn)
    if stage == "completed":
        turn = receive(journal, turn, reply(count=0, text="done"))
    elif stage not in {"ready", "inference_started"}:
        turn = receive(journal, turn)
        if stage != "tools_pending":
            turn = start(journal, turn).snapshot
        if stage in {"unknown", "not_sent"}:
            turn = finish(journal, turn, failure=stage).snapshot
    with journal._ledger.connection() as conn:
        before = conn.total_changes
        assert bool(reset_blockers(conn, "owner", "home")) == (
            stage not in {"completed", "not_sent"}
        )
        assert reset_blockers(conn, "other", "home") == []
        assert reset_blockers(conn, "owner", "other") == []
        assert conn.total_changes == before
    assert journal.get("owner", "home", turn.turn_id) == turn
    for table in ("agent_turns", "agent_turn_rounds", "agent_turn_tools"):
        assert MAIN_DB_TABLE_CLASSIFICATIONS[table] == "preserve_or_block"


def test_reset_blocks_corrupt_matching_progress(journal):
    from tinyassets.storage.agent_turn_journal import reset_blockers

    new(journal)
    with journal._ledger.connection() as conn:
        conn.execute("UPDATE agent_turns SET input_json = 'corrupt-private-payload'")
        assert reset_blockers(conn, "owner", "home") == [
            "unreadable agent turn journal references exact home",
        ]
        assert reset_blockers(conn, "other", "home") == []


@pytest.mark.parametrize(
    "update",
    [
        {"source_ref": "different"},
        {"reported_model": 7},
        {"input_tokens": True},
        {"input_tokens": -1},
        {"output_tokens": 1.5},
        {"stop": "completed"},
        {"text": "different"},
        {"continuation_json": '{"role":"assistant","role":"tool"}'},
        {"dropped_fields": [17]},
    ],
)
def test_reply_snapshot_revalidates_typed_fields(journal, update):
    turn = begin(journal, new(journal))
    invalid = replace(reply(), **update)
    with pytest.raises(ValueError):
        receive(journal, turn, invalid)
    assert journal.get("owner", "home", turn.turn_id) == turn


@pytest.mark.parametrize(
    "column,value",
    [
        ("content_kind", "non_text"),
        ("is_error", 1),
        ("result_json", '{"private":Infinity}'),
        ("arguments_json", "{}"),
        ("name", "other"),
    ],
)
def test_tool_result_corruption_does_not_become_retry(journal, column, value):
    turn = start(journal, receive(journal, begin(journal, new(journal)))).snapshot
    turn = finish(journal, turn, result=result()).snapshot
    with journal._ledger.connection() as conn:
        conn.execute(f"UPDATE agent_turn_tools SET {column} = ?", (value,))
    with pytest.raises(JournalUnavailable, match="^agent turn record unavailable$"):
        journal.get("owner", "home", turn.turn_id)


def test_explicit_failed_inference_retry_retains_completed_effects(journal):
    known = start(journal, receive(journal, begin(journal, new(journal)))).snapshot
    known = finish(journal, known, result=result()).snapshot
    failed = begin(journal, known)
    failed = journal.finish_inference(
        "owner",
        "home",
        failed.turn_id,
        expected_generation=failed.generation,
        ordinal=2,
        reply=None,
    ).snapshot
    retried = journal.begin_round(
        "owner",
        "home",
        failed.turn_id,
        expected_generation=failed.generation,
        candidate=candidate(),
        after_failed_inference=True,
    ).snapshot
    assert retried.state == "inference_started"
    assert retried.rounds[:2] == failed.rounds
    assert retried.rounds[0].tools[0].result_json == known.rounds[0].tools[0].result_json
    final = receive(journal, retried, reply(count=0, text="done"))
    assert final.state == "completed" and len(final.rounds) == 3
    assert journal.get("owner", "home", final.turn_id) == final


def test_failed_inference_retry_has_one_winner(journal):
    turn = begin(journal, new(journal))
    held = journal.finish_inference(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
        ordinal=1,
        reply=None,
    ).snapshot

    def retry(_):
        return journal.begin_round(
            "owner",
            "home",
            held.turn_id,
            expected_generation=held.generation,
            candidate=candidate(),
            after_failed_inference=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(retry, range(2)))
    assert sorted(item.status for item in outcomes) == ["applied", "conflict"]


@pytest.mark.parametrize("stage", ["ready", "inference_started", "tools_pending", "unknown"])
def test_retry_flag_cannot_resume_other_states(journal, stage):
    turn = new(journal)
    if stage != "ready":
        turn = begin(journal, turn)
    if stage in {"tools_pending", "unknown"}:
        turn = receive(journal, turn)
    if stage == "unknown":
        turn = finish(journal, start(journal, turn).snapshot, failure="unknown").snapshot
    changed = journal.begin_round(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
        candidate=candidate(),
        after_failed_inference=True,
    )
    assert changed.status == "conflict" and changed.snapshot == turn


def test_abandon_unused_root_unblocks_reset(journal):
    from tinyassets.storage.agent_turn_journal import reset_blockers

    turn = new(journal)
    abandoned = journal.abandon(
        "owner",
        "home",
        turn.turn_id,
        expected_generation=turn.generation,
    )
    assert abandoned.status == "applied" and abandoned.snapshot.state == "abandoned"
    assert (
        journal.abandon(
            "owner",
            "home",
            turn.turn_id,
            expected_generation=turn.generation,
        ).status
        == "already_applied"
    )
    assert (
        journal.begin_round(
            "owner",
            "home",
            turn.turn_id,
            expected_generation=abandoned.snapshot.generation,
            candidate=candidate(),
        ).status
        == "conflict"
    )
    with journal._ledger.connection() as conn:
        assert reset_blockers(conn, "owner", "home") == []
    launched = begin(journal, new(journal))
    assert (
        journal.abandon(
            "owner",
            "home",
            launched.turn_id,
            expected_generation=launched.generation,
        ).status
        == "conflict"
    )


def test_abandon_settled_frontier_preserves_history_and_refuses_resume(journal):
    from tinyassets.storage.agent_turn_journal import reset_blockers

    turn = new(journal)
    for _ in range(2):
        turn = receive(journal, begin(journal, turn))
        turn = finish(journal, start(journal, turn).snapshot, result=result()).snapshot
    closed = journal.abandon("owner", "home", turn.turn_id, expected_generation=turn.generation)
    assert closed.status == "applied" and closed.snapshot.state == "abandoned"
    assert closed.snapshot.rounds == turn.rounds
    assert journal.get("owner", "home", turn.turn_id) == closed.snapshot
    assert journal.abandon(
        "owner", "home", turn.turn_id, expected_generation=turn.generation,
    ).status == "already_applied"
    assert journal.begin_round(
        "owner", "home", turn.turn_id, expected_generation=closed.snapshot.generation,
        candidate=candidate(),
    ).status == "conflict"
    with journal._ledger.connection() as conn:
        assert reset_blockers(conn, "owner", "home") == []


@pytest.mark.parametrize("stage", ["inference", "planned", "started", "unknown", "nontext"])
def test_abandon_cannot_hide_incomplete_or_ambiguous_progress(journal, stage):
    turn = begin(journal, new(journal))
    if stage != "inference":
        turn = receive(journal, turn)
    if stage not in {"inference", "planned"}:
        turn = start(journal, turn).snapshot
    if stage == "unknown":
        turn = finish(journal, turn, failure="unknown").snapshot
    elif stage == "nontext":
        turn = finish(journal, turn, result=CallToolResult(content=[
            ImageContent(type="image", data="AA==", mimeType="image/png"),
        ])).snapshot
    assert journal.abandon(
        "owner", "home", turn.turn_id, expected_generation=turn.generation,
    ).status == "conflict"
    with journal._ledger.connection() as conn:
        conn.execute(
            "UPDATE agent_turns SET state = 'abandoned' WHERE turn_id = ?", (turn.turn_id,),
        )
    with pytest.raises(JournalUnavailable):
        journal.get("owner", "home", turn.turn_id)


@pytest.mark.parametrize("change", ["removed", "rebound", "deleted"])
@pytest.mark.parametrize("operation", ["create", "begin", "receive", "start", "finish", "abandon"])
def test_every_mutation_rechecks_home_in_same_write_transaction(journal, change, operation):
    from tinyassets.account_deletion import principal_digest
    from tinyassets.storage.current_home import CurrentHomeChanged

    turn = new(journal)
    if operation in {"receive", "start", "finish"}:
        turn = begin(journal, turn)
    if operation in {"start", "finish"}:
        turn = receive(journal, turn)
    if operation == "finish":
        turn = start(journal, turn).snapshot
    with journal._ledger.connection() as conn:
        if change == "removed":
            conn.execute("DELETE FROM founder_home WHERE founder_sub = 'owner'")
        elif change == "rebound":
            conn.execute(
                "UPDATE founder_home SET universe_id = 'new-home' WHERE founder_sub = 'owner'"
            )
        else:
            conn.execute(
                "INSERT INTO deleted_principals (founder_sub, deleted_at) VALUES (?, 1)",
                (principal_digest("owner"),),
            )
    action = {
        "create": lambda: new(journal),
        "begin": lambda: begin(journal, turn),
        "receive": lambda: receive(journal, turn),
        "start": lambda: start(journal, turn),
        "finish": lambda: finish(journal, turn, result=result()),
        "abandon": lambda: journal.abandon(
            "owner",
            "home",
            turn.turn_id,
            expected_generation=turn.generation,
        ),
    }[operation]
    with pytest.raises(CurrentHomeChanged):
        action()
    # Read-only audit remains possible; failed mutation changed no progress.
    assert journal.get("owner", "home", turn.turn_id) == turn


def test_inserts_survive_an_added_column(journal, tmp_path):
    """Every insert names its columns, so a later additive column (the owner
    generation of change execution-owner-lease) cannot break this code, and a
    revert after that column lands still writes (Codex round 1, finding 13)."""
    import sqlite3

    from tinyassets.storage import db_path

    new(journal)  # creates the schema
    conn = sqlite3.connect(db_path(tmp_path))
    for table in ("agent_turns", "agent_turn_rounds", "agent_turn_tools"):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN future_extra INTEGER NOT NULL DEFAULT 1")
    conn.commit()
    conn.close()
    turn = receive(journal, begin(journal, new(journal)))
    turn = finish(journal, start(journal, turn).snapshot, result=result()).snapshot
    assert turn.state == "ready" and turn.rounds[0].tools[0].state == "completed"

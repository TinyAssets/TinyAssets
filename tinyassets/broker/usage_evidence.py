"""Bounded owner-only daily accounting evidence; advisory, never provider quota."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

PAGE_SIZE = 128


def validate(document):
    action = document["action"]
    if action == "daily_page":
        if set(document) != {"action", "source_ref", "start", "end", "cursor"}:
            raise ValueError("invalid daily evidence fields")
        if not isinstance(document["source_ref"], str) or not document["source_ref"]:
            raise ValueError("invalid daily evidence source")
        start, end = (datetime.fromisoformat(document[key]) for key in ("start", "end"))
        if (start.tzinfo is None or end.tzinfo is None or not start <= end
                or end - start > timedelta(days=2)):
            raise ValueError("invalid daily evidence window")
        cursor = document["cursor"]
        if cursor is not None and (not isinstance(cursor, list) or len(cursor) != 3
                                   or any(not isinstance(v, str) for v in cursor[:2])
                                   or type(cursor[2]) is not int or cursor[2] < 1):
            raise ValueError("invalid evidence cursor")
    elif action == "linked_turns":
        if (set(document) != {"action", "subjects"}
                or not isinstance(document["subjects"], list)
                or not 1 <= len(document["subjects"]) <= PAGE_SIZE
                or any(not isinstance(pair, list) or len(pair) != 2
                       or any(not isinstance(v, str) or not v or len(v) > 512 for v in pair)
                       for pair in document["subjects"])):
            raise ValueError("invalid evidence subjects")
    else:
        raise ValueError("invalid evidence operation")


def local_operation(store, principal, document):
    validate(document)
    with store._connection() as conn:
        if document["action"] == "linked_turns":
            return {"linked": [conn.execute(
                "SELECT 1 FROM agent_request_usage_links WHERE owner=? AND universe=? "
                "AND kind='turn' AND subject_id=? LIMIT 1", (principal, *pair),
            ).fetchone() is not None for pair in document["subjects"]]}
        cursor = document["cursor"] or ["", "", 0]
        rows = conn.execute(
            "SELECT universe, usage_id, ordinal, attempt_json FROM agent_request_attempts "
            "WHERE owner=? AND source_ref=? AND dispatched_at IS NOT NULL "
            "AND julianday(dispatched_at)>=julianday(?) "
            "AND julianday(dispatched_at)<=julianday(?) "
            "AND (universe,usage_id,ordinal)>(?,?,?) "
            "ORDER BY universe,usage_id,ordinal LIMIT ?",
            (principal, document["source_ref"], document["start"], document["end"],
             *cursor, PAGE_SIZE + 1)).fetchall()
        events = []
        for row in rows[:PAGE_SIZE]:
            attempt = json.loads(row["attempt_json"])
            events.append({"dispatched_at": attempt["dispatched_at"],
                           "usage_id": row["usage_id"], "ordinal": row["ordinal"],
                           "free": attempt["free"], "succeeded": attempt["state"] == "succeeded"})
        return {"events": events, "next_cursor": list(rows[PAGE_SIZE - 1][:3])
                if len(rows) > PAGE_SIZE else None}


def daily_events(base, owner, source_ref, start, end):
    from tinyassets.broker.usage import operation

    cursor, events = None, []
    while True:
        result = operation(base, (owner, "usage-evidence", ""), {
            "action": "daily_page", "source_ref": source_ref, "start": start.isoformat(),
            "end": end.isoformat(), "cursor": cursor})
        for event in result["events"]:
            instant = datetime.fromisoformat(event["dispatched_at"])
            if event["free"] is True and start <= instant <= end:
                events.append((instant, event["usage_id"], event["ordinal"], event["succeeded"]))
        next_cursor = result["next_cursor"]
        if next_cursor is None:
            return events
        if cursor is not None and tuple(next_cursor) <= tuple(cursor):
            raise ValueError("daily evidence cursor did not advance")
        cursor = next_cursor


def unlinked_rows(base, owner, rows):
    from tinyassets.broker.usage import operation

    for offset in range(0, len(rows), PAGE_SIZE):
        page = rows[offset:offset + PAGE_SIZE]
        result = operation(base, (owner, "usage-evidence", ""), {
            "action": "linked_turns", "subjects": [[row[-1], row[1]] for row in page]})
        flags = result["linked"]
        if len(flags) != len(page) or any(type(flag) is not bool for flag in flags):
            raise ValueError("invalid accounting link evidence")
        yield from (row[:-1] for row, linked in zip(page, flags) if not linked)


def daily_counts(base, owner, source_ref, start, end, zero_priced_models):
    """Join broker attempts with daemon legacy turns, excluding linked rounds.

    Pages are advisory observations, not a cross-process snapshot or quota.
    Any unavailable page/link lookup propagates to the caller's unknown result.
    """
    from contextlib import closing
    from pathlib import Path

    from tinyassets.request_budget import _read_only
    from tinyassets.storage import DB_FILENAME

    events = daily_events(base, owner, source_ref, start, end)
    path = Path(base) / DB_FILENAME
    if path.exists():
        with closing(_read_only(path)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            rows = conn.execute(
                "SELECT t.created_at,t.turn_id,r.ordinal,r.candidate_json,r.state,"
                "r.reply_json,t.universe_id FROM agent_turns t JOIN agent_turn_rounds r "
                "ON (t.owner_user_id=r.owner_user_id AND t.universe_id=r.universe_id "
                "AND t.turn_id=r.turn_id) WHERE t.owner_user_id=? "
                "AND julianday(t.created_at)>=julianday(?) "
                "AND julianday(t.created_at)<=julianday(?)",
                (owner, start.isoformat(), end.isoformat()),
            ).fetchall() if "agent_turns" in tables else []
        for created, turn, ordinal, raw, state, reply in unlinked_rows(base, owner, rows):
            instant = datetime.fromisoformat(created)
            candidate = json.loads(raw)
            model = candidate.get("model", "")
            if (start <= instant <= end and candidate.get("source_ref") == source_ref
                    and (model.endswith(":free") or model in zero_priced_models)):
                events.append((instant, turn, ordinal, reply is not None
                               and state not in {"failed", "inference_started"}))
    successful = 0
    for count, event in enumerate(sorted(events), 1):
        if event[3]:
            successful = count
    return len(events), successful

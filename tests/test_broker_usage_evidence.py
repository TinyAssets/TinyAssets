"""Daily history stays owner-scoped across broker and legacy daemon stores."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import socket
import sqlite3
from datetime import datetime, timezone

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.request_budget import requests_today
from tinyassets.storage.agent_request_usage import UsageStore

pytestmark = pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"),
                                reason="Unix broker peer identity")

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
SOURCE = "api_key_http:history"


def seed(discovery, count=1, *, owner="alice", universe="cc-alice", source=SOURCE):
    store = UsageStore(discovery.root, broker_ledger=discovery.ledger)
    with store._connection() as conn:
        for index in range(count):
            attempt = dict(dispatched_at=NOW.isoformat(), free=True,
                           state="succeeded" if index == 0 else "failed")
            conn.execute("INSERT INTO agent_request_attempts VALUES (?,?,?,?,?,?,?,?)",
                          (owner, universe, f"{index:032x}", 1, json.dumps(attempt),
                           NOW.isoformat(), source, attempt["state"]))
    return store


def counts(discovery, **extra):
    return requests_today(discovery.root, "alice", SOURCE, reset_timezone="UTC", now=NOW, **extra)


def test_private_daily_pages_cover_more_than_one_page_and_exclude_foreign_rows(discovery):
    seed(discovery, 130)
    seed(discovery, 4, owner="bob")
    seed(discovery, 2, universe="cc-other", source="different-source")
    assert counts(discovery) == (130, 1)
    assert not (discovery.root / ".tinyassets.db").exists()
    assert not (discovery.root / "outbound.db").exists()


def test_legacy_rounds_are_excluded_only_for_exact_owner_center_turn_links(discovery):
    store = seed(discovery)
    with store._connection() as conn:
        conn.execute("INSERT INTO agent_request_usage_links VALUES (?,?,?,?,?)",
                      ("alice", "cc-alice", "0" * 32, "turn", "shared-id"))
    with sqlite3.connect(discovery.root / ".tinyassets.db") as conn:
        conn.execute("CREATE TABLE agent_turns (owner_user_id,universe_id,turn_id,created_at)")
        conn.execute("CREATE TABLE agent_turn_rounds "
                      "(owner_user_id,universe_id,turn_id,ordinal,candidate_json,state,reply_json)")
        for owner, universe in (("alice", "cc-alice"), ("alice", "cc-other"), ("bob", "cc-alice")):
            conn.execute("INSERT INTO agent_turns VALUES (?,?,?,?)",
                          (owner, universe, "shared-id", NOW.isoformat()))
            conn.execute("INSERT INTO agent_turn_rounds VALUES (?,?,?,?,?,?,?)",
                          (owner, universe, "shared-id", 1,
                           json.dumps({"source_ref": SOURCE, "model": "test:free"}),
                           "completed", "reply"))
    assert counts(discovery) == (2, 2)


@pytest.mark.parametrize("failure", ["fence", "outage", "first_page", "second_page", "links",
                                    "legacy_unreadable"])
def test_unavailable_evidence_is_unknown_not_a_zero_allowance(discovery, monkeypatch, failure):
    seed(discovery, 130 if failure == "second_page" else 1)
    if failure == "fence":
        discovery.broker.state["token"] = "stale"
    elif failure == "outage":
        from tinyassets.broker import supervisor
        monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    elif failure == "legacy_unreadable":
        with sqlite3.connect(discovery.root / ".tinyassets.db") as conn:
            conn.execute("CREATE TABLE sentinel(value)")
        (discovery.root / ".tinyassets.db").chmod(0o000)
    else:
        from tinyassets.broker import usage_evidence
        original = usage_evidence.local_operation
        def unavailable(store, principal, document):
            if (failure == "first_page" or document["action"] == "linked_turns"
                    or document.get("cursor") is not None):
                raise OSError("synthetic evidence unavailable")
            return original(store, principal, document)
        monkeypatch.setattr(usage_evidence, "local_operation", unavailable)
        if failure == "links":
            with sqlite3.connect(discovery.root / ".tinyassets.db") as conn:
                conn.execute("CREATE TABLE agent_turns "
                              "(owner_user_id,universe_id,turn_id,created_at)")
                conn.execute("CREATE TABLE agent_turn_rounds "
                              "(owner_user_id,universe_id,turn_id,ordinal,candidate_json,"
                              "state,reply_json)")
                conn.execute("INSERT INTO agent_turns VALUES ('alice','cc-alice','turn',?)",
                              (NOW.isoformat(),))
                conn.execute("INSERT INTO agent_turn_rounds VALUES "
                              "('alice','cc-alice','turn',1,'{}','failed',NULL)")
    assert counts(discovery) is None


def test_invalid_windows_and_oversized_link_batches_refuse(discovery):
    with pytest.raises(ValueError):
        discovery.broker.client.usage("", {"action": "daily_page", "source_ref": SOURCE,
                                           "start": "2026-09-01T00:00:00+00:00",
                                           "end": NOW.isoformat(), "cursor": None})
    with pytest.raises(ValueError):
        discovery.broker.client.usage("", {"action": "linked_turns",
                                           "subjects": [["cc", "turn"]] * 129})

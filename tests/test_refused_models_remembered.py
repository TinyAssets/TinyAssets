"""A source's refusal of a model is remembered past the turn that met it.

Live 2026-09-28, the free-only account: a source refused one model (403) and had
withdrawn another (404), and every turn spent a request rediscovering both before
it reached a model that answered. #4078 moves ONE turn past a refused model;
here the refusal is kept (per owner, time-limited, with the source's reason) and
the next turn's REAL plan (``prepare_owned_model_plan`` through ``converse``)
orders that model last instead of asking it again.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tests import test_interactive_http_agent as integration
from tests import test_selected_model_authority as authority
from tests import test_served_model_preferences as prefs

rig = integration.rig
reader = integration.reader
served = prefs.served
configured = prefs.configured
agent = integration.agent

SECOND = "lab/second:free"


def _marks(agent):
    from tinyassets.storage.refused_models import active_refused_models

    return active_refused_models(agent.served.rig.base, owner_user_id="owner")


def _with_second_model(monkeypatch):
    """Discovery lists a second accepted model beside the rig's own."""
    from tinyassets.providers import discovery_snapshot

    original = discovery_snapshot.read_http_discovery_document

    def added(**kwargs):
        value = original(**kwargs)
        if "models/user" in kwargs["url"]:
            value["data"].append(authority.snapshot_tests._model(SECOND))
        return value

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", added)


def _saved_first_then_second(agent):
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences

    connection = f"api_key_http:{agent.served.rig.definition.id}"
    first, second = ModelRef(connection, authority.MODEL), ModelRef(connection, SECOND)
    prefs._save(agent, ModelPreferences("explicit", first, (second,)))
    return first, second


def test_the_next_turn_does_not_ask_a_recently_refused_model_first(agent, monkeypatch):
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.storage.refused_models import record_refused_model

    _with_second_model(monkeypatch)
    first, second = _saved_first_then_second(agent)
    record_refused_model(
        agent.served.rig.base, owner_user_id="owner", connection_id=first.connection_id,
        model_id=first.model_id, failure_class="provider_refused", detail="HTTP 403: no",
    )

    automatic = ModelPreferences("automatic", None, ()).document()
    assert prefs._converse(agent, monkeypatch, automatic) == "finished exact answer"

    asked = [wire[1]["body"]["model"] for wire in agent.wires]
    assert asked[0] == SECOND, asked
    assert first.model_id not in asked
    # The refused model did not answer, so its mark stands; nothing marked the other.
    assert [m.model_id for m in _marks(agent)] == [first.model_id]


def test_a_model_chosen_for_this_turn_is_still_asked(agent, monkeypatch):
    """The owner picking it now is the retry; only the standing order steps past."""
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.storage.refused_models import record_refused_model

    _with_second_model(monkeypatch)
    first, second = _saved_first_then_second(agent)
    record_refused_model(
        agent.served.rig.base, owner_user_id="owner", connection_id=first.connection_id,
        model_id=first.model_id, failure_class="provider_refused", detail="HTTP 403: no",
    )
    choice = ModelPreferences("explicit", first, (second,)).document()

    assert prefs._converse(agent, monkeypatch, choice) == "finished exact answer"

    assert agent.wires[0][1]["body"]["model"] == first.model_id
    # It answered, so the refusal is forgotten.
    assert _marks(agent) == ()


def test_an_expired_refusal_orders_as_before(agent, monkeypatch):
    from tinyassets.storage.refused_models import REFUSAL_TTL, record_refused_model

    _with_second_model(monkeypatch)
    first, _second = _saved_first_then_second(agent)
    record_refused_model(
        agent.served.rig.base, owner_user_id="owner", connection_id=first.connection_id,
        model_id=first.model_id, failure_class="provider_refused", detail="HTTP 403: no",
        now=datetime.now(timezone.utc) - REFUSAL_TTL - timedelta(minutes=1),
    )

    assert prefs._converse(agent, monkeypatch) == "finished exact answer"

    assert agent.wires[0][1]["body"]["model"] == first.model_id


def test_account_deletion_takes_the_owners_marks(tmp_path):
    from tinyassets.account_deletion import deletion_plan
    from tinyassets.storage import db_path
    from tinyassets.storage.refused_models import record_refused_model

    record_refused_model(
        tmp_path, owner_user_id="owner", connection_id="api_key_http:x",
        model_id="m", failure_class="provider_refused", detail="HTTP 403",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        plan = deletion_plan(conn, principal="owner", home="")
    assert plan.get("refused_model_marks") == [("owner_user_id", "principal")]


@pytest.mark.parametrize("bad", ["", "   ", "a b"])
def test_an_unusable_identity_records_nothing(tmp_path, bad):
    from tinyassets.storage.refused_models import active_refused_models, record_refused_model

    assert record_refused_model(
        tmp_path, owner_user_id=bad, connection_id="c", model_id="m",
        failure_class="provider_refused", detail="",
    ) is False
    assert active_refused_models(tmp_path, owner_user_id=bad) == ()


def test_a_workflow_keeps_the_owners_explicit_order_despite_a_mark(agent, monkeypatch):
    """An explicit workflow order is checked position by position; demoting a
    marked model there refused the whole run. The run keeps the owner's order."""
    from tinyassets.providers.served_model_plan import prepare_owned_model_plan
    from tinyassets.providers.work_candidate_data import WorkCandidateData
    from tinyassets.storage.refused_models import record_refused_model

    _with_second_model(monkeypatch)
    first, second = _saved_first_then_second(agent)
    record_refused_model(
        agent.served.rig.base, owner_user_id="owner", connection_id=first.connection_id,
        model_id=first.model_id, failure_class="provider_refused", detail="HTTP 403: no",
    )
    prepared = prepare_owned_model_plan(
        base=agent.served.rig.base, universe=agent.served.context.universe_dir,
        owner="owner", agent=agent.served.agent,
    )
    # Both served and workflow orders preserve the owner's explicit choice.
    assert prepared.plan.next_candidate("owner", "u-models") == first
    assert WorkCandidateData(prepared.plan).order == (first, second)

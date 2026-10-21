"""Atomic delivery persistence, not public intake or worker recovery proof."""

from __future__ import annotations

import pytest

from tests.test_receiver_links import _link, _save
from tests.test_receiver_links import env as management_env  # noqa: F401
from tinyassets.storage import deliveries


@pytest.fixture
def delivery_env(management_env):  # noqa: F811 - pytest resolves the imported fixture
    base, auth = management_env
    auth("receiver")
    receiver = _save()
    auth("sender")
    link = _link(receiver)
    # Persist the schema before fault injection, like daemon startup would.
    with deliveries.transaction(base):
        pass
    return base, auth, receiver, link


def _accept(conn, link, *, occurrence="send-1", values=None, source_run=None):
    values = {"topic": "exact input 🍉"} if values is None else values
    return deliveries.accept_in_transaction(
        conn,
        sender_id="sender",
        sender_universe_id="u-sender",
        link_id=link["link_id"],
        occurrence_id=occurrence,
        request_payload={"result": values},
        validated_inputs=values,
        source_run_id=source_run,
    )


def _read(conn, receipt, *, principal="sender", universe="u-sender"):
    return deliveries.read_receipt_in_transaction(
        conn,
        delivery_id=receipt["delivery_id"],
        principal_id=principal,
        universe_id=universe,
    )

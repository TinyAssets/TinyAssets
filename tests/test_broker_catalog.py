"""Scoped pages remain redacted and complete across the page boundary."""
import pytest

from tests.test_broker_capabilities import ledger  # noqa: F401
from tinyassets.broker.catalog import connections, local_page


def test_catalog_pages_are_scoped_redacted_and_complete(ledger):  # noqa: F811
    for number in range(130):
        ledger.grant_connection(grant_id=f"page-{number:03}", connection_id="conn-a",
                                owner_user_id="alice", universe_id="cc-alice")
    ledger.grant_connection(grant_id="foreign-center", connection_id="conn-a",
                            owner_user_id="alice", universe_id="cc-bob")
    rows = list(connections(ledger._db_path.parent, principal="alice", command_center="cc-alice"))
    assert len(rows) == 131
    assert len({grant.grant_id for grant, _, _ in rows}) == 131
    assert all(not hasattr(view, "credential_ref") for _, view, _ in rows)
    assert len(list(connections(ledger._db_path.parent, principal="alice",
                                command_center="cc-alice", limit=65))) == 65
    assert list(connections(ledger._db_path.parent, principal="bob",
                            command_center="cc-alice")) == []
    ledger.revoke_grant("grant-a")
    assert len(list(connections(ledger._db_path.parent, principal="alice",
                                command_center="cc-alice"))) == 130
    ledger.revoke_connection("conn-a")
    assert list(connections(ledger._db_path.parent, principal="alice",
                            command_center="cc-alice")) == []


@pytest.mark.parametrize("cursor,limit", [(None, 1), ("", 0), ("", 65), ("", True), ("\0", 1)])
def test_catalog_refuses_invalid_pages(ledger, cursor, limit):  # noqa: F811
    with pytest.raises(ValueError):
        local_page(ledger, principal="alice", command_center="cc-alice", cursor=cursor, limit=limit)


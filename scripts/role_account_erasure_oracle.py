"""Production-image D11 ledger-erasure phase; not D10 filesystem deletion."""
from __future__ import annotations


def seed(ledger):
    from tinyassets.storage.agent_request_usage import _SCHEMA

    for principal in ("erase-first", "erase-restart", "erase-peer"):
        ledger.create_connection(
            connection_id=principal, owner_user_id=principal, connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("GET",), provider="http",
            destination=principal, credential_ref="vault://http/" + principal,
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/catalogue",
                                "methods": ["GET"]}])
    with ledger._connect() as db:
        for statement in _SCHEMA:
            db.execute(statement)
        for principal in ("erase-first", "erase-restart", "erase-peer"):
            db.execute("INSERT INTO outbound_connection_grants "
                       "(grant_id,connection_id,owner_user_id,universe_id,granted_at) "
                       "VALUES (?,?,?,'same-center',1)", (principal, principal, principal))
            db.execute("INSERT INTO connection_capabilities VALUES (?,'model_use','{}',1)",
                       (principal,))
            db.execute("INSERT INTO agent_request_usage VALUES "
                       "(?,'same-center','usage','{}','{}','lease','parent',0,'now')", (principal,))
            db.execute("INSERT INTO agent_request_attempts VALUES "
                       "(?,'same-center','usage',1,'{}','now','source','unknown')", (principal,))
            db.execute("INSERT INTO agent_request_usage_links VALUES "
                       "(?,'same-center','usage','run','same-run')", (principal,))
            db.execute("INSERT INTO agent_request_dispatches VALUES "
                       "(?,?,'same-center','usage',1,1,?,?,'digest','operation',0,1)",
                       (principal, principal, principal, principal))


def probe(root):
    from tinyassets.account_deletion import delete_account
    from tinyassets.broker.account_erasure import erase_account
    from tinyassets.broker.ledger_queries import OWNER_CONNECTION_NAMES, query_ledger
    from tinyassets.broker.supervisor import broker_selected

    assert broker_selected(), "account erasure probe requires selected broker routing"
    assert not (root / "outbound.db").exists(), "daemon-local ledger unexpectedly exists"

    def names(principal):
        return query_ledger(root, query=OWNER_CONNECTION_NAMES, principal=principal,
                            command_center="same-center", grant_id="metadata")["items"]

    principal = "erase-first" if names("erase-first") else "erase-restart"
    before = names("erase-peer")
    assert before
    result = delete_account(root, founder_sub=principal,
                            delete_identity=lambda owner: "not_applicable")
    assert result["unfinished_phases"] == [], result["unfinished_phases"]
    counts = result["rows_deleted"]
    for table in ("outbound_connections", "outbound_connection_grants", "connection_capabilities",
                  "agent_request_usage", "agent_request_attempts", "agent_request_usage_links",
                  "agent_request_dispatches"):
        assert counts["outbound:" + table] == 1, counts
    assert not names(principal) and names("erase-peer") == before
    assert erase_account(root, principal=principal) == {}
    assert not (root / "outbound.db").exists()
    print("D53 actual account-deletion ledger phase through launcher broker: "
          "7 exact counts, all accounting tables, peer preservation, repeat and no local DB: PASS",
          flush=True)

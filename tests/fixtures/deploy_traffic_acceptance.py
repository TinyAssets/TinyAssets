"""Fixture-only auth, admission and effect sink for the cutover-send slice.

The effect sink is a transactional deterministic fake, never an external call.
No runtime executor is started, nor does this grant an owner generation.
"""

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

from tinyassets.storage.ingress_journal import (
    IngressJournal,
    Scope,
    import_in_transaction,
    initialize_imports,
)

SCOPE = Scope("fixture-user", "fixture-center", "principal:fixture-user")
SEND_ID = "cc2e9e60-89dd-43a8-9bea-f87c333b21e4"


def journal(root):
    @contextmanager
    def authority(scope):
        if scope != SCOPE:
            raise PermissionError("fixture principal or target mismatch")
        yield

    def policy(scope, payload):
        document = json.loads(payload)
        if set(document) != {"message", "client_send_id"}:
            raise ValueError("fixture accepts message and client_send_id only")

    return IngressJournal(root / "ingress", authority=authority, admission_policy=policy)


def replay(root):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.runs import runs_db_path
    from tinyassets.storage import conversation_run_admissions as cr

    base = root / "runtime"
    (base / SCOPE.command_center_id).mkdir(parents=True, exist_ok=True)
    set_founder_home(base, founder_sub=SCOPE.principal_id, universe_id=SCOPE.command_center_id,
                     platform_generated=True)
    grant_universe_access(base, universe_id=SCOPE.command_center_id, actor_id=SCOPE.principal_id,
                          permission="admin", granted_by=SCOPE.principal_id)
    cr.initialize(base)
    with sqlite3.connect(runs_db_path(base)) as conn:
        initialize_imports(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS fixture_effects "
                     "(admission_id TEXT PRIMARY KEY, payload BLOB NOT NULL)")

    store = journal(root)

    def importer(envelope):
        with cr.authorized_scope(base, owner=SCOPE.principal_id,
                                 universe=SCOPE.command_center_id) as scope:
            with cr.runs_transaction(scope) as conn:
                def reserve(writer, item):
                    message = json.loads(item.payload)["message"]
                    return cr.reserve_in_transaction(writer, scope,
                        request_key=item.client_send_id,
                        intent={"version": 1, "message": message, "input_method": "typed",
                                "model_choice": None, "binding_id": "b", "binding_revision": 1},
                        context={"version": 1, "history": []},
                        selection={"version": 1, "branch_def_id": "fixture", "reply_key": "reply"},
                        inputs={"message": message})["admission_id"]
                result = import_in_transaction(conn, scope, envelope, reserve)
        return result

    admission_id = store.replay(SCOPE, SEND_ID, importer)
    envelope = store.receipt(SCOPE, SEND_ID)
    with sqlite3.connect(runs_db_path(base)) as conn:
        conn.execute("INSERT OR IGNORE INTO fixture_effects VALUES (?,?)",
                     (admission_id, envelope.payload))
        effects = conn.execute("SELECT count(*) FROM fixture_effects").fetchone()[0]
        admissions = conn.execute("SELECT count(*) FROM conversation_run_admissions").fetchone()[0]
    store.append(SCOPE, SEND_ID, 1, b"CUTOVER_SEND_FINISHED", terminal=True)
    print(json.dumps({"admission_id": admission_id, "effects": effects, "admissions": admissions}))


if __name__ == "__main__":
    replay(Path(sys.argv[1]))

"""Real database request correlation, no provider stubs or launch authority."""

import json
import sqlite3

import pytest

from tinyassets import account_deletion
from tinyassets.daemon_server import grant_universe_access, set_founder_home
from tinyassets.runs import runs_db_path
from tinyassets.storage import db_path

OWNER, HOME = "user-consumer", "u-consumer"


@pytest.fixture
def store(tmp_path):
    from tinyassets.storage import conversation_run_admissions as cr

    (tmp_path / HOME).mkdir()
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id=HOME, platform_generated=True)
    grant_universe_access(tmp_path, universe_id=HOME, actor_id=OWNER,
                          permission="admin", granted_by=OWNER)
    cr.initialize(tmp_path)
    return tmp_path


def reserve(base, key, *, message="hello", context=None, model_choice=None,
            owner=OWNER, home=HOME):
    from tinyassets.storage import conversation_run_admissions as cr

    with cr.authorized_scope(base, owner=owner, universe=home) as scope:
        with cr.runs_transaction(scope) as conn:
            return cr.reserve_in_transaction(
                conn, scope, request_key=key,
                intent={"version": 1, "message": message, "input_method": "typed",
                        "model_choice": model_choice, "binding_id": "b", "binding_revision": 1},
                context=context or {"version": 1, "history": [], "preferences": None},
                selection={"version": 1, "branch_def_id": "test-branch",
                           "branch_version_id": None, "reply_key": "reply"},
                inputs={"message": message},
            )


def complete(base, run_id, reply="answer"):
    with sqlite3.connect(runs_db_path(base)) as conn:
        conn.execute("UPDATE runs SET status='completed', output_json=? WHERE run_id=?",
                     (json.dumps({"reply": reply}), run_id))


def project(base, admission_id):
    from tinyassets.storage import conversation_run_admissions as cr

    with cr.authorized_scope(base, owner=OWNER, universe=HOME) as scope:
        return cr.project_terminal(scope, admission_id)


















def _projection_process(base, admission_id, entered, release, result):
    from tinyassets.storage import conversation_run_admissions as cr

    original = cr._write_pair
    def paused(*args, **kwargs):
        entered.set()
        if not release.wait(8):
            raise RuntimeError("test release missing")
        return original(*args, **kwargs)
    cr._write_pair = paused
    try:
        project(base, admission_id)
        result.put("projected")
    except Exception as exc:
        result.put(type(exc).__name__ + ": " + str(exc))


def _deletion_process(base, result):
    # Spawn does not inherit pytest's runtime doubles. Install the same venue
    # in this child; the real account-deletion/projection barrier remains intact.
    from tests.support.box_double import install
    from tests.support.broker_double import _Registry
    from tinyassets.broker import supervisor

    registry = _Registry()
    patch = pytest.MonkeyPatch()
    # The owner box deletion destroys is admitted on this data root.
    patch.setenv("TINYASSETS_DATA_DIR", str(base))
    double = install(patch)
    patch.setattr(supervisor, "get_supervisor", registry.supervisor_for)
    try:
        receipt = account_deletion.delete_account(base, founder_sub=OWNER)
        if receipt["unfinished_phases"]:
            raise RuntimeError(str(receipt["unfinished_phases"]))
        result.put("deleted")
    except Exception as exc:
        result.put(type(exc).__name__ + ": " + str(exc))

    finally:
        registry.close()
        double.close()
        patch.undo()




















def source_version(base, *, nested=False, public=True):
    from tinyassets.branch_versions import publish_branch_version

    with sqlite3.connect(db_path(base)) as conn:
        conn.execute("INSERT INTO branch_definitions "
                     "(branch_def_id,name,author,visibility,created_at,updated_at) "
                     "VALUES ('shared','Shared','creator','public',0,0)")
    node = {"node_id": "reply", "display_name": "Reply", "prompt_template": "hello"}
    if nested:
        node["invoke_branch_spec"] = {"branch_def_id": "mutable-child"}
    return publish_branch_version(base, {"branch_def_id": "shared", "author": "creator",
                                         "visibility": "public", "node_defs": [node]},
                                  public=public)




















def reset_plan(base):
    from tinyassets.daemon_server import ensure_universe_registered
    from tinyassets.scoped_reset import TestIdentityRoster, plan_test_identity_reset

    ensure_universe_registered(base, universe_id=HOME, universe_path=base / HOME)
    roster = TestIdentityRoster("consumer-reset-v1", {"receiver": OWNER}, frozenset({OWNER}))
    plan = plan_test_identity_reset(base, alias="receiver", roster=roster)
    return roster, plan


















def _reset_lock_process(base, results):
    from tinyassets.scoped_reset import ScopedResetLeaseBusy, acquire_maintenance_barrier

    try:
        with acquire_maintenance_barrier(base, exclusive=True):
            results.put("unexpected exclusive lock")
    except ScopedResetLeaseBusy:
        results.put("reset blocked")





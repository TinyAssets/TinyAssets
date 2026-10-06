"""Real FK erasure of either delivery party, retaining independent peer runs."""
# ruff: noqa: F811 -- pytest resolves imported fixtures by their public names
import json
import sqlite3

import pytest

from tests.test_delivery_reservations import (
    _accept,
    _read,
    delivery_env,  # noqa: F401
)
from tests.test_receiver_links import _link, _save
from tests.test_receiver_links import env as management_env  # noqa: F401
from tinyassets import account_deletion, runs
from tinyassets.daemon_server import set_founder_home
from tinyassets.storage import deliveries


def seed(delivery_env):
    base, auth, receiver, link = delivery_env
    with deliveries.transaction(base) as conn:
        first = _accept(conn, link)
        first_run = _read(conn, first, principal="receiver", universe="u-receiver")["run_id"]
    auth("outsider")
    peer_receiver = _save(universe_id="u-outsider", branch_def_id="b-outsider",
                          allowed_senders=["outsider", "sender", "receiver"])
    peer_link = _link(peer_receiver, universe_id="u-outsider", branch_def_id="b-outsider")
    with deliveries.transaction(base) as conn:
        peer = deliveries.accept_in_transaction(
            conn, sender_id="outsider", sender_universe_id="u-outsider",
            link_id=peer_link["link_id"], occurrence_id="peer-send",
            request_payload={"result": "peer"}, validated_inputs={"topic": "peer"})
        peer_run = deliveries.read_receipt_in_transaction(
            conn, delivery_id=peer["delivery_id"], principal_id="outsider",
            universe_id="u-outsider")["run_id"]
        before = tuple(conn.execute("SELECT * FROM runs WHERE run_id=?", (peer_run,)).fetchone())
    return base, first_run, peer_run, before


@pytest.mark.parametrize("principal", ["sender", "receiver"])
def test_each_party_erasure_counts_children_and_preserves_peer(delivery_env, principal):
    base, first_run, peer_run, before = seed(delivery_env)
    home = "u-" + principal
    (base / home).mkdir(exist_ok=True)
    set_founder_home(base, founder_sub=principal, universe_id=home, platform_generated=True)
    receipt = account_deletion.delete_account(
        base, founder_sub=principal, cancel_billing=lambda _: "cancelled",
        delete_identity=lambda _: "deleted")
    assert receipt["unfinished_phases"] == []
    counts = receipt["rows_deleted"]
    with deliveries.transaction(base) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT count(*) FROM graph_deliveries").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM graph_delivery_attempts").fetchone()[0] == 1
        peer_row = conn.execute("SELECT * FROM runs WHERE run_id=?", (peer_run,)).fetchone()
        assert tuple(peer_row) == before
        retained = conn.execute("SELECT 1 FROM runs WHERE run_id=?", (first_run,)).fetchone()
        assert bool(retained) == (principal == "sender")
        for table in ("graph_deliveries", "graph_delivery_attempts",
                      "graph_output_links", "graph_receivers"):
            for row in conn.execute(f"SELECT * FROM {table}"):
                # Exact identity references, not user-authored content substring erasure.
                assert principal not in tuple(row)
        for row in conn.execute("SELECT senders_json FROM graph_receivers"):
            assert principal not in json.loads(row[0])
    assert counts["runs:graph_deliveries"] == 1
    assert counts["runs:graph_delivery_attempts"] == 1
    assert counts["runs:graph_output_links"] == 1


def test_satellite_failure_rolls_back_delivery_children_and_receipt(delivery_env):
    base, _, _, _ = seed(delivery_env)
    path = runs.runs_db_path(base)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER refuse_link_erasure BEFORE DELETE ON graph_output_links "
                     "BEGIN SELECT RAISE(ABORT, 'test refusal'); END")
    counts = {}
    with pytest.raises(sqlite3.IntegrityError, match="test refusal"):
        account_deletion._delete_satellite_rows(
            path, principal="sender", home="u-sender", counts=counts, label="runs")
    assert counts == {}
    with deliveries.transaction(base) as conn:
        assert conn.execute("SELECT count(*) FROM graph_delivery_attempts").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM graph_deliveries").fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    (base / "u-sender").mkdir(exist_ok=True)
    set_founder_home(base, founder_sub="sender", universe_id="u-sender", platform_generated=True)
    receipt = account_deletion.delete_account(
        base, founder_sub="sender", cancel_billing=lambda _: "cancelled",
        delete_identity=lambda _: "deleted")
    assert receipt["unfinished_phases"] == ["store:runs"]
    assert receipt["billing"] == "cancelled"
    assert receipt["identity"] == "deleted"
    assert list((base / ".account-deletions").glob("*.json"))


@pytest.mark.parametrize("failed_maintenance", ["delivery", "admission", "files", "updates"])
def test_delivery_tick_exception_does_not_starve_budget_reconciliation(
    failed_maintenance,
):
    import ast
    import builtins
    import time
    from pathlib import Path
    from types import SimpleNamespace

    tree = ast.parse(Path("tinyassets/universe_server.py").read_text(encoding="utf-8"))
    function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                    and n.name == "_served_budget_lease_loop")
    seen, cursors, file_cursors, deliveries, sleeps = [], [], [], [], []
    updates = []
    process_sleep = time.sleep

    class StopLoop(BaseException):
        pass

    def sleep(seconds):
        if len(sleeps) == 3:
            raise StopLoop
        sleeps.append(seconds)

    def delivery(_):
        deliveries.append("delivery")
        if failed_maintenance == "delivery":
            raise RuntimeError("delivery store unavailable")

    def admitted(_, *, after_run_id=""):
        cursors.append(after_run_id)
        if failed_maintenance == "admission" and len(cursors) == 2:
            raise RuntimeError("admission store unavailable")
        return f"cursor-{len(cursors)}"

    def files(_, *, after_operation_id=""):
        file_cursors.append(after_operation_id)
        if failed_maintenance == "files" and len(file_cursors) == 2:
            raise RuntimeError("file store unavailable")
        return f"file-cursor-{len(file_cursors)}"

    def update_centers(_):
        updates.append("updates")
        if failed_maintenance == "updates":
            raise RuntimeError("update store unavailable")

    def loop_import(name, globals=None, locals=None, fromlist=(), level=0):
        # Only the extracted loop gets this clock: live background workers in
        # the same pytest process must retain the real time module and sleep.
        if name == "time":
            return SimpleNamespace(sleep=sleep)
        if name == "tinyassets.command_center_update_maintenance":
            return SimpleNamespace(tick=update_centers)
        return builtins.__import__(name, globals, locals, fromlist, level)

    scope = {"__builtins__": {**vars(builtins), "__import__": loop_import},
             "reconcile_deliveries": delivery,
             "reconcile_admitted_runs": admitted,
             "reconcile_run_files": files,
             "reconcile_served_budget_leases": lambda _: seen.append("budget") or 0,
             "_sb_data_dir": lambda: "/unused",
             "logger": SimpleNamespace(exception=lambda *_: None, info=lambda *_: None)}
    # Preserve the actual production closure's cursor scope, not a rewritten loop.
    factory = ast.parse(
        "def make_loop():\n"
        "    _admitted_run_cursor = ''\n"
        "    _file_retention_cursor = ''\n"
        "    return _served_budget_lease_loop\n"
    )
    factory.body[0].body.insert(2, function)
    exec(compile(ast.fix_missing_locations(factory), "loop", "exec"), scope)
    with pytest.raises(StopLoop):
        scope["make_loop"]()()
    assert sleeps == [300.0] * 3
    assert time.sleep is process_sleep
    assert updates == ["updates"] * 3
    assert seen == ["budget"] * 3
    assert deliveries == ["delivery"] * 3
    assert cursors == ["", "cursor-1",
                       "cursor-1" if failed_maintenance == "admission" else "cursor-2"]
    assert file_cursors == ["", "file-cursor-1",
                            "file-cursor-1" if failed_maintenance == "files" else "file-cursor-2"]

"""Real attempt locks and conservative recovery; no public intake proof."""

import os

import pytest

from tests.test_delivery_reservations import (
    _accept,
    _read,
    delivery_env,  # noqa: F401
)
from tests.test_receiver_links import env as management_env  # noqa: F401
from tinyassets import runs
from tinyassets.storage import deliveries, delivery_lock


@pytest.fixture
def reserved(delivery_env):  # noqa: F811
    base, _, _, link = delivery_env
    with deliveries.transaction(base) as conn:
        receipt = _accept(conn, link)
        private = _read(conn, receipt, principal="receiver", universe="u-receiver")
    return base, receipt, private["run_id"]


def _lock(base, receipt):
    return delivery_lock.try_attempt_lock(base, delivery_id=receipt["delivery_id"], attempt=1)




















def _process_hold_and_crash(base, receipt, mark_started, ready, stop):
    with _lock(base, receipt) as guard:
        assert guard is not None
        if mark_started:
            with deliveries.transaction(base) as conn:
                assert deliveries.start_attempt_in_transaction(conn, guard)
                if mark_started == "terminal":
                    run_id = conn.execute("SELECT run_id FROM graph_delivery_attempts").fetchone()[
                        0
                    ]
            if mark_started == "terminal":
                runs.update_run_status(base, run_id, status="completed")
        ready.send(True)
        if not stop.wait(20):
            raise RuntimeError("test did not release child")
        os._exit(74)  # No finally cleanup: only the kernel can release this lock.



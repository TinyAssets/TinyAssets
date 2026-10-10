"""Actual hosted main nominates admitted work without network or provider effects."""

import threading
import time
import uuid

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_consumer_run_envelope import reserve, setup
from tests.test_consumer_selection import store as store


@pytest.mark.usefixtures("cloud_runtime")
def test_actual_main_nominates_saved_consumer_on_boot_and_periodic_tick(
    store, monkeypatch, in_process_broker,
):
    from tinyassets import (
        delivery_runtime,
        engine_mcp_http,
        provider_assignment,
        run_input_origins,
        universe_server,
    )
    from tinyassets.api import runs as api_runs
    from tinyassets.onboarding import session_store

    row = reserve(store, str(uuid.uuid4()), setup(store))
    nominated, loops = [], []

    class CapturedThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            loops.append(self.target)

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(store))
    in_process_broker.supervisor_for(store)
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    monkeypatch.setattr(session_store, "arm", lambda: None)
    monkeypatch.setattr(threading, "Thread", CapturedThread)
    monkeypatch.setattr(provider_assignment, "reconcile_orphaned_reservations_on_boot", lambda _: 0)
    monkeypatch.setattr(provider_assignment, "reconcile_served_budget_leases", lambda _: 0)
    monkeypatch.setattr(api_runs, "_ensure_runs_recovery", lambda: None)
    monkeypatch.setattr(delivery_runtime, "reconcile_deliveries", lambda _: None)
    monkeypatch.setattr(engine_mcp_http, "start_engine_mcp_http_servers", lambda: [])
    monkeypatch.setattr(universe_server, "create_streamable_http_app", object)
    monkeypatch.setattr(universe_server.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(run_input_origins, "dispatch_initial_run",
                        lambda base, *, run_id: nominated.append((base, run_id)))

    universe_server.main(host="127.0.0.1", port=0)
    assert nominated == [(store, row["run_id"])]
    loop = next(target for target in loops if target.__name__ == "_served_budget_lease_loop")
    sleeps = []

    class StopLoop(BaseException):
        pass

    def one_tick(seconds):
        if sleeps:
            raise StopLoop
        sleeps.append(seconds)

    monkeypatch.setattr(time, "sleep", one_tick)
    with pytest.raises(StopLoop):
        loop()
    assert sleeps == [300.0]
    assert nominated == [(store, row["run_id"]), (store, row["run_id"])]

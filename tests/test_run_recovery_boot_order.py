"""The server takes the run-recovery sweep before it starts anything that runs.

The engine MCP children serve run tools too, and the process that sweeps must
be one whose own runs are not in the table yet. So hosted ``main`` runs the
recovery before it spawns the engine children or starts the automation
consumer, and holds the lock for its lifetime.
"""

from __future__ import annotations

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401


@pytest.mark.usefixtures("cloud_runtime")
def test_main_sweeps_before_spawning_engines_or_the_consumer(
    tmp_path, monkeypatch, in_process_broker,
):
    import threading

    from tinyassets import delivery_runtime, engine_mcp_http, provider_assignment
    from tinyassets import universe_server as us
    from tinyassets.api import runs as api_runs
    from tinyassets.onboarding import session_store
    from tinyassets.runtime import assigned_queue_consumer as aqc

    order: list[str] = []

    class _Thread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            pass

    class _Consumer:
        def __init__(self, base):
            pass

        def start(self):
            order.append("consumer")

        def stop(self):
            pass

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(session_store, "arm", lambda: None)
    in_process_broker.supervisor_for(tmp_path)
    monkeypatch.setattr(threading, "Thread", _Thread)

    def _maintenance_fails(_base):
        # Boot maintenance also sweeps, but inside a try this failure skips.
        raise RuntimeError("budget maintenance unavailable")

    monkeypatch.setattr(provider_assignment, "reconcile_orphaned_reservations_on_boot",
                        _maintenance_fails)
    monkeypatch.setattr(provider_assignment, "reconcile_served_budget_leases", lambda _: 0)
    monkeypatch.setattr(delivery_runtime, "reconcile_deliveries", lambda _: None)
    monkeypatch.setattr(api_runs, "_ensure_runs_recovery",
                        lambda: order.append("recovery"))
    monkeypatch.setattr(engine_mcp_http, "start_engine_mcp_http_servers",
                        lambda: order.append("engines") or [])
    monkeypatch.setattr(aqc, "assigned_queue_consumer_enabled", lambda: True)
    monkeypatch.setattr(aqc, "AssignedQueueConsumer", _Consumer)
    monkeypatch.setattr(us, "create_streamable_http_app", object)
    monkeypatch.setattr(us.uvicorn, "run", lambda *a, **k: order.append("serve"))

    us.main(host="127.0.0.1", port=0)

    assert "recovery" in order, order
    first = order.index("recovery")
    assert first < order.index("engines") and first < order.index("consumer"), order

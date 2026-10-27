"""W10 cost-driver receipts (row N12): recorded at the lifecycle, rolled up daily, read by root."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from scripts import check_shapes, metering_receipts
from tests.support.admission import drive
from tests.test_box_public_egress import exchange, proxy  # noqa: F401
from tests.test_owner_box_lifecycle import fleet  # noqa: F401
from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.host_service import recover_daemons
from tinyassets.metering import Meter, day_of

DAY = dt.datetime(2026, 10, 20, tzinfo=dt.UTC).timestamp()
USAGE = {"peak_memory_bytes": 300 * 2**20, "cpu_usec": 4_000_000}


def _intervals(meter):
    with sqlite3.connect(meter.path) as db:
        return db.execute("SELECT owner, uid, size_class, ended_at IS NULL, end_reason, "
                          "peak_memory_bytes, cpu_usec FROM meter_intervals ORDER BY id").fetchall()


@pytest.fixture
def metered(fleet, tmp_path):  # noqa: F811
    fleet.meter = fleet.manager.meter = Meter(tmp_path / "metering.db", source="boxhostd")
    fleet.runtime.usage = lambda uid: USAGE
    fleet.manager.volumes.store = tmp_path / "images"
    fleet.manager.volumes.store.mkdir()
    return fleet


@pytest.mark.verifies("cost-driver-receipts:Receipts measure and never limit")
def test_interval_opens_at_box_start_and_closes_at_stop_with_cgroup_facts(metered):
    uid = metered.wake("a")
    assert _intervals(metered.meter) == [("a", uid, "thin", 1, None, None, None)]
    drive(metered.routing.sleep("a"))
    assert metered.runtime.stops == [uid]
    assert _intervals(metered.meter) == [
        ("a", uid, "thin", 0, "stopped", USAGE["peak_memory_bytes"], USAGE["cpu_usec"])]


@pytest.mark.verifies("cost-driver-receipts:Receipts measure and never limit")
def test_crashed_box_closes_its_interval(metered):
    uid = metered.wake("a")
    metered.runtime.live.discard(uid)
    metered.manager.maintain()
    assert _intervals(metered.meter)[-1][3:5] == (0, "crashed")
    assert metered.meter.failures == 0


@pytest.mark.verifies("cost-driver-receipts:Receipts measure and never limit")
def test_host_restart_closes_open_intervals_at_best_known_end(tmp_path):
    before = Meter(tmp_path / "metering.db", source="boxhostd")
    before.open_interval("a", 300001, DAY)
    before.open_interval("b", 300002, DAY)
    before.seen(300002, DAY + 900)
    stopped = []
    (tmp_path / "bundles").mkdir()
    runtime = SimpleNamespace(
        box_id=GvisorRuntime.box_id, bundle_root=tmp_path / "bundles", stop=stopped.append,
        usage=lambda uid: USAGE, _command=lambda *args: SimpleNamespace(stdout=json.dumps(
            [{"id": "owner-300001", "status": "running"}])))
    start = time.time()
    recover_daemons(runtime, Meter(tmp_path / "metering.db", source="boxhostd"))
    assert stopped == [300001]
    with sqlite3.connect(before.path) as db:
        rows = {uid: row for uid, *row in db.execute(
            "SELECT uid, ended_at, end_reason, peak_memory_bytes FROM meter_intervals")}
    assert rows[300001][0] >= start
    assert rows[300001][1:] == ["recovered", USAGE["peak_memory_bytes"]]
    assert rows[300002] == [DAY + 900, "lost", None]


@pytest.mark.verifies("cost-driver-receipts:Receipts measure and never limit")
def test_metering_write_failure_never_blocks_a_box_and_is_counted(fleet, tmp_path, caplog):  # noqa: F811
    fleet.manager.meter = meter = Meter(tmp_path / "absent" / "metering.db", source="boxhostd")
    fleet.runtime.usage = lambda uid: USAGE
    uid = fleet.wake("a")
    drive(fleet.routing.sleep("a"))
    assert fleet.runtime.stops == [uid]
    assert meter.failures >= 2
    assert "receipts are incomplete" in caplog.text
    counted = Meter(tmp_path / "metering.db", source="broker")
    counted.add("a", "storage", 1, time.time())  # a derived driver is not a counter
    today = day_of(time.time())
    assert counted.receipts(today, today)["write_failures"] == {today: 1}


@pytest.mark.verifies("cost-driver-receipts:Receipts measure and never limit")
@pytest.mark.asyncio
async def test_relay_bytes_are_counted_per_owner(proxy, monkeypatch):  # noqa: F811
    async def echo(reader, writer):
        writer.write(await reader.read())
        await writer.drain()
        writer.close()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    monkeypatch.setattr("tinyassets.broker.public_egress.destination", lambda *a: ["127.0.0.1"])
    port = upstream.sockets[0].getsockname()[1]
    payload = b"x" * 5000
    try:
        reply = await exchange(proxy, f"CONNECT example.test:{port} HTTP/1.1\r\n\r\n".encode()
                               + payload)
    finally:
        upstream.close()
    assert reply.endswith(payload)
    meter = Meter(proxy._owner_identities.path.parent / "metering.db", source="broker")
    today = day_of(time.time())
    async with asyncio.timeout(2):
        while not meter.path.is_file() or not meter.receipts(today, today)["events"]:
            await asyncio.sleep(.01)
    [event] = meter.receipts(today, today)["events"]
    assert (event["subject"], event["type"], event["data"]["unit"]) == (
        "alice", "io.tinyassets.meter.relay", "GB")
    assert event["data"]["quantity"] == pytest.approx(2 * len(payload) / 1e9)


@pytest.mark.verifies("cost-driver-receipts:One receipt per owner per day")
def test_daily_rollup_is_cloudevents_clipped_to_each_day(tmp_path):
    meter = Meter(tmp_path / "metering.db", source="boxhostd")
    meter.open_interval("a", 300001, DAY + 86400 - 600)
    meter.close_interval(300001, "stopped", DAY + 86400 + 300,
                         {"peak_memory_bytes": 512 * 2**20, "cpu_usec": 9_000_000})
    meter.daily(DAY + 86400 + 400, lambda: {300001: (None, 2 * 10**9, 20 * 10**9)})
    meter.daily(DAY + 2 * 86400 + 1, lambda: {})
    events = meter.receipts("2026-10-20", "2026-10-21")["events"]
    assert [(e["time"], e["type"], e["subject"], e["data"]["quantity"]) for e in events] == [
        ("2026-10-20T00:00:00Z", "io.tinyassets.meter.box_seconds", "a", 600),
        ("2026-10-21T00:00:00Z", "io.tinyassets.meter.box_seconds", "a", 300),
        ("2026-10-21T00:00:00Z", "io.tinyassets.meter.storage", "a", 2)]
    first = events[0]
    assert {key: first[key] for key in ("specversion", "source", "datacontenttype")} == {
        "specversion": "1.0", "source": "tinyassets/boxhostd",
        "datacontenttype": "application/json"}
    assert first["data"] == {"day": "2026-10-20", "owner": "a", "center": None,
                             "driver": "box_seconds", "unit": "box-seconds", "quantity": 600,
                             "size_class": "thin", "intervals": 1,
                             "peak_memory_bytes": 512 * 2**20, "cpu_seconds": 6.0}
    assert events[2]["data"]["provisioned_bytes"] == 20 * 10**9
    assert len({e["id"] for e in events}) == 3
    assert [e["id"] for e in meter.receipts("2026-10-20", "2026-10-21")["events"]] == [
        e["id"] for e in events]


@pytest.mark.verifies("cost-driver-receipts:One receipt per owner per day")
def test_founder_read_prices_drivers_and_refuses_everyone_else(tmp_path, monkeypatch, capsys):
    boxhostd = Meter(tmp_path / "metering.db", source="boxhostd")
    boxhostd.open_interval("a", 300001, DAY)
    boxhostd.close_interval(300001, "stopped", DAY + 600, USAGE)
    boxhostd.daily(DAY + 86400, lambda: {})
    broker = tmp_path / "broker"
    broker.mkdir()
    Meter(broker / "metering.db", source="broker").add("a", "relay", 2 * 10**9, DAY + 60)
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"box_seconds": 0.00001, "relay": 0.01}))
    argv = ["--broker-state", str(broker), "--since", "2026-10-20", "--until", "2026-10-20",
            "--prices", str(prices)]
    monkeypatch.setattr(metering_receipts, "BOXHOSTD_STATE", tmp_path)
    monkeypatch.setattr(metering_receipts.os, "geteuid", lambda: 1001)
    with pytest.raises(SystemExit, match="founder-only"):
        metering_receipts.main(argv)
    monkeypatch.setattr(metering_receipts.os, "geteuid", lambda: 0)
    metering_receipts.main(argv)
    read = json.loads(capsys.readouterr().out)
    assert read["receipts"] == [{"day": "2026-10-20", "owner": "a", "usd": 0.026, "drivers": {
        "box_seconds": {"unit": "box-seconds", "quantity": 600, "usd": 0.006},
        "relay": {"unit": "GB", "quantity": 2, "usd": 0.02}}}]
    assert read["unmeasured"] == ["loop_seconds", "storage"]
    assert read["write_failures"] == {}


@pytest.mark.verifies("cost-driver-receipts:Receipts measure and never limit")
def test_admission_module_importing_metering_fails_the_n12_guard(tmp_path):
    shape = check_shapes.SHAPES["N12"]
    assert all((check_shapes.ROOT / path).is_file() for path in shape.paths)
    assert check_shapes.forbidden("N12", shape) == []
    routing = tmp_path / "tinyassets/broker/routing.py"
    routing.parent.mkdir(parents=True)
    routing.write_text("# never reads tinyassets.metering\nimport asyncio\n")
    assert check_shapes.forbidden("N12", shape, tmp_path) == []
    for planted in ("import tinyassets.metering", "from tinyassets.metering import Meter",
                    "def wake():\n    from tinyassets import metering",
                    "from tinyassets import usage_policy, metering"):
        routing.write_text(planted + "\n")
        assert check_shapes.forbidden("N12", shape, tmp_path), planted

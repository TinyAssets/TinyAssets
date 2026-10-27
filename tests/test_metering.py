"""W10 cost-driver receipts (row N12): recorded at the lifecycle, rolled up daily, read by root."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from scripts import check_shapes, metering_receipts
from tests.support.admission import drive
from tests.test_box_public_egress import exchange, proxy  # noqa: F401
from tests.test_owner_box_lifecycle import fleet  # noqa: F401
from tinyassets import metering
from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.host_service import recover_daemons
from tinyassets.boxes.provider import BoxError
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
def test_a_start_that_fails_before_its_sandbox_reads_no_cgroup(metered):
    read = []
    metered.runtime.usage = lambda uid: read.append(uid) or USAGE

    def refuse(owner, uid):
        raise BoxError("relay unavailable")

    metered.manager.relays.bind = refuse
    with pytest.raises(Exception):  # noqa: B017 - the broker reports the start's failure
        metered.wake("a")
    assert metered.runtime.starts == []
    assert (read, metered.meter.failures, metered.meter.path.exists()) == ([], 0, False)


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
def test_metering_write_failure_never_blocks_a_box_and_is_counted(
        fleet, tmp_path, caplog, monkeypatch):  # noqa: F811
    fleet.manager.meter = meter = Meter(tmp_path / "absent" / "metering.db", source="boxhostd")
    fleet.runtime.usage = lambda uid: USAGE
    uid = fleet.wake("a")
    drive(fleet.routing.sleep("a"))
    assert fleet.runtime.stops == [uid]
    assert meter.failures >= 2
    assert "receipts are incomplete" in caplog.text
    counted = Meter(tmp_path / "metering.db", source="broker")
    counted.add("a", "storage", 1, time.time())  # a derived driver is not a counter
    today, tomorrow = day_of(time.time()), time.time() + 86400
    monkeypatch.setattr(metering, "time", SimpleNamespace(time=lambda: tomorrow))
    assert counted.receipts(today, today)["write_failures"] == {today: 1}


async def _relay(proxy, monkeypatch, payload):  # noqa: F811
    """One CONNECT through the broker; returns the reply and the thread each meter write ran on."""
    loop, written, threads, add = asyncio.get_running_loop(), asyncio.Event(), [], Meter.add

    def spy(self, *args):
        add(self, *args)
        threads.append(threading.current_thread())
        loop.call_soon_threadsafe(written.set)

    async def echo(reader, writer):
        writer.write(await reader.read())
        await writer.drain()
        writer.close()

    monkeypatch.setattr(Meter, "add", spy)
    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    monkeypatch.setattr("tinyassets.broker.public_egress.destination", lambda *a: ["127.0.0.1"])
    port = upstream.sockets[0].getsockname()[1]
    try:
        reply = await exchange(proxy, f"CONNECT example.test:{port} HTTP/1.1\r\n\r\n".encode()
                               + payload)
    finally:
        upstream.close()
    await asyncio.wait_for(written.wait(), 2)
    return reply, threads


@pytest.mark.verifies("cost-driver-receipts:Receipts measure and never limit")
@pytest.mark.asyncio
async def test_relay_bytes_are_counted_per_owner_off_the_event_loop(proxy, monkeypatch):  # noqa: F811
    payload = b"x" * 5000
    reply, threads = await _relay(proxy, monkeypatch, payload)
    assert reply.endswith(payload)
    assert len(threads) == 1 and threads[0] is not threading.current_thread()
    with sqlite3.connect(proxy._public_egress.meter.path) as db:
        assert db.execute("SELECT owner, driver, quantity FROM meter_daily").fetchall() == [
            ("alice", "relay", 2 * len(payload))]


@pytest.mark.verifies("cost-driver-receipts:Receipts measure and never limit")
@pytest.mark.asyncio
async def test_relay_metering_failure_is_counted_and_never_reaches_the_proxy(
        proxy, monkeypatch):  # noqa: F811
    absent = proxy._owner_identities.path.parent / "absent" / "metering.db"
    meter = proxy._public_egress.meter = Meter(absent, source="broker")
    payload = b"y" * 100
    reply, _ = await _relay(proxy, monkeypatch, payload)
    assert reply.endswith(payload)
    assert meter.failures == 1
    assert proxy._public_egress.active == {}


@pytest.mark.verifies("cost-driver-receipts:One receipt per owner per day")
def test_daily_rollup_splits_an_interval_across_every_day_it_spans(tmp_path):
    meter = Meter(tmp_path / "metering.db", source="boxhostd")
    meter.open_interval("a", 300001, DAY + 86400 - 600)
    meter.daily(DAY + 86400 + 60, lambda: {300001: (None, 2 * 10**9, 20 * 10**9)})
    [open_day] = meter.receipts("2026-10-20", "2026-10-20")["events"]
    assert (open_day["data"]["quantity"], open_day["data"]["peak_memory_bytes"]) == (600, None)
    meter.daily(DAY + 2 * 86400 + 60, lambda: {})
    # Awake 600 s on the 20th, all of the 21st and 300 s on the 22nd: CPU splits pro rata.
    meter.close_interval(300001, "stopped", DAY + 2 * 86400 + 300,
                         {"peak_memory_bytes": 512 * 2**20, "cpu_usec": 87_300_000})
    meter.daily(DAY + 3 * 86400 + 1, lambda: {})
    events = meter.receipts("2026-10-20", "2026-10-22")["events"]
    assert [(e["time"], e["type"], e["subject"], e["data"]["quantity"]) for e in events] == [
        ("2026-10-20T00:00:00Z", "io.tinyassets.meter.box_seconds", "a", 600),
        ("2026-10-21T00:00:00Z", "io.tinyassets.meter.box_seconds", "a", 86400),
        ("2026-10-21T00:00:00Z", "io.tinyassets.meter.storage", "a", 2),
        ("2026-10-22T00:00:00Z", "io.tinyassets.meter.box_seconds", "a", 300)]
    assert [e["data"].get("cpu_seconds") for e in events] == [
        pytest.approx(0.6), pytest.approx(86.4), None, pytest.approx(0.3)]
    first = events[0]
    assert {key: first[key] for key in ("specversion", "source", "datacontenttype")} == {
        "specversion": "1.0", "source": "tinyassets/boxhostd",
        "datacontenttype": "application/json"}
    assert first["data"] == {"day": "2026-10-20", "owner": "a", "center": None,
                             "driver": "box_seconds", "unit": "box-seconds", "quantity": 600,
                             "size_class": "thin", "intervals": 1,
                             "peak_memory_bytes": 512 * 2**20, "cpu_seconds": pytest.approx(0.6)}
    assert first["id"] == open_day["id"]
    assert events[2]["data"]["provisioned_bytes"] == 20 * 10**9
    assert len({e["id"] for e in events}) == 4


@pytest.mark.verifies("cost-driver-receipts:One receipt per owner per day")
def test_a_superseded_interval_gives_back_the_days_it_never_ran(tmp_path):
    meter = Meter(tmp_path / "metering.db", source="boxhostd")
    meter.open_interval("a", 300001, DAY + 82800)  # 23:00 on the 20th; seen until 00:00
    meter.seen(300001, DAY + 86400)
    meter.daily(DAY + 86400 + 60, lambda: {})  # rolled while open: counted to midnight
    meter.daily(DAY + 2 * 86400 + 60, lambda: {})  # and all of the 21st
    # Its close was lost; the next start supersedes it at its last sighting.
    meter.open_interval("a", 300001, DAY + 2 * 86400 + 120)
    events = meter.receipts("2026-10-20", "2026-10-21")["events"]
    assert [(e["time"][:10], e["data"]["quantity"]) for e in events] == [("2026-10-20", 3600)]


@pytest.mark.verifies("cost-driver-receipts:One receipt per owner per day")
def test_an_unfinished_day_is_refused(tmp_path):
    meter = Meter(tmp_path / "metering.db", source="broker")
    meter.add("a", "relay", 10, time.time())
    today, yesterday = day_of(time.time()), day_of(time.time() - 86400)
    assert meter.receipts(yesterday, yesterday)["events"] == []
    with pytest.raises(ValueError, match="not a finished UTC day"):
        meter.receipts(yesterday, today)


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
def test_only_the_allowlist_imports_metering_in_any_form(tmp_path):
    assert check_shapes.metering_import_problems() == []
    assert all(any(check_shapes.ROOT.glob(path)) for path in check_shapes.METERING_IMPORTERS)
    broker = tmp_path / "tinyassets/broker"
    broker.mkdir(parents=True)
    (broker / "public_egress.py").write_text("from tinyassets.metering import Meter\n")
    routing = broker / "routing.py"
    routing.write_text("# never reads tinyassets.metering\nNAME = 'tinyassets.metering'\n")
    assert check_shapes.metering_import_problems(tmp_path) == []
    refused = ["N12 metering-not-admission: tinyassets/broker/routing.py imports "
               "tinyassets.metering"]
    for planted in ("import tinyassets.metering", "import tinyassets.metering as receipts",
                    "from tinyassets.metering import Meter", "from tinyassets import metering",
                    "from .. import metering", "from ..metering import Meter",
                    "from tinyassets import (\n    usage_policy,\n    metering,\n)",
                    "def wake():\n    from tinyassets import metering",
                    "meter = __import__('tinyassets.metering')",
                    "import importlib\nmeter = importlib.import_module('tinyassets.metering')"):
        routing.write_text(planted + "\n")
        assert check_shapes.metering_import_problems(tmp_path) == refused, planted
    routing.unlink()
    (tmp_path / "tinyassets/usage_policy.py").write_text("from . import metering\n")
    assert check_shapes.metering_import_problems(tmp_path) == [
        "N12 metering-not-admission: tinyassets/usage_policy.py imports tinyassets.metering"]

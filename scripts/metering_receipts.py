"""W10 founder read: cost-driver receipts, one per owner per day.

Run as root on the box host with PYTHONPATH=/opt/tinyassets/boxhostd:

    python3 scripts/metering_receipts.py --broker-state \\
        "$(docker volume inspect tinyassets-data --format '{{ .Mountpoint }}')/.broker/state" \\
        [--since 2026-10-20 --until 2026-10-26] [--prices prices.json]

Root is the gate, as for ``turns_in_flight.py``: boxhostd's store is in its
root-private state directory and the broker's in ``/data/.broker/state``
(uid 1002, mode 0700), which no owner, box or daemon uid can open. Both are
opened read-only. A day appears once boxhostd's daily job has rolled it up.

``prices.json`` maps a driver to US dollars per unit (``box_seconds`` per
box-second, ``relay`` per GB, ``storage`` per GB-day). Unpriced drivers are
listed, never guessed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from tinyassets.metering import DRIVERS, Meter

BOXHOSTD_STATE = Path("/var/lib/boxhostd/state")


def receipts(stores, since, until, prices):
    events, failures = [], {}
    for meter in stores:
        read = meter.receipts(since, until)
        events += read["events"]
        if read["write_failures"]:
            failures[read["source"]] = read["write_failures"]
    days = {}
    for event in events:
        data = event["data"]
        receipt = days.setdefault((data["day"], data["owner"]), {
            "day": data["day"], "owner": data["owner"], "drivers": {}})
        driver = receipt["drivers"].setdefault(data["driver"], {
            "unit": data["unit"], "quantity": 0.0})
        driver["quantity"] += data["quantity"]
    for receipt in days.values():
        for name, driver in receipt["drivers"].items():
            if name in prices:
                driver["usd"] = round(driver["quantity"] * prices[name], 6)
        receipt["usd"] = round(sum(d.get("usd", 0) for d in receipt["drivers"].values()), 6)
    measured = {event["data"]["driver"] for event in events}
    return {"since": since, "until": until, "receipts": list(days.values()),
            "unpriced": sorted(measured - prices.keys()),
            "unmeasured": sorted(DRIVERS.keys() - measured),
            "write_failures": failures, "events": events}


def main(argv=None):
    if os.geteuid() != 0:
        raise SystemExit("receipts are a founder-only read: run as root on the box host")
    yesterday = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--broker-state", type=Path, required=True)
    parser.add_argument("--since", default=(yesterday - dt.timedelta(days=6)).isoformat())
    parser.add_argument("--until", default=yesterday.isoformat())
    parser.add_argument("--prices", type=Path)
    args = parser.parse_args(argv)
    prices = json.loads(args.prices.read_text()) if args.prices else {}
    stores = (Meter(BOXHOSTD_STATE / "metering.db", source="boxhostd"),
              Meter(args.broker_state / "metering.db", source="broker"))
    print(json.dumps(receipts(stores, args.since, args.until, prices), indent=2))


if __name__ == "__main__":
    main()

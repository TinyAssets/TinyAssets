"""Render only aggregate P2a gate measurements in a workflow summary."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    print("## C6 P2a production host gates\n")
    if not path.is_file():
        print("No result artifact: the gate run did not produce a report.")
        return
    report = json.loads(path.read_text())
    print(f"Overall: **{'PASS' if report.get('pass') else 'FAIL'}**\n")
    if report.get("fatal"):
        failure = str(report["fatal"]).replace("`", "'").replace("\n", " ")
        print(f"Harness failure: `{failure}`\n")
    print("| Gate | Result | Aggregate measurements |\n|---|---|---|")
    for name in ("G1", "G3", "G4", "G6"):
        gate = report.get("gates", {}).get(name, {})
        measurements = ", ".join(f"{key}={value}" for key, value in gate.items() if key != "pass")
        # All report fields are host facts or aggregate numbers, never owner data.
        measurements = measurements.replace("|", "/").replace("\n", " ")
        print(f"| {name} | {'PASS' if gate.get('pass') else 'FAIL'} | {measurements} |")


if __name__ == "__main__":
    main()

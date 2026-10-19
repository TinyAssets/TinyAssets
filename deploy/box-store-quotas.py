#!/usr/bin/env python3
"""Quiescence check retained for the outer-store backup until backup splitting."""
import argparse
import json
import subprocess
from pathlib import Path


def check_snapshot(data: Path, *, quiescent: bool = False) -> None:
    """Caller holds the exclusive layout lock; no mutation or recovery here."""
    marker = data / ".layout.json"
    if not marker.exists():
        return  # pre-owner-box archive
    layout = json.loads(marker.read_text())
    if layout.get("box_layout") != "owner-box":
        return
    if layout.get("state") != "stable":
        raise ValueError("owner-box snapshot layout is not stable")
    if quiescent:
        result = subprocess.run([
            "/opt/tinyassets/boxhostd/current/runsc", "--root", "/var/lib/boxhostd/state/runsc",
            "--systemd-cgroup=false", "list", "--format=json"],
            check=True, capture_output=True, text=True, timeout=10)
        if json.loads(result.stdout) != []:
            raise ValueError("surviving owner sandbox prevents coherent snapshot/restore")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-data", type=Path, required=True)
    parser.add_argument("--quiescent", action="store_true")
    args = parser.parse_args()
    check_snapshot(args.snapshot_data, quiescent=args.quiescent)

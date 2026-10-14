#!/usr/bin/env python3
"""P2a G4 durability check run by idle boxhostd after sandbox cleanup."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path

STORE = Path("/var/lib/boxhostd/owners")
MOUNT = "var-lib-boxhostd-owners.mount"
DEVICE = "box-store-device.service"


def command(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)


def check() -> dict:
    if any(STORE.iterdir()):
        return {"pass": False, "reason": "durability check requires empty P2a store"}
    marker = STORE / f".g4-fsync-{secrets.token_hex(8)}"
    payload = secrets.token_bytes(4096)
    result: dict = {"pass": False}
    try:
        with marker.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        directory = os.open(STORE, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        command("systemctl", "stop", MOUNT)
        command("systemctl", "stop", DEVICE)
        command("systemctl", "start", MOUNT)
        survived = marker.read_bytes() == payload
        result = {"pass": survived, "fsync_remount": survived}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        result = {"pass": False, "reason": type(error).__name__}
    finally:
        try:
            command("systemctl", "start", MOUNT)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            result = {"pass": False, "reason": f"restart:{type(error).__name__}"}
        try:
            marker.unlink(missing_ok=True)
        except OSError as error:
            result = {"pass": False, "reason": f"cleanup:{type(error).__name__}"}
    return result


if __name__ == "__main__":
    try:
        result = check()
    except (OSError, ValueError) as error:
        result = {"pass": False, "reason": type(error).__name__}
    print(json.dumps(result, sort_keys=True))

#!/usr/bin/env python3
"""Select the single serving path from the stable data layout marker."""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
from pathlib import Path


def _exit_on_signal(signum, _frame) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 - a flush must not hold up the exit
            pass
    os._exit(128 + signum)


def exit_on_sigterm() -> None:
    """Give PID 1 the SIGTERM every other process gets.

    This process is PID 1 in the daemon container, and Linux ignores any signal
    PID 1 has no handler for. So `docker stop` always ran to SIGKILL: during
    startup, before uvicorn installs its handler, and after uvicorn's graceful
    shutdown, whose re-raised SIGTERM was ignored while interpreter exit joined
    a worker thread still serving a request. This process is the router; owner
    turns run in their boxes and outlive a dropped reply stream, which uvicorn
    has already drained for `GRACEFUL_SHUTDOWN_S` by the time this runs.
    """
    signal.signal(signal.SIGTERM, _exit_on_signal)


def check_layout(data: Path) -> None:
    marker = data / ".layout.json"
    try:
        info = marker.lstat()
    except FileNotFoundError:
        raise RuntimeError("owner-box layout marker is required") from None
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("data layout marker is not a regular file")
    document = json.loads(marker.read_text(encoding="utf-8"))
    if (not isinstance(document, dict)
            or document.get("state") not in {"stable", "recovering"}):
        raise RuntimeError("data layout migration is incomplete")
    box_layout = document.get("box_layout")
    if box_layout == "owner-box":
        if os.geteuid() != 1001:
            raise RuntimeError("owner-box daemon must start unprivileged")
        return
    raise RuntimeError("unrecognized box layout")


def main() -> None:
    exit_on_sigterm()
    data = Path(os.environ.get("TINYASSETS_DATA_DIR", "/data"))
    check_layout(data)
    from tinyassets.boxes.daemon_control import broker_bootstrap
    from tinyassets.broker.supervisor import BrokerSupervisor
    from tinyassets.sqlite_floor import require_sqlite_floor

    require_sqlite_floor()
    bootstrap = broker_bootstrap()
    supervisor = BrokerSupervisor.from_boxhost(
        data, socket_path=Path(bootstrap["broker_socket"]), proof=bootstrap["proof"])
    supervisor.start()
    from tinyassets.universe_server import main as serve

    serve()


if __name__ == "__main__":
    main()

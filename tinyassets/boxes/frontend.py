"""Unprivileged owner-box daemon entry. Never performs a layout migration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from tinyassets.boxes.bootstrap import channel_factory, daemon_bootstrap
from tinyassets.boxes.production_files import ProductionFileProvider
from tinyassets.boxes.provider import ExecLimits


def configure(data: Path) -> ProductionFileProvider:
    if os.geteuid() != 1001:
        raise RuntimeError("owner-box frontend requires the retired daemon identity")
    marker = json.loads((data / ".layout.json").read_text())
    if marker.get("state") != "stable" or marker.get("box_layout") != "owner-box":
        raise RuntimeError("owner-box frontend requires a committed layout")
    from tinyassets.broker.supervisor import BrokerSupervisor

    bootstrap = daemon_bootstrap()
    supervisor = BrokerSupervisor.from_boxhost(data, socket_path=Path(bootstrap["broker_socket"]),
                                               proof=bootstrap["proof"])
    supervisor.start()
    provider = ProductionFileProvider(channel_factory=channel_factory)
    from tinyassets.agent_loop.served_chat import configure_box_provider

    configure_box_provider(provider, limits=ExecLimits())
    return provider


def main() -> None:
    from tinyassets.boxes.layout import hold

    data = Path(os.environ.get("TINYASSETS_DATA_DIR", "/data"))
    with hold(data):
        provider = configure(data)
        try:
            from tinyassets.sqlite_floor import require_sqlite_floor
            from tinyassets.universe_server import main as serve

            require_sqlite_floor()
            serve()
        finally:
            provider.close()


if __name__ == "__main__":
    main()

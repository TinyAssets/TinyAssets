"""Test-only origin: real FastMCP/uvicorn shutdown with a deterministic long tool.

No provider, production network, credentials, or production data. The parent
owns the socket. File barriers make the idle-poll/start/signal race repeatable.
"""

import os
import socket
import sys
import time
from pathlib import Path

import uvicorn
from fastmcp import FastMCP


def main():
    root = Path(sys.argv[1])
    listener = socket.socket(fileno=int(sys.argv[2]))
    server = FastMCP("deploy-traffic-fixture")

    @server.tool()
    def converse(client_send_id: str, message: str) -> str:
        # Exclusive creation records an effect once, including across a restart.
        with (root / f"effect-{client_send_id}").open("x") as out:
            out.write(message)
            out.flush()
            os.fsync(out.fileno())
        (root / "workspace.txt").write_text(message, encoding="utf-8")
        (root / "started").write_text(client_send_id)
        while not (root / "finish").exists():
            time.sleep(0.02)
        (root / "completed").write_text(client_send_id)
        return "TURN_FINISHED"

    # Same transport and shutdown setting as universe_server._serve_configs.
    from tinyassets.universe_server import GRACEFUL_SHUTDOWN_S

    uvicorn.Server(uvicorn.Config(
        server.http_app(path="/mcp", transport="streamable-http"),
        log_level="info", timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
    )).run(sockets=[listener])


if __name__ == "__main__":
    main()

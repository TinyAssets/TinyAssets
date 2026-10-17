"""Serve the existing TinyAssets app inside an admitted owner box."""

from __future__ import annotations

import argparse
import signal
from pathlib import Path


def create_app(*, relay: Path, secret_file: Path):
    from tinyassets.owner_runtime import start

    start(relay)
    from tinyassets.owner_ingress import OwnerIngress
    from tinyassets.universe_server import create_streamable_http_app

    return OwnerIngress(create_streamable_http_app(), secret_file)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--secret-file", required=True, type=Path)
    parser.add_argument("--relay", type=Path, default=Path("/run/boxhostd/relay.sock"))
    args = parser.parse_args()
    app = create_app(relay=args.relay, secret_file=args.secret_file)
    serve(app, args.socket)


def serve(app, socket_path: Path):
    """Finish the ASGI lifespan and tool flush before acknowledging SIGTERM."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, uds=str(socket_path),
                                         timeout_graceful_shutdown=10))
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def stop(*_):
        server.should_exit = True

    # Uvicorn re-raises captured signals after its lifespan. Keep that re-raise
    # from terminating Python before the local tool journal's finally runs.
    for sig in previous:
        signal.signal(sig, stop)
    try:
        server.run()
    finally:
        from tinyassets.owner_runtime import current

        current().tools.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()

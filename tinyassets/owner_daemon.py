"""Serve the existing TinyAssets app inside an admitted owner box."""

from __future__ import annotations

import argparse
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
    import uvicorn

    try:
        uvicorn.run(app, uds=str(args.socket))
    finally:
        from tinyassets.owner_runtime import current

        current().tools.close()


if __name__ == "__main__":
    main()

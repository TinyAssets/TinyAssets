"""Open one restored credential and one restored browser session.

The DR drill assumes the keyless OIDC role first, then runs this. A missing
key or an unopenable record fails the drill and names the key service.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/vault_dr_open.py")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--center", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    from tinyassets.broker.key_service import KeyServiceUnavailable, broker_service

    try:
        service = broker_service(args.data_root)
        probe = service.probe()
    except KeyServiceUnavailable:
        print("key service", file=sys.stderr)
        return 1
    if probe.get("status") != "ok":
        print("key service", file=sys.stderr)
        return 1
    try:
        records = service.load_center(args.center)
    except KeyServiceUnavailable:
        print("key service", file=sys.stderr)
        return 1
    if not records:
        print("key service: restored credential did not open", file=sys.stderr)
        return 1
    from tinyassets.broker.key_service import account_id, open_blob
    from tinyassets.broker.root_key import ReconnectRequired

    account = account_id(args.owner)
    ledger = args.data_root / ".broker" / "outbound.db"
    if not ledger.is_file():
        print("key service: restored browser session did not open", file=sys.stderr)
        return 1
    import sqlite3

    with sqlite3.connect(ledger) as conn:
        conn.row_factory = sqlite3.Row
        present = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='browser_vault'"
        ).fetchone()
        row = None
        if present is not None:
            row = conn.execute(
                "SELECT id, owner, home, revision, sealed FROM browser_vault "
                "WHERE home = ? AND owner = ? AND length(sealed) > 0 LIMIT 1",
                (args.center, args.owner),
            ).fetchone()
    if row is None:
        print("key service: restored browser session did not open", file=sys.stderr)
        return 1
    try:
        opened = open_blob(
            service,
            owner=row["owner"],
            center=row["home"],
            blob=row["sealed"],
            generation=int(row["revision"]),
            record_id=row["id"],
        )
    except ReconnectRequired:
        print("key service: restored browser session did not open", file=sys.stderr)
        return 1
    if not opened:
        print("key service: restored browser session did not open", file=sys.stderr)
        return 1
    print("opened one restored credential")
    print("opened one restored browser session")
    print(f"account {account[:8]} key service ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - the drill must name the key service
        from tinyassets.broker.root_key import ReconnectRequired

        if isinstance(exc, ReconnectRequired):
            print("key service: restored credential did not open", file=sys.stderr)
        else:
            print("key service", file=sys.stderr)
        raise SystemExit(1) from None

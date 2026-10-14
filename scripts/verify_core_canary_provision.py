"""Fail the deploy unless the dedicated core-canary owner is configured.

The synthetic owner is a one-time founder step (``docs/host-actions.md``,
"One-time core-canary setup"). This script does not create that owner. It
checks that ``TINYASSETS_CORE_CANARY_CONFIG`` is JSON with the eight non-empty
string fields ``scripts/core_capability_live.py`` probes. Secret values are
never printed. ``deploy-prod`` runs this before any step that touches the host.
"""

from __future__ import annotations

import json
import os
import sys

REQUIRED = (
    "email",
    "password",
    "universe_id",
    "connection_id",
    "service_path",
    "service_hmac_key",
    "intake_receiver_id",
    "branch_id",
)


def main() -> int:
    raw = os.environ.get("TINYASSETS_CORE_CANARY_CONFIG", "")
    if not raw.strip():
        print(
            "::error::core canary is not provisioned: TINYASSETS_CORE_CANARY_CONFIG is empty",
            file=sys.stderr,
        )
        return 1
    try:
        config = json.loads(raw)
    except ValueError:
        print("::error::core canary config is not JSON", file=sys.stderr)
        return 1
    if not isinstance(config, dict):
        print("::error::core canary config is not a JSON object", file=sys.stderr)
        return 1
    missing = [
        key for key in REQUIRED
        if not isinstance(config.get(key), str) or not config[key]
    ]
    if missing:
        print(
            "::error::core canary is not provisioned; missing or empty: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    print("core canary config has the required fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

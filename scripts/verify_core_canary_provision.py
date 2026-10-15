"""Record whether the dedicated core-canary owner is configured.

The synthetic owner is a one-time founder step (``docs/host-actions.md``,
"One-time core-canary setup"). This script does not create that owner. It
checks that ``TINYASSETS_CORE_CANARY_CONFIG`` is JSON with the eight non-empty
string fields ``scripts/core_capability_live.py`` probes. Secret values are
never printed.

A missing or incomplete config is not provisioned: this prints a loud warning,
writes ``provisioned=0``, and exits 0. ``deploy_fail_safe.sh`` refuses only a
deploy that would move data when that value is not ``1``. Every other deploy
continues. A complete config writes ``provisioned=1``.
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


def classify(raw: str) -> tuple[bool, str]:
    """Return ``(provisioned, reason)``. ``reason`` is empty when provisioned."""
    if not raw.strip():
        return False, "TINYASSETS_CORE_CANARY_CONFIG is empty"
    try:
        config = json.loads(raw)
    except ValueError:
        return False, "core canary config is not JSON"
    if not isinstance(config, dict):
        return False, "core canary config is not a JSON object"
    missing = [
        key for key in REQUIRED
        if not isinstance(config.get(key), str) or not config[key]
    ]
    if missing:
        return False, "missing or empty: " + ", ".join(missing)
    return True, ""


def main() -> int:
    provisioned, reason = classify(os.environ.get("TINYASSETS_CORE_CANARY_CONFIG", ""))
    value = "1" if provisioned else "0"
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"provisioned={value}\n")
    print(f"core_canary_provisioned={value}")
    if provisioned:
        print("core canary config has the required fields")
        return 0
    print(
        "::warning::core canary is not provisioned: " + reason
        + ". Data-moving deploys refuse; every other deploy continues.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

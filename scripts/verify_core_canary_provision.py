"""Record whether the dedicated core-canary owner secret is configured.

The founder signs up at https://tinyassets.io and pastes JSON with exactly
``email`` and ``password`` (``docs/host-actions.md``). This script does not
create that owner and does not provision the host. The deploy pipes the same
secret to ``python -m tinyassets.core_canary``, which derives and stores the
HMAC key and the other probe fields. Secret values are never printed.

A missing or incomplete config is not provisioned: this prints a loud warning,
writes ``provisioned=0``, and exits 0. ``deploy_fail_safe.sh`` refuses only a
deploy that would move data when that value is not ``1``. Every other deploy
continues. A complete config writes ``provisioned=1``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tinyassets.core_canary import CONFIG_FIELDS, parse_founder_config  # noqa: E402

REQUIRED = CONFIG_FIELDS


def classify(raw: str) -> tuple[bool, str]:
    """Return ``(provisioned, reason)``. ``reason`` is empty when provisioned."""
    parsed, reason = parse_founder_config(raw)
    return parsed is not None, reason


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

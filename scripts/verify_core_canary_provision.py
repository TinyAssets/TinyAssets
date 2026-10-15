"""Sign in as the dedicated core-canary owner and make it ready, before a deploy.

The canary self-provisions (``scripts/core_canary_provision.py``): from the
three ``TINYASSETS_CANARY_*`` secrets it signs in like a normal user and creates
or repairs its default model, signing connection, echo branch and intake.
Secret values are never printed.

Missing secrets or a failed sign-in/repair is not provisioned: this prints a
loud warning, writes ``provisioned=0``, and exits 0. ``deploy_fail_safe.sh``
refuses only a deploy that would move data when that value is not ``1``. Every
other deploy continues. A ready canary writes ``provisioned=1``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.core_canary_provision import credentials, missing_secrets, provision  # noqa: E402


async def _provision(config: dict) -> None:
    import httpx
    from fastmcp import Client

    from scripts import core_capability_live as live

    async with httpx.AsyncClient(
        timeout=180,
        follow_redirects=True,
        headers={"Origin": live.ORIGIN, "User-Agent": "tinyassets-core-canary/1.0"},
    ) as http:
        token = await live.login(config, http)
        async with Client(live.stream_transport(token, {}), timeout=180) as client:

            async def call(name, **arguments):
                result = await client.call_tool(name, arguments)
                value = json.loads(result.data) if isinstance(result.data, str) else result.data
                if result.is_error or not isinstance(value, dict) or value.get("error"):
                    code = value.get("error", "tool_failed") if isinstance(value, dict) else ""
                    raise live.ProbeFailure(name + " refused: " + live.failure_code(code))
                return value

            await provision(config, http, live.ORIGIN, call)


def classify() -> tuple[bool, str]:
    """Return ``(provisioned, reason)``. ``reason`` is empty when provisioned."""
    config = credentials()
    if config is None:
        return False, "missing secrets: " + ", ".join(missing_secrets())
    try:
        asyncio.run(_provision(config))
    except Exception as exc:  # noqa: BLE001 - reported as a code, never a value
        name = type(exc).__name__
        detail = f" ({exc})" if name in ("ProvisionError", "ProbeFailure") else ""
        return False, "canary setup failed: " + name + detail
    return True, ""


def main() -> int:
    provisioned, reason = classify()
    value = "1" if provisioned else "0"
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"provisioned={value}\n")
    print(f"core_canary_provisioned={value}")
    if provisioned:
        print("core canary signed in and is ready")
        return 0
    print(
        "::warning::core canary is not provisioned: " + reason
        + ". Data-moving deploys refuse; every other deploy continues.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

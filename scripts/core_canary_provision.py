"""Make the dedicated core-canary owner ready, the way a normal user would.

The canary signs in with three secrets (``TINYASSETS_CANARY_EMAIL``,
``TINYASSETS_CANARY_PASSWORD``, ``TINYASSETS_CANARY_HMAC_KEY``) and then
creates or repairs, through the same public doors any owner uses:

- a saved default model: a free model (``:free``) its connected source offers,
  since paid models exceed the canary's cost cap;
- the HTTP connection ``canary-hmac`` (POST ``/sign`` only) to the signing
  service in ``TINYASSETS_CANARY_SERVICE_URL`` (a repo variable; an existing
  ``canary-hmac`` connection is reused without it);
- the private branch ``canary-echo`` (``nonce`` -> ``echo``).

The patch intake is the deployment's own (``read_graph pending_requests``);
receivers are not chosen per owner. Every step is idempotent. Returns the
complete probe config for ``scripts/core_capability_live.py``. Secret values are
never printed; errors carry codes only.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

CONNECTION = "canary-hmac"
SERVICE_PATH = "/sign"
BRANCH = "canary-echo"
BRANCH_SPEC = {
    "name": BRANCH,
    "entry_point": "echo",
    "node_defs": [
        {
            "node_id": "echo",
            "display_name": "Echo",
            "input_keys": ["nonce"],
            "output_keys": ["echo"],
            "source_code": 'def run(state):\n    return {"echo": state["nonce"]}\n',
        }
    ],
    "edges": [{"from": "echo", "to": "END"}],
    "state_schema": [{"name": "nonce", "type": "str"}, {"name": "echo", "type": "str"}],
}
SECRETS = ("TINYASSETS_CANARY_EMAIL", "TINYASSETS_CANARY_PASSWORD", "TINYASSETS_CANARY_HMAC_KEY")


class ProvisionError(RuntimeError):
    """A provisioning step could not complete; the message is a code, never a value."""


def credentials() -> dict | None:
    """The three canary secrets, or None when any is unset."""
    values = [os.environ.get(name, "") for name in SECRETS]
    if not all(values):
        return None
    email, password, key = values
    return {"email": email, "password": password, "service_hmac_key": key}


def missing_secrets() -> list[str]:
    return [name for name in SECRETS if not os.environ.get(name)]


def pick_free_model(options: dict, current: dict | None) -> dict | None:
    """The current default when it is still a usable free model, else the first one offered."""
    usable = [
        row["reference"]
        for row in options.get("options", [])
        if row.get("in_candidate_catalog")
        and not row.get("reasons")
        and row.get("tools") is not False
        and str(row["reference"].get("model_id", "")).endswith(":free")
    ]
    if current in usable:
        return current
    return usable[0] if usable else None


async def ensure_model(http, origin: str, call) -> dict:
    response = await http.get(origin + "/app/models/preferences")
    response.raise_for_status()
    saved = response.json()
    policy = saved.get("policy") or {}
    options = await call("read_graph", target="model_options")
    choice = pick_free_model(options, policy.get("saved_default"))
    if choice is None:
        raise ProvisionError("no free model is offered by the canary's connected source")
    if policy.get("mode") == "explicit" and policy.get("saved_default") == choice:
        return choice
    document = {
        "expected_generation": saved.get("generation", 0),
        "policy": {
            "version": 2,
            "mode": "explicit",
            "saved_default": choice,
            "fallbacks": [],
            "efforts": [],
        },
    }
    response = await http.post(origin + "/app/models/preferences", json=document)
    if response.status_code >= 400:
        raise ProvisionError(f"saving the default model failed with HTTP {response.status_code}")
    return choice


async def ensure_connection(http, origin: str, call) -> str:
    response = await http.get(origin + "/app/connections")
    response.raise_for_status()
    for row in response.json().get("connections", []):
        if row.get("destination") == CONNECTION and row.get("connection_id"):
            return row["connection_id"]
    url = os.environ.get("TINYASSETS_CANARY_SERVICE_URL", "")
    host = urlsplit(url).hostname if url.startswith("https://") else None
    if not host:
        raise ProvisionError(
            "TINYASSETS_CANARY_SERVICE_URL (https) is unset and no canary-hmac connection exists"
        )
    created = await call(
        "write_graph",
        target="connection",
        operation="connect_http",
        payload_json=json.dumps(
            {
                "destination": CONNECTION,
                # The signing service takes no credential; this door requires one.
                "secret": "canary-unused",
                "auth_scheme": "bearer",
                "allowed_endpoints": [
                    {"host": host, "path_template": SERVICE_PATH, "methods": ["POST"]}
                ],
            }
        ),
    )
    connection_id = created.get("connection_id") or (created.get("connection") or {}).get(
        "connection_id"
    )
    if not connection_id:
        raise ProvisionError("connect_http returned no connection id")
    return connection_id


async def ensure_branch(call) -> str:
    listed = await call("read_graph", target="branches")
    for row in listed.get("branches", []):
        if row.get("name") == BRANCH and row.get("branch_def_id"):
            return row["branch_def_id"]
    created = await call(
        "write_graph", target="branch", operation="create", payload_json=json.dumps(BRANCH_SPEC)
    )
    branch_id = created.get("branch_def_id") or (created.get("branch") or {}).get("branch_def_id")
    if not branch_id:
        raise ProvisionError("branch create returned no branch id")
    return branch_id


async def intake_receiver(call) -> str:
    rail = await call("read_graph", target="pending_requests")
    receiver = (rail.get("patch_intake") or {}).get("receiver_id", "")
    if not receiver:
        raise ProvisionError("this deployment has no patch intake configured")
    return receiver


async def provision(config: dict, http, origin: str, call) -> dict:
    """Fill the probe config in place: model, connection, branch and intake ids.

    ``call(tool, **arguments)`` is the signed-in owner's MCP tool call; every
    call is scoped to the canary's own home, ``config["universe_id"]``.
    """

    async def home(name, **arguments):
        return await call(name, graph_id=config["universe_id"], **arguments)

    config["default_model"] = await ensure_model(http, origin, home)
    config["connection_id"] = await ensure_connection(http, origin, home)
    config["service_path"] = SERVICE_PATH
    config["branch_id"] = await ensure_branch(home)
    config["intake_receiver_id"] = await intake_receiver(home)
    return config

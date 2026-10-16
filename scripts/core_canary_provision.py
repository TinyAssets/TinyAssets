"""Make the dedicated core-canary owner ready, the way a normal user would.

The canary signs in with four secrets (``TINYASSETS_CANARY_EMAIL``,
``TINYASSETS_CANARY_PASSWORD``, ``TINYASSETS_CANARY_HMAC_KEY``,
``TINYASSETS_CANARY_DOOR_KEY``) through the canary-only door the deployment
pins to this one account, refuses any account whose home is not
``TINYASSETS_CANARY_UNIVERSE_ID``, and then creates or repairs, through the
same public doors any owner uses:

- a saved default model: a free model (``:free``) its connected source offers,
  since paid models exceed the canary's cost cap;
- the HTTP connection ``canary-hmac`` (POST ``/sign`` only) to the signing
  service in ``TINYASSETS_CANARY_SERVICE_URL`` (a repo variable; an existing
  ``canary-hmac`` connection is reused without it);
- the private branch ``canary-echo`` (``nonce`` -> ``echo``).

The canary never touches the deployment's patch intake. A missing free
model or a refused model save is a warning too: provider availability says
nothing about the platform. Every step is idempotent. Returns the
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
SECRETS = (
    "TINYASSETS_CANARY_EMAIL",
    "TINYASSETS_CANARY_PASSWORD",
    "TINYASSETS_CANARY_HMAC_KEY",
    # The canary-only sign-in door's shared key. The deployment holds the same
    # value and pins the one account it works for.
    "TINYASSETS_CANARY_DOOR_KEY",
)
# The dedicated canary's home id (repo variable, not a secret). Sign-in refuses
# any other account, so wrong credentials can never repair a real owner.
HOME = "TINYASSETS_CANARY_UNIVERSE_ID"
REQUIRED = (*SECRETS, HOME)


class ProvisionError(RuntimeError):
    """A provisioning step could not complete; the message is a code, never a value."""


def credentials() -> dict | None:
    """The canary secrets and dedicated home id, or None when any is unset."""
    values = [os.environ.get(name, "") for name in REQUIRED]
    if not all(values):
        return None
    email, password, key, door_key, home = values
    return {
        "email": email,
        "password": password,
        "service_hmac_key": key,
        "door_key": door_key,
        "universe_id": home,
    }


def missing_secrets() -> list[str]:
    return [name for name in REQUIRED if not os.environ.get(name)]


def pick_free_model(options: dict, current: dict | None) -> dict | None:
    """The current default when it is still a usable free model, else the first one offered.

    ``options`` is the public door's compact ``model_options`` view searched for
    ``:free`` (``tinyassets/engine_read_views.py``): flat ``models`` rows.
    """
    usable = [
        {"provider_ref": row["provider_ref"], "model_id": row["model_id"]}
        for row in options.get("models") or ()
        if row.get("selectable")
        and not row.get("reasons")
        and row.get("tools") is not False
        and str(row.get("model_id", "")).endswith(":free")
    ]
    if current in usable:
        return current
    return usable[0] if usable else None


def _warn(config: dict, code: str) -> None:
    config.setdefault("provision_warnings", []).append(code)


async def ensure_model(http, origin: str, call, config: dict) -> dict | None:
    response = await http.get(origin + "/app/models/preferences")
    response.raise_for_status()
    saved = response.json()
    policy = saved.get("policy") or {}
    options = await call("read_graph", target="model_options", query=":free")
    choice = pick_free_model(options, policy.get("saved_default"))
    if choice is None:
        _warn(config, "no_free_model_offered")
        return None
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
        # 409: a concurrent canary run saved first. Either way, advisory.
        _warn(config, f"default_model_save_http_{response.status_code}")
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




async def provision(config: dict, http, origin: str, call) -> dict:
    """Fill the probe config in place: model, connection and branch ids.

    ``call(tool, **arguments)`` is the signed-in owner's MCP tool call; every
    call is scoped to the canary's own home, ``config["universe_id"]``.
    """

    async def home(name, **arguments):
        return await call(name, graph_id=config["universe_id"], **arguments)

    config["default_model"] = await ensure_model(http, origin, home, config)
    config["connection_id"] = await ensure_connection(http, origin, home)
    config["service_path"] = SERVICE_PATH
    config["branch_id"] = await ensure_branch(home)
    return config

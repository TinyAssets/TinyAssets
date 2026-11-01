"""Outside clients: who each one is, and the grant each call is admitted under (P0-3).

Only verified resource-token claims identify a client and credential family
(``origin``). The grant is owner policy in the broker (``broker/policy.py``
``outside_clients``): ``full`` by default (decision A1), ``scoped`` to exact
``{universe, agent, capability}`` triples, or ``revoked``, changed only from the
owner's protected session. The router admits every outside call (``admit``) and
stamps the grant on the identity it forwards; the box checks that stamp
(``check_identity``) and re-reads the broker (``refresh``) for work that outlives its
request. ``TINYASSETS_OUTSIDE_DENY=1`` refuses every outside client.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager

#: What the router's admission adds to a verified ``outside_origin``.
STAMP = ("mode", "scopes", "generation")


class OutsideRefused(PermissionError):
    pass


def origin(claims):
    """Called only after signature, issuer, audience and expiry verification."""
    claim = os.environ.get("TINYASSETS_OUTSIDE_CLIENT_CLAIM", "")
    if not claim:
        return None  # Explicit migration gate; never guess AuthKit claim semantics.
    client, issuer = claims.get(claim), claims.get("iss")
    family_claim = os.environ.get("TINYASSETS_OUTSIDE_FAMILY_CLAIM", "sid")
    family = claims.get(family_claim)
    if not all(isinstance(v, str) and 0 < len(v) <= 2048 for v in (client, issuer)):
        raise OutsideRefused("verified outside client/family evidence missing")
    try:
        first_party = json.loads(os.environ.get("TINYASSETS_FIRST_PARTY_CLIENTS", "[]"))
    except ValueError:
        raise OutsideRefused("invalid first-party client configuration") from None
    if not isinstance(first_party, list) or any(not isinstance(v, str) for v in first_party):
        raise OutsideRefused("invalid first-party client configuration")
    if issuer + "|" + client in first_party:
        return None
    if not isinstance(family, str) or not 0 < len(family) <= 2048:
        raise OutsideRefused("verified outside credential family missing")
    authenticated_at = claims.get("auth_time")
    if type(authenticated_at) is not int or authenticated_at <= 0:
        raise OutsideRefused("verified interactive authentication time missing")
    return {"client": hashlib.sha256((issuer + "\0" + client).encode()).hexdigest(),
            "family": hashlib.sha256((issuer + "\0" + family).encode()).hexdigest(),
            "authenticated_at": authenticated_at}


def admit(data_root, identity) -> None:
    """Router: stamp the verified outside client's grant on ``identity``, or refuse.

    The one admission of every outside call, before the router answers it or
    forwards it to the box. A broker that cannot answer raises ``RuntimeError``.
    """
    from tinyassets.broker.policy import PolicyRefused
    from tinyassets.broker.policy_submit import submit

    bound = identity.metadata["outside_origin"]
    if os.environ.get("TINYASSETS_OUTSIDE_DENY") == "1":
        raise OutsideRefused("outside clients are switched off")
    source = "api_key" if identity.metadata.get("auth_provider") == "api_key" else "mcp"
    try:
        grant = submit(data_root, identity.user_id, "outside_admit", client=bound["client"],
                       source=source, authenticated_at=bound.get("authenticated_at"))
    except PolicyRefused as exc:
        raise OutsideRefused(str(exc)) from None
    identity.metadata["outside_origin"] = {**bound, **{k: grant[k] for k in STAMP}}


def still_granted(data_root, identity) -> bool:
    """Router: whether the grant stamped on ``identity`` still stands, unchanged."""
    from tinyassets.broker.policy_submit import submit

    bound = identity.metadata["outside_origin"]
    try:
        grant = submit(data_root, identity.user_id, "outside_check", client=bound["client"],
                       authenticated_at=bound.get("authenticated_at"))
    except Exception:  # noqa: BLE001 - a refused or unreadable grant does not stand
        return False
    return grant["generation"] == bound.get("generation")


def refresh(owner, bound) -> dict:
    """Box: the grant as it stands now, for work that outlives the call that stamped it.

    Any change to the grant since then (a new mode, new scopes, a revoke) refuses.
    """
    from tinyassets.broker.client import BrokerRefused, owner_policy_operation
    from tinyassets.storage import data_dir

    try:
        grant = owner_policy_operation(data_dir(), owner, "outside_check",
                                       client=bound["client"],
                                       authenticated_at=bound.get("authenticated_at"))
    except BrokerRefused as exc:
        raise OutsideRefused(str(exc)) from None
    except Exception:  # noqa: BLE001 - an unreadable grant never admits outside work
        raise OutsideRefused("outside authority unavailable") from None
    if grant["generation"] != bound.get("generation"):
        raise OutsideRefused("outside client authority changed")
    return {**bound, **{k: grant[k] for k in STAMP}}


def refreshed(identity):
    """``identity`` with its grant as it stands now, for a launch that outlives its call
    (``ta`` dispatch, extension checks); an owner identity is returned unchanged."""
    from dataclasses import replace

    bound = identity.metadata.get("outside_origin")
    if bound is None:
        return identity
    return replace(identity, metadata={**identity.metadata,
                                       "outside_origin": refresh(identity.user_id, bound)})


def check_identity(identity, *, universe=None, agent=None, capability=None):
    """An outside identity's stamped grant admits this capability, or it refuses."""
    bound = identity.metadata.get("outside_origin")
    if bound is None:
        return
    if bound.get("mode") not in ("full", "scoped") or type(bound.get("generation")) is not int:
        raise OutsideRefused("outside client authority unavailable")
    if (bound["mode"] == "scoped" and capability is not None
            and {"universe": universe, "agent": agent, "capability": capability}
            not in bound["scopes"]):
        raise OutsideRefused("outside client capability not granted")


def recheck() -> str | None:
    """Before an outside client's external effect: its grant as it stands, so a revoke
    since its call stops it; the label the broker's OPEN gate binds (P0-3)."""
    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    bound = identity.metadata.get("outside_origin") if identity is not None else None
    if bound is None:
        return None
    grant = refresh(identity.user_id, bound)
    return f"{grant['client']}:{grant['generation']}:{grant.get('authenticated_at') or ''}"


def check_mcp_request(identity, document):
    if not isinstance(document, dict):
        raise OutsideRefused("outside batches unavailable")
    check_identity(identity)
    method = document.get("method")
    if method in {"initialize", "notifications/initialized", "ping", "tools/list",
                  "server/discover", "events/list"}:
        return
    if method in {"events/subscribe", "events/unsubscribe"}:
        # The event service checks exact owner/resource access and the existing
        # read_graph grant; this does not mint an outside-client capability.
        return
    if method != "tools/call":
        raise OutsideRefused("outside protocol method unavailable")
    params = document.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("arguments", {}), dict):
        raise OutsideRefused("invalid outside tool request")
    arguments = params.get("arguments", {})
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_founder_home

    scopes = {arguments[k] for k in ("universe_id", "graph_id", "command_center_id")
              if arguments.get(k)}
    agents = {arguments[k] for k in ("agent_id", "agent_binding_id", "conversation_agent")
              if arguments.get(k)}
    if arguments.get("webhook_op") == "mint" or arguments.get("source_op") == "create":
        raise OutsideRefused("channel credentials require the protected owner surface")
    if identity.metadata["outside_origin"]["mode"] == "full":
        return  # the owner's normal authority (A1): no scope to resolve
    indirect = {"run_id", "branch_def_id", "branch_id", "branch_version_id", "automation_id",
                "agent_definition_id", "source_id", "goal_id"}
    if any(arguments.get(k) for k in indirect) and not scopes:
        raise OutsideRefused("outside resource calls require an explicit universe")
    if len(scopes) > 1 or len(agents) > 1:
        raise OutsideRefused("ambiguous outside resource scope")
    universe = next(iter(scopes), None) or get_founder_home(_base_path(), identity.user_id)
    agent = next(iter(agents), "main")
    check_identity(identity, universe=universe, agent=agent, capability=params.get("name", ""))
    identity.metadata["outside_resource"] = {"universe": universe, "agent": agent,
                                             "capability": params.get("name", "")}


def captured_identity(owner):
    """Private run admission marker, written atomically with the queued run."""
    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    if identity is None or identity.metadata.get("outside_origin") is None:
        return None
    if identity.user_id != owner:
        raise OutsideRefused("outside run owner changed")
    check_identity(identity)
    return json.dumps({"owner": owner, "bound": identity.metadata["outside_origin"],
                       "capabilities": identity.capabilities})


@contextmanager
def run_identity(base, run_id):
    """A restarted/queued/resumed run cannot become unrestricted owner work."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.runs import _connect

    with _connect(base) as conn:
        row = conn.execute("SELECT owner_user_id,outside_origin_json FROM runs WHERE run_id=?",
                           (run_id,)).fetchone()
    if row is None or row[1] is None:
        yield
        return
    document = json.loads(row[1])
    if document["owner"] != row[0]:
        raise OutsideRefused("outside run owner changed")
    identity = Identity(document["owner"], document["owner"],
                        capabilities=document["capabilities"],
                        metadata={"outside_origin": refresh(document["owner"],
                                                            document["bound"])})
    with identity_context(identity):
        yield


@contextmanager
def stored_identity(raw, owner):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    if raw is None:
        yield
        return
    document = json.loads(raw)
    if document["owner"] != owner:
        raise OutsideRefused("outside saved work owner changed")
    identity = Identity(owner, owner, capabilities=document["capabilities"],
                        metadata={"outside_origin": refresh(owner, document["bound"])})
    with identity_context(identity):
        yield


@contextmanager
def automation_identity(base, automation):
    from tinyassets.automations import AutomationStore

    conn = AutomationStore(base)._connect(create=False)
    if conn is None:
        raise OutsideRefused("automation authority unavailable")
    try:
        row = conn.execute("SELECT outside_origin_json FROM automations WHERE automation_id=?",
                           (automation.automation_id,)).fetchone()
    finally:
        conn.close()
    # Unsaved automation objects are used by trusted admission checks.
    with stored_identity(row[0] if row else None, automation.owner_principal_id):
        yield


def check_resource(universe):
    """Check the resolved object scope, including indirect IDs and mixed reads."""
    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    if identity is None or identity.metadata.get("outside_origin") is None:
        return
    requested = identity.metadata.get("outside_resource")
    if requested is not None:
        if requested["universe"] != universe:
            raise OutsideRefused("outside resource is not in the admitted universe")
        check_identity(identity, **requested)
    else:
        check_identity(identity)

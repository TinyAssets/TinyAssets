"""The daemon's side of one workspace git operation: authority, then a cell.

Nothing here runs git, reads a credential or touches a repository. This module
turns a workspace request into three things and then gets out of the way:

1. **authority** -- the live grant for exactly this principal, command center,
   connection and repository (``ledger_queries.authorized_connection``), with
   the git scopes narrowed to the one verb this operation needs;
2. **a route** -- an ephemeral rewrite on the command center's own checking
   egress proxy (``git_egress``), so the URL the cell hands git resolves to a
   route id that only the BROKER can turn into an authenticated request. The
   token never leaves the broker process, and the route dies with this call;
3. **a cell** -- the owner's ``workspace-remote`` cell
   (``role_remote_git``), which performs every git step as the owner, inside
   the owner's command center.

What comes back is a sha, a byte count, a ref name and a fixed error class,
validated here before any caller sees it. The request that goes out carries no
secret and no credential reference: there is nothing to resolve any more.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tinyassets.workspace_remote_cell import MAX_BUNDLE_BYTES, ROUTED_OPS

__all__ = [
    "MAX_BUNDLE_BYTES",
    "WORKSPACE_OPS",
    "execute_workspace_operation",
    "reconcile_push_intents",
]

#: The operations the daemon offers. Anything else is refused before authority.
WORKSPACE_OPS = frozenset({"checkout", "push", "ls_remote", "create"})
#: Operations that reach a remote, and the git verb each one needs.
VERB_FOR_OP = {"checkout": "git_read", "push": "git_write", "ls_remote": "git_read"}

_DEFAULT_TIMEOUT_S = 900.0
#: The loopback proxy the in-cell forwarder binds (``deploy/role_git.py``).
_CELL_PROXY = "http://127.0.0.1:3128"
_AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")


class _Refused(Exception):
    """One refusal with a fixed class. Turned into an answer, never raised out."""

    def __init__(self, code: str, error: str) -> None:
        self.code = code
        self.error = error
        super().__init__(error)


def _require(request: dict[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _Refused("bad_argument", f"request.{field} is required")
    return value.strip()


def _authority(request: dict[str, Any], op: str):
    """The live grant and a view narrowed to this operation's one git scope.

    Narrowed because a route carries the scopes of the view it was opened with:
    a checkout must not leave a push route open behind it, even for one call.
    """
    from dataclasses import replace

    from tinyassets.broker.ledger_queries import authorized_connection
    from tinyassets.storage.outbound_connections import (
        GrantResolutionError,
        ProxyRequestError,
    )
    from tinyassets.storage.workspace_authority import connection_git_host

    universe_dir = Path(_require(request, "universe_dir"))
    principal = _require(request, "principal")
    verb = f"{VERB_FOR_OP[op]}:{_require(request, 'owner_repo')}"
    try:
        grant, resource, incarnation = authorized_connection(
            universe_dir.parent, principal=principal, command_center=universe_dir.name,
            grant_id=_require(request, "grant_id"),
            connection_id=_require(request, "connection_id"),
        )
    except (GrantResolutionError, ProxyRequestError) as exc:
        raise _Refused("auth", f"connection authority refused: {type(exc).__name__}") from None
    if verb not in resource.scopes:
        raise _Refused("auth", "the connection carries no git scope for this operation")
    host = connection_git_host(resource)
    if not host or host.lower() != _require(request, "host").lower():
        raise _Refused("auth", "the connection does not reach the requested git host")
    return grant, replace(resource, scopes=(verb,)), incarnation


def _proxy(universe_dir: Path):
    """The command center's checking egress proxy, started on first use."""
    from tinyassets import universe_egress

    socket_path = universe_egress.ensure_proxy(universe_dir)
    proxy = universe_egress._PROXIES.get(str(Path(universe_dir).resolve()))
    if socket_path is None or proxy is None:
        raise _Refused("transport", "the command center's checking egress proxy is unavailable")
    return proxy, socket_path


def _broker_client(request: dict[str, Any], universe_dir: Path):
    from tinyassets.storage.outbound_connections import (
        ProxyRequestError,
        _broker_channel,
    )

    try:
        channel = _broker_channel(
            universe_dir.parent, principal=_require(request, "principal"),
            command_center=universe_dir.name, grant_id=_require(request, "grant_id"),
            connection_id=_require(request, "connection_id"),
        )
    except ProxyRequestError as exc:
        raise _Refused("auth", f"the credential broker is unavailable: {exc}") from None
    if channel is None:
        raise _Refused("auth", "authenticated git requires the credential broker")
    return channel._client


def _cell_request(request: dict[str, Any], op: str, options: list[str]) -> dict[str, Any]:
    """The bounded request the cell receives. Never a token, path or principal."""
    document: dict[str, Any] = {
        "op": op, "options": options,
        "timeout_s": float(request.get("timeout_s") or _DEFAULT_TIMEOUT_S),
    }
    if op in ROUTED_OPS:
        document["host"] = _require(request, "host")
        document["repo"] = _require(request, "owner_repo")
    if op in ("checkout", "create"):
        document["storage"] = request.get("storage")
        document["lease_parent"] = list(request.get("lease_parent") or ())
        document["lease_name"] = _require(request, "lease_name")
    if op == "checkout":
        document["ref"] = _require(request, "ref")
        document["checkout_ref"] = _require(request, "checkout_ref")
    if op == "push":
        document["lease_parent"] = list(request.get("lease_parent") or ())
        document["lease_name"] = _require(request, "lease_name")
        document["remote_ref"] = _require(request, "remote_ref")
        document["commit_sha"] = _require(request, "commit_sha")
        document["reconcile_only"] = bool(request.get("reconcile_only"))
        document["max_bundle_bytes"] = int(request.get("max_bundle_bytes") or MAX_BUNDLE_BYTES)
    if op == "ls_remote":
        document["remote_ref"] = str(request.get("remote_ref") or "")
    return document


#: Every key an answer may carry, with the type the callers read it as. An
#: answer the cell did not shape -- or one shaped by something else -- is
#: refused rather than passed into the effector's evidence.
_ANSWER_TYPES = {
    "ok": bool, "resolved_sha": str, "bytes": int, "ref_name": str, "lease": str,
    "content": str, "remote_ref": str, "head_ref": str, "observed_sha": str,
    "reconciled": bool, "error": str, "stderr_class": str, "reconcile_error": str,
}


def _validated(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict) or type(answer.get("ok")) is not bool:
        raise _Refused("other", "the workspace cell returned no answer")
    for key, value in answer.items():
        wanted = _ANSWER_TYPES.get(key)
        if wanted is None or type(value) is not wanted:
            raise _Refused("other", f"the workspace cell answer carries an unknown {key!r}")
    if answer["ok"]:
        return answer
    if not answer.get("error") or not answer.get("stderr_class"):
        raise _Refused("other", "a refused workspace answer must name its class")
    return answer


def _routed(request: dict[str, Any], op: str, universe_dir: Path) -> dict[str, Any]:
    """Open the route, run the cell inside it, and close the route after."""
    from tinyassets import role_remote_git
    from tinyassets.git_egress import rewrite_options

    agent = str(request.get("agent") or "")
    if not _AGENT_RE.match(agent):
        raise _Refused("bad_argument", "request.agent must name the acting run")
    grant, view, incarnation = _authority(request, op)
    proxy, socket_path = _proxy(universe_dir)
    client = _broker_client(request, universe_dir)
    with rewrite_options(proxy, client, [(grant, view, incarnation)], agent) as rewrites:
        if not rewrites:
            raise _Refused("auth", "no git route was opened for this grant")
        options = ["-c", f"http.proxy={_CELL_PROXY}", *rewrites]
        return _validated(role_remote_git.run(
            _cell_request(request, op, options),
            universe_dir=universe_dir, principal=_require(request, "principal"),
            egress_socket=socket_path,
        ))


def execute_workspace_operation(
    request: dict[str, Any],
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    startup_timeout_s: float = 30.0,
    spawn: Any = None,
) -> dict[str, Any]:
    """Perform one workspace operation and return a secret-free answer.

    Never raises: a refusal is an answer with a fixed ``stderr_class``, which
    is the contract the effector and the push reconciler already carry.
    ``spawn`` stays injectable for the callers' own tests.
    """
    del timeout_s, startup_timeout_s
    if spawn is not None:
        return spawn(request)
    try:
        if not isinstance(request, dict):
            raise _Refused("bad_argument", "request must be a mapping")
        op = str(request.get("op") or "").strip()
        if op not in WORKSPACE_OPS:
            raise _Refused("bad_argument", f"unknown workspace op: {op or '(none)'}")
        universe_dir = Path(_require(request, "universe_dir"))
        if op in ROUTED_OPS:
            return _routed(request, op, universe_dir)
        from tinyassets import role_remote_git

        # An empty workspace reaches nothing: no grant, no route, no socket.
        return _validated(role_remote_git.run(
            _cell_request(request, op, []),
            universe_dir=universe_dir, principal=_require(request, "principal"),
            egress_socket=None,
        ))
    except _Refused as refused:
        return {"ok": False, "error": refused.error, "stderr_class": refused.code}
    except Exception as exc:  # noqa: BLE001 - loud, but never leaky
        from tinyassets.workspace_git import scrub_text

        return {"ok": False, "stderr_class": "other",
                "error": scrub_text(f"the workspace cell refused: {type(exc).__name__}: {exc}")}


def reconcile_push_intents(base_path: Any, **kwargs: Any) -> list[tuple[str, str]]:
    """Settle every push whose outcome was lost. Exposed HERE because this is
    where the caller already is: ``runs.py``'s startup reconciliation calls
    ``workspace_worker.reconcile_push_intents(base_path)`` and this module is
    what asks the remote."""
    from tinyassets.workspace_intents import reconcile_push_intents as _reconcile

    kwargs.setdefault("execute", execute_workspace_operation)
    return _reconcile(base_path, **kwargs)

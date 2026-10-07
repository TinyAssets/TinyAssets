"""Owner self-serve source-channel approval + policy.

Host directive 2026-08-18: "what does and does not need host approval should be
user changeable", and the shape is GENERAL — ``approve_source_channel``, where a
*source channel* is ANY source/sink a user binds to a graph node. GitHub is one
channel type, not a special case.

Today a ``source_code`` node is gated fail-closed by
``graph_compiler._validate_source_code`` (``approved=True`` + matching
``approved_source_hash``). The only writer of that approval is
``extensions action=approve_source_code``, which needs the host-only
``tinyassets.extensions.admin`` scope and is not one of the advertised connector
handles — so a universe OWNER cannot approve their own graph's node and a run
fails ``node_not_approved``. This module is the owner self-serve path, reached
through ``write_graph target=source_channel`` (no new advertised handle).

Authorization (fail-closed, owner-scoped):

- The caller must be the authenticated ``admin``-ACL OWNER of the named
  universe. ``founder_grant.py`` names the ``admin`` ACL row as the canonical
  answer to "does this subject own THIS universe?"; a ``write``-ACL collaborator
  is NOT an owner. An unbound, non-owner, ``write`` collaborator, or a caller
  naming a universe they do not own all get ``auth_failed``.
- A code-channel approval additionally requires the caller to be the branch
  ``author`` and the branch to be PRIVATE. Public/commons branches run for other
  principals, so self-approving them would affect the commons — those stay on
  the host operator surface.

This module ADDS the owner path; it does not weaken the host operator surface.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

CHANNEL_SOURCE_CODE = "source_code"


def person_only_sinks() -> frozenset[str]:
    """Consent sinks the OWNER answers on the request rail, never self-approved.

    This verb writes into the very ``effector_consents`` store those rails feed,
    so a sink whose whole point is a person's tap must be refused here or the
    rail is a formality the agent can walk around -- it shares the owner's
    principal, so no authorization check downstream can tell the two apart.

    ``workspace`` was the first (Codex refute review of PR #2742, Q1);
    ``patch_intake`` is the second, and it was missed in exactly the same way --
    the refusal named one sink instead of the class it belongs to, so the next
    rail-answered sink inherited the hole. A SET, checked in the one function
    that writes the grant, is what stops a third.
    """
    from tinyassets.effectors.workspace import EXTERNAL_WRITE_SINK_WORKSPACE
    from tinyassets.patch_intake import PATCH_INTAKE_SINK

    return frozenset({EXTERNAL_WRITE_SINK_WORKSPACE, PATCH_INTAKE_SINK})


def _auth_failed(detail: str, **extra: Any) -> str:
    payload = {
        "error": "auth_failed",
        "failure_class": "auth_failed",
        "detail": detail,
        "actionable_by": "user",
    }
    payload.update(extra)
    return json.dumps(payload)


def _authenticated_actor() -> str | None:
    """Return the credential-validated request subject, or None if unbound.

    Never an environment fallback: a ``UNIVERSE_SERVER_USER`` env value must not
    confer approval authority over a universe.
    """
    from tinyassets.api.permissions import current_request_actor_id
    from tinyassets.principals import named_principal

    actor = named_principal(current_request_actor_id())
    if not actor:
        return None
    return actor


def universe_owner_actor(base_path: Any, universe_id: str, actor: str) -> bool:
    """True iff ``actor`` holds the ``admin`` ACL grant on ``universe_id``.

    The canonical per-universe ownership signal (``founder_grant.py``). Excludes
    ``read``/``write`` ACL holders and wrong-universe callers (admin on A is not
    admin on B). Fail-closed on any read error.
    """
    from tinyassets.daemon_server import universe_access_permission

    uid = (universe_id or "").strip()
    actor = (actor or "").strip()
    if not uid or not actor:
        return False
    try:
        permission = universe_access_permission(
            base_path, universe_id=uid, actor_id=actor
        )
    except Exception:  # noqa: BLE001 — fail closed on any storage error
        return False
    return permission == "admin"


def _payload_dict(payload: Any) -> dict[str, Any] | None:
    if payload is None or payload == "":
        return {}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def source_channel(
    *,
    action: str,
    universe_id: str = "",
    branch_id: str = "",
    payload: Any = None,
) -> str:
    """Dispatch an owner source-channel operation.

    ``action`` ∈ {approve, revoke} -- the consent row, which is the only
    per-channel setting enforcement reads. ``universe_id`` is the owner's
    universe (``graph_id`` from the connector). ``payload`` carries
    ``channel_type``/``node_id``/``reason``/``sink``/``destination``.

    ``set_policy``/``get_policy`` were REMOVED (concern
    `2026-09-24-source-channel-policy-store-has-no-reader`): they wrote and read
    an approval mode no gate consulted, so the owner configured nothing and was
    told `policy_set`. Since change `sandboxed-code-node` an approval gates no
    run, and ADR-013 settles the direction -- authorship, not host approval,
    decides whose code runs. An unknown operation now says so.
    """
    from tinyassets.api.helpers import _base_path, _request_universe

    normalized = (action or "").strip().lower()
    fields = _payload_dict(payload)
    if fields is None:
        return json.dumps({
            "error": "payload_json must be a JSON object",
            "failure_class": "invalid_payload",
            "actionable_by": "chatbot",
        })

    actor = _authenticated_actor()
    if actor is None:
        return _auth_failed("authentication required")

    base = _base_path()
    uid = _request_universe(universe_id)
    if not uid:
        return json.dumps({
            "error": "universe_id is required",
            "failure_class": "missing_universe",
            "actionable_by": "chatbot",
        })

    # Single owner gate for every operation: only the admin-ACL owner of THIS
    # universe may grant or take back its channels.
    if not universe_owner_actor(base, uid, actor):
        return _auth_failed(
            "only the command center owner may approve or configure its source "
            "channels",
            universe_id=uid,
        )

    if normalized == "approve":
        return _approve(base, uid, actor, branch_id, fields)
    if normalized == "revoke":
        return _revoke_sink(uid, fields)
    return json.dumps({
        "error": "unknown_source_channel_operation",
        "operation": action,
        "allowed_operations": ["approve", "revoke"],
        "actionable_by": "chatbot",
    })


#: Universe ids permitted to APPROVE source_code for in-process execution.
#:
#: Approving source is not an ordinary universe operation -- it is the authority to
#: run arbitrary Python inside the daemon. `graph_compiler` executes an approved node
#: with `exec()` and full builtins; the pattern denylist blocks a handful of substrings
#: and leaves `open`, `os.environ`, sockets and ordinary imports available. So an
#: approver can read every credential the process can read: the live Stripe key, the
#: webhook secret, the session-store digest key, every per-universe credential vault,
#: and every other user's refresh token. It can also write any database under the data
#: dir, including the one that decides who has paid.
#:
#: The owner gate below is per-universe, and EVERY user owns their own universe, so on
#: its own it authorises every user to do all of that (Codex, 2026-08-28, ranked the
#: top second-user blocker). Until user code runs in a real OS sandbox, the capability
#: is limited to an explicit allowlist.
#:
#: Empty = DARK, deliberately: a deployment that has not thought about this must not
#: hand out in-process execution. This source-approval control is separate from
#: current serving-owner engine-tool admission; changing one does not relax the other.
_SOURCE_APPROVAL_VAR = "TINYASSETS_SOURCE_APPROVAL_UNIVERSES"


def source_approval_allowlist() -> frozenset[str]:
    """Universe ids allowed to approve source_code. Empty = nobody."""
    import os as _os

    raw = _os.environ.get(_SOURCE_APPROVAL_VAR, "")
    return frozenset(u.strip() for u in raw.split(",") if u.strip())


def source_approval_allowed(universe_id: str) -> bool:
    return bool(universe_id) and universe_id in source_approval_allowlist()


def source_approval_refusal(universe_id: str) -> dict:
    """The refusal body. Names the capability, so it does not read as a glitch."""
    return {
        "status": "rejected",
        "error": (
            "approving source_code runs arbitrary Python inside the daemon, and "
            "this deployment has not allowlisted this command center for that. It is "
            "off by default because an approver can read every credential the "
            "process holds, including other users'."
        ),
        "failure_class": "source_approval_not_allowlisted",
        "actionable_by": "host",
        "universe_id": universe_id,
        "remediation": (
            f"Set {_SOURCE_APPROVAL_VAR} to a comma-separated list of command center ids "
            "that may approve source. Until user code runs in an OS sandbox, keep "
            "it to vetted founders only."
        ),
    }


def _approve(
    base: Any,
    uid: str,
    actor: str,
    branch_id: str,
    fields: dict[str, Any],
) -> str:
    channel_type = (fields.get("channel_type") or "").strip()
    if not channel_type:
        return json.dumps({
            "error": "channel_type is required",
            "failure_class": "missing_channel_type",
            "actionable_by": "chatbot",
        })
    if channel_type == CHANNEL_SOURCE_CODE:
        return _approve_source_code(base, uid, actor, branch_id, fields)
    return _approve_sink(base, uid, actor, channel_type, fields)


def _approve_source_code(
    base: Any,
    uid: str,
    actor: str,
    branch_id: str,
    fields: dict[str, Any],
) -> str:
    from tinyassets.api.branches import (
        _resolve_readable_branch,
        _source_code_hash,
    )
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import save_branch_definition

    selector = (branch_id or fields.get("branch_def_id") or "").strip()
    node_id = (fields.get("node_id") or "").strip()
    reason = (fields.get("reason") or "").strip()
    if not selector or not node_id:
        return json.dumps({
            "error": "branch_id and node_id are required for a source_code "
            "channel",
            "failure_class": "missing_target",
            "actionable_by": "chatbot",
        })

    resolved = _resolve_readable_branch(selector, str(base))
    if resolved is None:
        return json.dumps({
            "error": f"Branch '{selector}' not found.",
            "failure_class": "branch_not_found",
            "actionable_by": "chatbot",
        })
    bid, source = resolved

    # You may only approve a branch you authored — a universe owner cannot
    # approve a branch authored by another principal.
    if (source.get("author") or "").strip() != actor:
        return _auth_failed(
            "only the branch author may approve its source_code channel",
            branch_def_id=bid,
        )

    # Commons safety (fail-closed allowlist): only a PRIVATE branch may be
    # self-approved. A public/commons/unlisted branch runs for other principals
    # too, so owner self-approval must not touch it — those stay on the host
    # operator surface. Allowlisting ``== "private"`` (not blocklisting
    # ``"public"``) means any non-private visibility value is refused.
    visibility = (source.get("visibility") or "public").strip().lower() or "public"
    if visibility != "private":
        return _auth_failed(
            "only a PRIVATE branch may be self-approved by its owner; "
            "public/commons branches require host operator approval",
            branch_def_id=bid,
            visibility=visibility,
        )

    staging = BranchDefinition.from_dict(source)
    target_node = next(
        (n for n in staging.node_defs if n.node_id == node_id), None
    )
    if target_node is None:
        return json.dumps({
            "status": "rejected",
            "error": f"Node '{node_id}' not found on branch '{bid}'.",
            "failure_class": "node_not_found",
            "actionable_by": "chatbot",
        })
    if not target_node.source_code:
        return json.dumps({
            "status": "rejected",
            "error": f"Node '{node_id}' has no source_code to approve.",
            "failure_class": "no_source_code",
            "actionable_by": "chatbot",
        })

    # Checked HERE, at the write that grants execution, not at the route edge:
    # this is the line that turns text into something the daemon will run.
    if not source_approval_allowed(uid):
        return json.dumps(source_approval_refusal(uid))

    source_hash = _source_code_hash(target_node.source_code)
    target_node.approved = True
    target_node.approved_by = actor
    target_node.approved_at = datetime.now(timezone.utc).isoformat()
    target_node.approved_source_hash = source_hash
    target_node.approval_reason = reason

    saved = save_branch_definition(base, branch_def=staging.to_dict())
    persisted = BranchDefinition.from_dict(saved)
    approved_node = next(
        (n for n in persisted.node_defs if n.node_id == node_id), target_node
    )
    return json.dumps({
        "status": "approved",
        "channel_type": CHANNEL_SOURCE_CODE,
        "universe_id": uid,
        "branch_def_id": bid,
        "node_id": node_id,
        "approved": approved_node.approved,
        "approved_by": approved_node.approved_by,
        "approved_at": approved_node.approved_at,
        "approved_source_hash": approved_node.approved_source_hash,
        "approval_reason": approved_node.approval_reason,
    }, default=str)


def _approve_sink(
    base: Any,
    uid: str,
    actor: str,
    channel_type: str,
    fields: dict[str, Any],
) -> str:
    """Approve a sink/effector channel via the shared effector-consent store.

    ``channel_type`` is the sink name (e.g. ``authenticated_external_call``), or the
    caller may pass ``sink`` explicitly. ``granted_by`` is the authenticated
    owner — stronger than the legacy ``grant_effector_consent`` which derived it
    from the ambient ``UNIVERSE_SERVER_USER`` env.
    """
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.storage.effector_consents import grant_consent

    sink = (fields.get("sink") or channel_type or "").strip()
    destination = (fields.get("destination") or "").strip()
    if not sink:
        return json.dumps({
            "error": "sink (or channel_type) is required for a sink channel",
            "failure_class": "missing_sink",
            "actionable_by": "chatbot",
        })
    if not destination:
        return json.dumps({
            "error": "destination is required for a sink channel",
            "failure_class": "missing_destination",
            "actionable_by": "chatbot",
        })
    # THE authority check, in the one function that writes the grant. The served
    # handle refuses these earlier with a friendlier message, but a refusal that
    # lives only at one entry point is a refusal with a way around it.
    if sink in person_only_sinks():
        return json.dumps({
            "error": "consent_is_person_only",
            "failure_class": "consent_is_person_only",
            "actionable_by": "user",
            "detail": (
                ('The owner approves patch_intake in their app. Once approved, use '
                 'write_graph target="patch_request" operation="send".')
                if sink == "patch_intake" else
                f'"{sink}" consent is answered by the command center\'s owner on the '
                "request rail, not granted here. Ask for it there."
            ),
        })
    try:
        universe_dir = _universe_dir(uid)
    except ValueError:
        return json.dumps({
            "error": f"Invalid universe_id: {uid}",
            "failure_class": "invalid_universe",
            "actionable_by": "chatbot",
        })
    record = grant_consent(
        universe_dir,
        sink=sink,
        destination=destination,
        granted_by=actor,
    )
    return json.dumps({
        "status": "granted",
        "channel_type": sink,
        "universe_id": uid,
        "consent": record,
    })


def _revoke_sink(uid: str, fields: dict[str, Any]) -> str:
    """Take a sink consent back. Narrowing only, so any sink may be revoked --
    including ``workspace``, which the served agent cannot grant itself.

    The reply's ``active`` is read back from the store the effectors consult
    (``is_consent_active``), not inferred from the write.
    """
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.storage.effector_consents import is_consent_active, revoke_consent

    sink = (fields.get("sink") or fields.get("channel_type") or "").strip()
    destination = (fields.get("destination") or "").strip()
    if sink == CHANNEL_SOURCE_CODE:
        return json.dumps({
            "error": (
                "source_code is not a consent: a code node runs in the OS sandbox "
                "of the command center that authored it, so there is nothing to revoke"
            ),
            "failure_class": "not_a_consent",
            "actionable_by": "chatbot",
        })
    if not sink or not destination:
        return json.dumps({
            "error": "channel_type (or sink) and destination are required",
            "failure_class": "missing_target",
            "actionable_by": "chatbot",
        })
    try:
        universe_dir = _universe_dir(uid)
    except ValueError:
        return json.dumps({
            "error": f"Invalid universe_id: {uid}",
            "failure_class": "invalid_universe",
            "actionable_by": "chatbot",
        })
    was_active = is_consent_active(universe_dir, sink=sink, destination=destination)
    if was_active:
        revoke_consent(universe_dir, sink=sink, destination=destination)
    active = is_consent_active(universe_dir, sink=sink, destination=destination)
    if active:  # fail loudly: the store still says it is granted
        return json.dumps({
            "error": "revoke did not take effect; the consent is still active",
            "failure_class": "revoke_failed",
            "actionable_by": "host",
            "channel_type": sink,
            "destination": destination,
            "active": True,
        })
    return json.dumps({
        "status": "revoked" if was_active else "not_held",
        "channel_type": sink,
        "destination": destination,
        "universe_id": uid,
        "active": active,
    })


__all__ = [
    "CHANNEL_SOURCE_CODE",
    "source_channel",
    "universe_owner_actor",
]

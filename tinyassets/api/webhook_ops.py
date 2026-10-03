"""User-facing mint/revoke/list of inbound webhook URLs (channel-agnostic inbound).

These are WRITE actions on the run surface: a universe's founder mints a stable
`https://<domain>/hooks/<token>` URL for one of THEIR OWN branches, revokes it, or lists
their hooks. Ownership is enforced two ways: the run surface already gates write actions
to a universe the caller can write (per-universe), and mint additionally resolves the
branch within that universe's graph — so a token can only ever be minted for a branch the
caller's own universe owns.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def _public_base_url() -> str:
    """The public HTTPS ORIGIN webhooks are posted to. Canonical is tinyassets.io.

    Validated to scheme+host only (https, a host, no path/query/fragment/userinfo)
    so a misconfigured ``TINYASSETS_PUBLIC_BASE_URL`` can never mis-mint the webhook
    URL — e.g. an embedded path yielding ``…/mcp/mcp/hooks/<token>`` or a query that
    places the SECRET token in a query string. Anything not a bare https origin
    falls back to the canonical origin (Codex inbound review).
    """
    from urllib.parse import urlsplit

    default = "https://tinyassets.io"
    raw = (os.environ.get("TINYASSETS_PUBLIC_BASE_URL") or default).rstrip("/")
    # A canonical origin carries no whitespace/control chars — reject outright
    # (Codex: whitespace was accepted before) so it can't smuggle a header/URL.
    if any(c.isspace() for c in raw):
        logger.warning("TINYASSETS_PUBLIC_BASE_URL has whitespace; using %s", default)
        return default
    try:
        parts = urlsplit(raw)
        host = parts.hostname  # malformed host (e.g. "https://[bad") raises ValueError
        _ = parts.port         # a non-numeric port raises ValueError
    except ValueError:
        logger.warning("TINYASSETS_PUBLIC_BASE_URL is malformed; using %s", default)
        return default
    if (
        parts.scheme == "https"
        and host
        and not parts.path
        and not parts.query
        and not parts.fragment
        and "@" not in parts.netloc
    ):
        return raw
    logger.warning(
        "TINYASSETS_PUBLIC_BASE_URL is not a bare https origin; using %s", default
    )
    return default


def _uid(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.runs import _request_universe

    return _request_universe(kwargs.get("universe_id") or "")


def _resolve_owned_branch(base: str, branch_def_id: str, uid: str) -> tuple[str, str | None]:
    """Resolve ``branch_def_id`` and verify the CALLER authored it (Codex #1).

    Returns ``(bid, error_json_or_None)``. The author-gate is the real ownership check:
    verifying the branch merely EXISTS is not enough — an attacker with write to their own
    universe could otherwise mint a token for a victim's public branch. Ownership holds when
    the branch's author is the authenticated caller, or the caller's own universe actor
    (agent-authored branches). Indistinct error so a non-owner learns nothing.
    """
    from tinyassets.api.branches import _resolve_branch_id
    from tinyassets.api.permissions import current_request_actor_id
    from tinyassets.daemon_server import get_branch_definition

    not_found = json.dumps({"error": f"branch not found in your command center: {branch_def_id}"})
    bid = _resolve_branch_id(branch_def_id, base)
    try:
        branch = get_branch_definition(base, branch_def_id=bid)
    except Exception:  # noqa: BLE001
        return bid, not_found
    author = (branch.get("author") or "").strip()
    caller = (current_request_actor_id() or "").strip()
    owners = {caller, f"universe:{uid}"} - {""}
    if author not in owners:
        return bid, not_found
    return bid, None


def _owner_principal() -> str:
    """The authenticated caller a hook will run AS. Every hook has one
    (authenticated-owner boundary D2); a request with none cannot mint."""
    from tinyassets.api.permissions import current_request_actor_id

    return (current_request_actor_id() or "").strip()


def _action_mint_webhook(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.helpers import _base_path
    from tinyassets.storage import webhook_hooks

    uid = _uid(kwargs)
    if not uid:
        return json.dumps({
            "error": "mint_webhook requires a universe_id (your own command center).",
        })
    branch_def_id = str(kwargs.get("branch_def_id", "")).strip()
    if not branch_def_id:
        return json.dumps({"error": "branch_def_id is required."})

    base = _base_path()
    bid, err = _resolve_owned_branch(base, branch_def_id, uid)
    if err is not None:
        return err

    owner = _owner_principal()
    if not owner:
        return json.dumps({"error": "authentication_required"})
    token = webhook_hooks.mint(
        base, universe_id=uid, branch_def_id=bid, owner_principal_id=owner
    )
    url = f"{_public_base_url()}/mcp/hooks/{token}"
    return json.dumps({
        "text": (
            "Inbound webhook URL created. Paste it into the channel's webhook settings "
            f"(GitHub, Stripe, or any tool that can POST a webhook):\n{url}\n"
            "Each POST to it runs this branch as your command center."
        ),
        "url": url,
        "token": token,
        "branch_def_id": bid,
    })


def _action_revoke_webhook(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.helpers import _base_path
    from tinyassets.storage import webhook_hooks

    uid = _uid(kwargs)
    token = str(kwargs.get("token", "")).strip()
    token_prefix = str(kwargs.get("token_prefix", "") or "").strip()
    if uid and not token and token_prefix:
        # The list shows only the prefix; the raw token was shown once at mint.
        # Scoped to THIS universe's plain hooks and to exactly one match.
        revoked = webhook_hooks.revoke_by_prefix(
            _base_path(), universe_id=uid, token_prefix=token_prefix,
        )
        return json.dumps({
            "text": "Webhook revoked." if revoked else "No matching webhook to revoke.",
            "revoked": bool(revoked),
        })
    if not uid or not token:
        return json.dumps({
            "error": "revoke_webhook requires a universe_id and a token or token_prefix.",
        })
    base = _base_path()
    # Only revoke a token that belongs to THIS universe (never another's).
    binding = webhook_hooks.resolve(base, token=token)
    if binding is None or str(binding.get("universe_id")) != uid:
        # Indistinct: do not reveal whether the token exists for another universe.
        return json.dumps({"text": "No matching webhook to revoke.", "revoked": False})
    revoked = webhook_hooks.revoke(base, token=token)
    return json.dumps({"text": "Webhook revoked." if revoked else "Already revoked.",
                       "revoked": bool(revoked)})


def _action_list_webhooks(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.helpers import _base_path
    from tinyassets.storage import webhook_hooks

    uid = _uid(kwargs)
    if not uid:
        return json.dumps({
            "error": "list_webhooks requires a universe_id (your own command center).",
        })
    base = _base_path()
    rows = webhook_hooks.list_for_universe(base, universe_id=uid)
    active = [r for r in rows if r.get("revoked_at") is None]
    # The raw token is never stored, so the full URL cannot be reconstructed (Codex #6);
    # show the non-secret prefix for identification. The full URL is shown once at mint.
    hooks = [{
        "branch_def_id": r["branch_def_id"],
        "token_prefix": r.get("token_prefix", ""),
        "created_at": r["created_at"],
    } for r in active if not r.get("source_id")]
    return json.dumps({
        "text": (
            f"You have {len(hooks)} active inbound webhook(s). The full URL is shown only "
            "once at creation; revoke and re-mint if you need a new one."
        ),
        "webhooks": hooks,
        "count": len(hooks),
    })


# ── Source nodes (Floor 2/3): a live inbound source = a hook + an event-trigger ──────
#
# A Source is a user-composed graph object that turns "a channel that emits events" into
# a first-class thing: creating one MINTS a webhook token (bound to universe+branch, with a
# source_id) AND REGISTERS a `source:<id>` event-trigger subscription. An inbound POST then
# publishes to the event bus, which fires the bound branch as the owning universe with
# at-most-once dedupe. Ownership is enforced by the same universe-write dispatch gate that
# guards mint_webhook (see `_WEBHOOK_OWNER_ACTIONS`), so a Source can only ever be created,
# listed, or revoked for the caller's OWN universe.


def _event_type_for(source_id: str) -> str:
    return f"source:{source_id}"


def _action_create_source(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.helpers import _base_path
    from tinyassets.scheduler import register_subscription
    from tinyassets.storage import webhook_hooks

    uid = _uid(kwargs)
    if not uid:
        return json.dumps({
            "error": "create_source requires a universe_id (your own command center).",
        })
    branch_def_id = str(kwargs.get("branch_def_id", "")).strip()
    if not branch_def_id:
        return json.dumps({"error": "branch_def_id is required."})

    base = _base_path()
    bid, err = _resolve_owned_branch(base, branch_def_id, uid)
    if err is not None:
        return err

    source_id = uuid.uuid4().hex
    # Register the event-trigger FIRST so a delivery can never arrive before a subscriber
    # exists (an event with no subscription is silently dropped). Owner is the universe, so
    # the fired run's actor is `universe:<uid>` — the correct branch-run identity.
    try:
        register_subscription(
            base,
            branch_def_id=bid,
            owner_actor=f"universe:{uid}",
            event_type=_event_type_for(source_id),
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    owner = _owner_principal()
    if not owner:
        return json.dumps({"error": "authentication_required"})
    token = webhook_hooks.mint(
        base, universe_id=uid, branch_def_id=bid, source_id=source_id,
        owner_principal_id=owner,
    )
    url = f"{_public_base_url()}/mcp/hooks/{token}"
    return json.dumps({
        "text": (
            "Inbound source created. Paste this URL into the channel's webhook settings; "
            f"each delivery fires this branch (deduped per delivery):\n{url}"
        ),
        "url": url,
        "token": token,
        "source_id": source_id,
        "branch_def_id": bid,
    })


def _action_list_sources(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.helpers import _base_path
    from tinyassets.storage import webhook_hooks

    uid = _uid(kwargs)
    if not uid:
        return json.dumps({
            "error": "list_sources requires a universe_id (your own command center).",
        })
    base = _base_path()
    rows = webhook_hooks.list_for_universe(base, universe_id=uid)
    sources = [{
        "source_id": r["source_id"],
        "branch_def_id": r["branch_def_id"],
        "token_prefix": r.get("token_prefix", ""),
        "created_at": r["created_at"],
    } for r in rows if r.get("source_id") and r.get("revoked_at") is None]
    return json.dumps({
        "text": f"You have {len(sources)} active inbound source(s).",
        "sources": sources,
        "count": len(sources),
    })


def _action_revoke_source(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.helpers import _base_path
    from tinyassets.scheduler import list_scheduler_subscriptions, unregister_subscription
    from tinyassets.storage import webhook_hooks

    uid = _uid(kwargs)
    source_id = str(kwargs.get("source_id", "")).strip()
    if not uid or not source_id:
        return json.dumps({"error": "revoke_source requires a universe_id and a source_id."})
    base = _base_path()
    # Revoke the source's hook scoped to (universe, source_id) — never by raw token, which
    # we no longer store (Codex #6), and never another universe's source. Indistinct no-op
    # when there is no matching active source for THIS universe.
    revoked = webhook_hooks.revoke_source(base, universe_id=uid, source_id=source_id)
    if not revoked:
        return json.dumps({"text": "No matching source to revoke.", "revoked": False})

    owner = f"universe:{uid}"
    for sub in list_scheduler_subscriptions(
        base, owner_actor=owner, event_type=_event_type_for(source_id)
    ):
        try:
            unregister_subscription(
                base, sub["subscription_id"], requesting_actor=owner,
            )
        except Exception:  # noqa: BLE001 - hook is already revoked; best-effort trigger teardown
            logger.exception("revoke_source: failed to deactivate subscription for %s", source_id)
    return json.dumps({"text": "Source revoked.", "revoked": True, "source_id": source_id})

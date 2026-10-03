"""Universal inbound webhook receiver (channel-agnostic inbound, Floor 1).

A single public endpoint — ``POST /hooks/<token>`` — lets ANY channel that can fire an
HTTP webhook trigger a branch, with zero platform code per channel. The token is the
only authority: it was minted by a universe's founder for one of that universe's own
branches (which the founder authored), so an inbound POST can only ever run THAT branch
as THAT universe, using that universe's own credentials. Nothing in the request selects
identity.

Security posture:
- DARK unless ``TINYASSETS_INBOUND_ENABLED`` is truthy: the flag gates the ENTIRE inbound
  execution path (the route is not mounted, and even a direct call refuses) — dark means
  no run can be triggered, not merely un-tunneled (Codex #2).
- Caller-facing response is UNIFORM across all non-deliverable states — unknown, revoked,
  malformed, disabled, un-runnable — all return 404 with an identical body (Codex #7); the
  real reason is logged LOUDLY internally. Deliverable → 202; 429 only for the per-token
  flood limit. There is no saturation refusal — a busy universe queues.
- Replay is deduped SERVER-side on (token, exact body), never a caller header (Codex #4);
  a replay never consumes rate budget (Codex #5) and fires at most once.
- Admission is durable and per-token (Codex #3): anti-flood on an unauthenticated public
  ingress, aimed at a stranger with the URL rather than at the account holder. A
  deliverable request is always accepted and its run queues for an agent seat.
- The run's actor is ``universe:<uid>`` from the binding, fail-closed (Codex #1); no
  header/body redirects it. Only an ALLOWLIST of safe headers reaches branch state; no
  credential header and no raw token is ever forwarded or stored (Codex #6).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

#: Refuse anything larger unread — a webhook payload is a small JSON body, not a megabyte.
MAX_BODY_BYTES = 256 * 1024

#: Per-TOKEN anti-flood on an unauthenticated public ingress. This is the one load
#: limit left here, and it is not an account limit: ``POST /hooks/<token>`` is
#: reachable by anyone who has the URL, so the traffic it bounds comes from a
#: stranger rather than from the account holder.
#:
#: What went with the other non-usage limits (founder, 2026-09-30 — an account's
#: only limits are cloud bytes and concurrent agent seats):
#:   * ``_UNIVERSE_RATE_MAX = 6000``, a per-universe aggregate over the same
#:     window. It metered the OWNER's own hooks against each other.
#:   * ``_MAX_INFLIGHT_PER_UNIVERSE = 20``, which answered 503 and DROPPED the
#:     delivery past 20 concurrent inbound runs. An inbound-triggered run now
#:     queues for a seat like any other run; over the seat count work waits, and
#:     a webhook the sending channel will not retry must never be thrown away
#:     because the universe was busy.
_RATE_MAX = 600
_RATE_WINDOW_S = 60.0

#: Replay dedupe window: a delivery repeated within this window fires the branch at most once.
_DEDUPE_WINDOW_S = 600.0

#: TTL for an ABANDONED in-flight reservation (reserved but never linked to a run because
#: enqueue failed / the process died). Linked reservations are released on run termination.
_RESERVATION_TTL_S = 120.0

#: The ONLY request headers forwarded into branch state (Codex #6). An allowlist, not a
#: denylist: a proxy-injected Access/OIDC assertion or an API key must never reach durable
#: run input. These are channel content-type + verification headers a branch legitimately
#: needs (e.g. to verify a GitHub/Stripe/GitLab signature over the raw body).
_HEADER_ALLOWLIST = frozenset({
    "content-type",
    "user-agent",
    "x-github-event",
    "x-github-hook-id",
    "x-github-delivery",
    "x-hub-signature",
    "x-hub-signature-256",
    "x-gitlab-event",
    "x-gitlab-instance",
    "x-event-key",
    "x-stripe-signature",
    "stripe-signature",
    "x-slack-signature",
    "x-slack-request-timestamp",
    "x-shopify-topic",
    "x-shopify-hmac-sha256",
})

#: The uniform "not deliverable" response. Unknown / revoked / malformed / disabled /
#: un-runnable all answer identically so a caller learns nothing about token existence or
#: usability (Codex #7). The real reason is logged internally at the decision point.
_NOT_DELIVERABLE: tuple[int, dict[str, Any]] = (404, {"error": "not_found"})


def inbound_enabled() -> bool:
    """Whether the inbound webhook execution path is enabled (Codex #2). DARK by default."""
    return os.environ.get("TINYASSETS_INBOUND_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _delivery_key(token: str, body: bytes) -> str:
    """Server-side idempotency key for one delivery — a hash of (token, exact body). NEVER
    derived from a caller header, so an attacker cannot alter it to force a re-run (Codex #4)."""
    return "sha256:" + hashlib.sha256(token.encode("utf-8") + b"\x00" + body).hexdigest()


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Forward ONLY allowlisted headers; drop everything else (Codex #6)."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if str(k).lower() in _HEADER_ALLOWLIST:
            out[str(k)] = str(v)
    return out


def _decode_body(body: bytes) -> Any:
    """The payload as parsed JSON when it is JSON, else the raw text (bounded)."""
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - a non-JSON webhook body is fine; pass the text
        return text


def handle_hook(
    *,
    token: str,
    body: bytes,
    headers: Mapping[str, str],
    base_path: str | Path | None = None,
    enqueue: Callable[..., str] | None = None,
    emit: Callable[..., None] | None = None,
    now: float | None = None,
) -> tuple[int, dict[str, Any]]:
    """Authenticate a webhook by its token and trigger the bound branch. Returns
    ``(status, payload)``. Transport-agnostic so the security decisions are testable
    without a server. Every internal error is caught and normalized to the uniform 404
    (Codex #7): a valid token hitting a DB fault must not answer differently from an
    unknown one."""
    try:
        return _handle_hook_inner(
            token=token, body=body, headers=headers, base_path=base_path,
            enqueue=enqueue, emit=emit, now=now,
        )
    except Exception:  # noqa: BLE001 - uniform response even on an unexpected internal fault
        logger.exception("webhook: unhandled internal error; answering uniform 404")
        return _NOT_DELIVERABLE


def _handle_hook_inner(
    *,
    token: str,
    body: bytes,
    headers: Mapping[str, str],
    base_path: str | Path | None,
    enqueue: Callable[..., str] | None,
    emit: Callable[..., None] | None,
    now: float | None,
) -> tuple[int, dict[str, Any]]:
    """The pipeline. Ordered ATOMIC gates that hold under concurrency (Codex round-2):
    size → enabled → resolve → **dedupe (atomic, FIRST)** → per-token rate → **reserve
    (atomic active-check)** → dispatch → link. Every non-deliverable exit logs its real
    reason and returns the uniform 404; the per-token flood limit returns 429."""
    if len(body) > MAX_BODY_BYTES:
        return 413, {"error": "too_large"}

    if not inbound_enabled():
        logger.info("webhook: refused — inbound disabled (dark)")
        return _NOT_DELIVERABLE

    from tinyassets.api.runs import terminal_run_ids_for_universe
    from tinyassets.storage import webhook_hooks

    base = Path(base_path) if base_path is not None else _default_base()
    binding = webhook_hooks.resolve(base, token=token)
    if binding is None:
        logger.info("webhook: refused — unknown/revoked/malformed token")
        return _NOT_DELIVERABLE

    universe_id = str(binding["universe_id"]).strip()
    branch_def_id = str(binding["branch_def_id"]).strip()
    source_id = (binding.get("source_id") or "").strip() or None
    owner_principal_id = str(binding.get("owner_principal_id") or "").strip()
    if not owner_principal_id:
        # A hook minted before owners were recorded. It would run as nobody,
        # which is exactly what the fail-closed boundary removes: refuse, say
        # so in the log (the caller sees the uniform 404), and the owner
        # re-creates the hook or Source.
        logger.error(
            "webhook: refused — hook %s… has no recorded owner; re-create it",
            token[:12],
        )
        return _NOT_DELIVERABLE
    if not universe_id:
        logger.error("webhook: refused — binding for a valid token had an empty universe_id")
        return _NOT_DELIVERABLE

    dedupe_key = _delivery_key(token, body)

    # ── Gate 1: dedupe FIRST, atomically (Codex #4/#5). N concurrent identical deliveries
    # produce exactly ONE claim winner; losers get 202-replay WITHOUT consuming any budget.
    if not webhook_hooks.claim_delivery(
        base, dedupe_key=dedupe_key, window_s=_DEDUPE_WINDOW_S, now=now,
    ):
        logger.info("webhook: deduped replay for command center %s", universe_id)
        return 202, {"queued": True, "deduped": True}

    reservation_id: str | None = None
    try:
        # ── Gate 2: durable atomic per-TOKEN flood admission (Codex #3).
        if not webhook_hooks.admit(
            base, token=token, universe_id=universe_id,
            token_max=_RATE_MAX, window_s=_RATE_WINDOW_S, now=now,
        ):
            webhook_hooks.release_delivery(base, dedupe_key=dedupe_key)
            logger.info("webhook: token flood limit reached for command center %s", universe_id)
            return 429, {"error": "rate_limited"}

        # ── Gate 3: ONE atomic transaction — re-check the token is ACTIVE (serializes with a
        # concurrent revoke) and reserve. No in-flight ceiling: the run queues for a seat.
        terminal = terminal_run_ids_for_universe(base, universe_id)
        reservation_id, _reason = webhook_hooks.reserve_dispatch(
            base, token=token, universe_id=universe_id,
            ttl_s=_RESERVATION_TTL_S, terminal_run_ids=terminal, now=now,
        )
        if reservation_id is None:
            webhook_hooks.release_delivery(base, dedupe_key=dedupe_key)
            logger.info("webhook: token revoked at reserve for command center %s", universe_id)
            return _NOT_DELIVERABLE

        inputs = {"webhook": {
            "payload": _decode_body(body),
            "raw_base64": base64.b64encode(body).decode("ascii"),
            "headers": _safe_headers(headers),
        }}

        if source_id is not None:
            # The reservation is HELD (not released here): the bus fires the run later and
            # `_inbound_event_run_fn` links + releases it. On emit failure, release below.
            (emit or _emit_source_event)(
                source_id=source_id, universe_id=universe_id,
                dedupe_key=dedupe_key, inputs=inputs, reservation_id=reservation_id,
                owner_principal_id=owner_principal_id,
            )
            return 202, {"queued": True, "via": "event"}

        run_id = (enqueue or _enqueue_branch_run)(
            base, universe_id=universe_id, branch_def_id=branch_def_id, inputs=inputs,
            principal_id=owner_principal_id,
        )
        webhook_hooks.link_dispatch(base, reservation_id=reservation_id, run_id=str(run_id))
        return 202, {"queued": True}
    except Exception:
        # Roll back BOTH the reservation and the delivery claim so a legitimate retry works,
        # then re-raise into the uniform-404 boundary (Codex #7).
        if reservation_id is not None:
            try:
                webhook_hooks.release_dispatch(base, reservation_id=reservation_id)
            except Exception:  # noqa: BLE001
                logger.exception("webhook: reservation release failed")
        try:
            webhook_hooks.release_delivery(base, dedupe_key=dedupe_key)
        except Exception:  # noqa: BLE001
            logger.exception("webhook: delivery release failed")
        raise


def _default_base() -> Path:
    from tinyassets.storage import data_dir

    return Path(data_dir())


def _enqueue_branch_run(
    base_path: str | Path, *, universe_id: str, branch_def_id: str, inputs: dict[str, Any],
    principal_id: str,
) -> str:
    """Enqueue a run of ``branch_def_id`` as ``universe:<universe_id>``.

    Enqueued via the SHARED audited trigger path (``enqueue_universe_branch_run``), which
    fails closed on an empty universe, uses the same provider binding as ``run_graph``, and
    ledgers the run. Never a host identity.
    """
    from tinyassets.api.runs import enqueue_universe_branch_run

    return enqueue_universe_branch_run(
        base_path,
        universe_id=universe_id,
        branch_def_id=branch_def_id,
        inputs=inputs,
        run_name="webhook",
        principal_id=principal_id,
    )


#: Private key under which the dispatch reservation id rides inside the event's run inputs.
#: `_inbound_event_run_fn` pops it (so it never reaches the branch) to link + release the
#: in-flight reservation to the run the bus fires.
RESERVATION_INPUT_KEY = "__inbound_reservation__"


def _emit_source_event(
    *,
    source_id: str,
    universe_id: str,
    dedupe_key: str,
    inputs: dict[str, Any],
    reservation_id: str,
    owner_principal_id: str,
) -> None:
    """Publish a Source-node inbound event onto the scheduler bus.

    The event carries the run inputs and the hook OWNER's principal (never a
    credential); ``event_id`` is the SERVER-side dedupe key so the bus also dedupes
    at-most-once (Codex #4). The subscription carries the universe binding; the
    owner rides on the event so the run binds to a person without a lookup on the
    event thread (authenticated-owner boundary D2). The in-flight reservation id rides
    under a private inputs key so the fired run can link + release it.
    """
    from tinyassets.scheduler import SchedulerEvent, emit_event, is_running

    if not is_running():
        # Fail loud internally; the caller sees the uniform 404 (Codex #7). A Source hook
        # with the bus off is a misconfiguration — never a silent drop.
        raise RuntimeError("inbound event bus is not running; cannot publish source event")
    payload_inputs = {**inputs, RESERVATION_INPUT_KEY: reservation_id}
    emit_event(SchedulerEvent(
        event_type=f"source:{source_id}",
        event_id=dedupe_key,
        payload={"universe_id": universe_id, "inputs": payload_inputs},
        owner_principal_id=owner_principal_id,
    ))

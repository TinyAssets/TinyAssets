"""Requester-local provider assignment state and admission.

Assignments are server-owned routing authority.  Configuration preferences are
only a projection; served turns re-read this SQLite state under the shared
admission lock before any credential or provider access.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_selection import SelectedModel
    from tinyassets.providers.native_model_selection import NativeSelection

from tinyassets.provider_assignment_manifest import (
    AssignmentCandidate,
    ensure_manifest_schema,
    load_candidates,
    manifest_digest,
    store_candidates,
)
from tinyassets.providers.owner_binding import (
    CONNECT_PROVIDER_MESSAGE as _CONNECT_PROVIDER_MESSAGE,
)
from tinyassets.storage import db_path

logger = logging.getLogger(__name__)

_WINDOWS_LOCK_RETRY_ATTEMPTS = 100
_WINDOWS_LOCK_RETRY_SECONDS = 0.01
_SERVED_REQUEST_MAX_INVOCATIONS = 10_000


@dataclass(frozen=True, slots=True)
class ProviderAssignment:
    universe_id: str
    owner_user_id: str
    state: str
    generation: int
    provider: str
    binding_id: str
    binding_generation: int
    binding_digest: str
    credential_reference_id: str
    credential_reference_generation: int
    credential_reference_digest: str
    assignment_digest: str
    updated_at: str
    manifest_digest: str = ""
    candidates: tuple[AssignmentCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ServedProviderAuthority:
    """Fresh, server-validated provider facts for one exact served call.

    ``authority_kind`` explicitly discriminates the credential model (Codex shape
    review of serve-open-compute-provider): ``subscription_snapshot`` carries the
    subscription custody tuple + a required ``credential_snapshot_dir``;
    ``connection_grant`` carries an open provider's exact definition + grant identity
    with ``credential_snapshot_dir=None`` (the credential lives in the connection grant,
    resolved credential-blind at call time). The kind is NEVER inferred from a missing
    snapshot — an absent subscription snapshot must fail closed, not silently become
    open authority."""

    authority_kind: str
    provider: str
    max_invocations: int
    request_max_invocations: int
    max_tokens: int
    max_cost_microunits: int
    owner_user_id: str
    universe_id: str
    agent_binding_id: str
    binding_revision: int
    binding_id: str
    binding_generation: int
    binding_digest: str
    credential_reference_id: str
    credential_reference_generation: int
    credential_reference_digest: str
    credential_service: str
    credential_snapshot_dir: Path | None = field(repr=False, compare=False)
    request_capability: object = field(repr=False, compare=False)
    operation: str = "converse"
    allowed_roles: tuple[str, ...] = ("writer",)
    budget_owner: str = "served_request"
    before_provider_launch: object | None = field(
        default=None, repr=False, compare=False
    )
    selected_model: SelectedModel | None = None
    native_selection: NativeSelection | None = None
    after_provider_claim: object | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ServedProviderBudgetReservation:
    reservation_id: str
    binding_id: str
    binding_generation: int
    output_tokens: int
    reserved_total_tokens: int
    reserved_cost_microunits: int


_SERVED_COST_MICROUNITS_PER_TOKEN = 100

#: Cap on retained SETTLED served-budget rows (Codex gate #7 — bounded growth).
#: Open rows are never pruned, nor is any row still inside the runaway window;
#: only the oldest settled history beyond both the window and this cap.
_SETTLED_RETENTION_ROWS = 5000


def _is_open_provider(name: str) -> bool:
    """True for an open compute provider serving name (``api_key_http:<def-id>``).
    Its credential is a connection grant, not a subscription snapshot — the authority
    + budget paths branch on this."""
    return name.startswith("api_key_http:")

#: Rolling window for the invocation runaway guard (Codex 2026-08-19 reject #3).
#: The per-binding-generation invocation ceiling counts only rows CREATED within
#: this window, so it bounds a runaway self-invoking loop WITHOUT ever
#: permanently bricking a 24/7 binding — old invocations age out. This replaces
#: the retention-dependent lifetime count that was simultaneously a routine brick
#: (~2500 conversations) and a restart-resettable runaway guard.
_RUNAWAY_WINDOW_S = 3600.0

#: Margin added to EACH served call's OWN configured timeout to derive its lease
#: deadline. The reconciler settles a reservation only once ``now`` passes
#: ``created_at + call_timeout + this margin``, so it never reclaims a genuinely
#: live call — regardless of how high the (unbounded) served timeout is set.
#: Codex 2026-08-19 re-review #4 reproduced ``UNBOUNDED_SERVED_TIMEOUT=3600``,
#: which ANY fixed lease would race; a per-call deadline tracks the real timeout
#: instead. The margin absorbs the router sync wrapper (+30s) + settle overhead.
_LEASE_MARGIN_S = 300.0

#: Conservative call-timeout assumed when a reservation is written without one on
#: record (``call_timeout_s`` omitted). Kept well above any normal served turn so
#: a healthy call is never reclaimed early.
_FALLBACK_CALL_TIMEOUT_S = 3600.0


def _ensure_served_budget_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS served_provider_budget_reservations (
            reservation_id TEXT PRIMARY KEY,
            binding_id TEXT NOT NULL,
            binding_generation INTEGER NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('reserved', 'succeeded', 'indeterminate', 'exceeded')
            ),
            reserved_total_tokens INTEGER NOT NULL CHECK (reserved_total_tokens >= 1),
            reserved_cost_microunits INTEGER NOT NULL
                CHECK (reserved_cost_microunits >= 1),
            actual_total_tokens INTEGER,
            actual_cost_microunits INTEGER,
            created_at REAL,
            lease_deadline REAL
        )
        """
    )
    # Migration for pre-existing tables. Add the columns if missing, then backfill
    # existing NULL created_at to 0 (epoch) so genuine PRE-migration history reads
    # as ancient — excluded from the runaway window (it is not recent activity).
    # After that a NULL created_at can only come from an OLD/rollback binary
    # inserting mid-upgrade; the guard treats such a stray NULL as IN-window
    # (fail-safe: over-count, never under-count — Codex re-review #3). lease_
    # deadline stays NULL on old rows; the PERIODIC reconciler keys on
    # lease_deadline (so it never reclaims a row whose real deadline is unknown),
    # while BOOT reconciliation settles every open row (safe: nothing is live at
    # boot), catching any NULL-deadline straggler on the next restart.
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(served_provider_budget_reservations)"
    ).fetchall()}
    if "created_at" not in cols:
        conn.execute(
            "ALTER TABLE served_provider_budget_reservations ADD COLUMN created_at REAL"
        )
        conn.execute(
            "UPDATE served_provider_budget_reservations "
            "SET created_at = 0 WHERE created_at IS NULL"
        )
    if "lease_deadline" not in cols:
        conn.execute(
            "ALTER TABLE served_provider_budget_reservations "
            "ADD COLUMN lease_deadline REAL"
        )


def reconcile_orphaned_reservations_on_boot(base_path: str | Path) -> int:
    """Settle every OPEN served-budget reservation at daemon boot; return count.

    The served budget bounds IN-FLIGHT (``reserved`` / ``indeterminate``)
    reservations. A turn that crashed or was killed mid-call leaves its row open
    forever, permanently consuming capacity — the stuck-reservation DoS a
    cross-family review flagged (Codex P1 2026-08-19). At BOOT there are no
    CURRENT in-flight turns yet, so any open row is ORPHANED from a process that
    no longer exists and is safe to settle (its provider request cannot still be
    running — the process holding it is gone). We charge them conservatively
    (actual = reserved) so a crash-loop cannot mint free spend once a rolling
    cumulative budget is added, while releasing the hold so serving resumes. Run
    once, early in daemon startup, before any turn is served.
    """
    from tinyassets.storage.provider_work_authority import (
        SQLiteProviderWorkAuthorityStore,
    )

    store = SQLiteProviderWorkAuthorityStore(base_path)
    try:
        with store.connection() as conn:
            _ensure_served_budget_schema(conn)
            window_start = time.time() - _RUNAWAY_WINDOW_S
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE served_provider_budget_reservations
                   SET state = 'succeeded',
                       actual_total_tokens = COALESCE(
                           actual_total_tokens, reserved_total_tokens),
                       actual_cost_microunits = COALESCE(
                           actual_cost_microunits, reserved_cost_microunits)
                 WHERE state IN ('reserved', 'indeterminate')
                """
            )
            _prune_settled_history(conn, window_start)
            conn.commit()
            return int(cur.rowcount or 0)
    except sqlite3.Error:
        logger.exception("served budget: boot reconciliation failed")
        return 0


def _prune_settled_history(conn: sqlite3.Connection, window_start: float) -> None:
    """Bound SETTLED-row growth WITHOUT touching runaway-window integrity.

    Settled rows are never counted against the in-flight token budget, but they
    accumulate forever — unbounded SQLite growth is a slow DoS (Codex gate #7).
    Cap the settled history to the newest ``_SETTLED_RETENTION_ROWS``, BUT never
    prune a row the runaway guard still counts: the guard must count every recent
    invocation regardless of how retention falls, so the guard is INDEPENDENT of
    retention (Codex reject #3). A row is prunable only if it has a KNOWN
    timestamp older than the window (``created_at IS NOT NULL AND < window_start``).
    NULL created_at is NOT prunable: the guard counts NULL rows as in-window
    (fail-safe over-count), so pruning them would silently defeat the runaway
    guard — pruning NULLs to the retention cap re-admitted a runaway in the
    final Codex re-review 2026-08-19. Backfilled pre-migration rows carry
    ``created_at = 0`` (a known-ancient timestamp) and remain prunable. Open rows
    are never pruned.
    """
    conn.execute(
        """
        DELETE FROM served_provider_budget_reservations
         WHERE state IN ('succeeded', 'exceeded')
           AND created_at IS NOT NULL AND created_at < ?
           AND reservation_id NOT IN (
               SELECT reservation_id
                 FROM served_provider_budget_reservations
                WHERE state IN ('succeeded', 'exceeded')
                ORDER BY rowid DESC
                LIMIT ?
           )
        """,
        (window_start, _SETTLED_RETENTION_ROWS),
    )


def _settle_expired_leases(
    conn: sqlite3.Connection,
    now: float,
    *,
    binding_id: str | None = None,
    binding_generation: int | None = None,
) -> int:
    """Settle in-flight reservations PAST THEIR OWN lease_deadline; return count.

    Each reservation stores ``lease_deadline = created_at + call_timeout +
    margin`` (its OWN worst-case healthy duration), so this reclaims only rows a
    live call cannot still occupy — safe regardless of how high the unbounded
    served timeout is set (Codex re-review #4). Rows with a NULL lease_deadline
    (old/rollback binary) are left to BOOT reconciliation, which is safe because
    nothing is live at boot. Optionally scoped to one binding (opportunistic
    reconcile on the reserve path). Charges actual = reserved (never free spend).
    """
    params: list[object] = [now]
    scope = ""
    if binding_id is not None and binding_generation is not None:
        scope = " AND binding_id = ? AND binding_generation = ?"
        params.extend([binding_id, binding_generation])
    cur = conn.execute(
        f"""
        UPDATE served_provider_budget_reservations
           SET state = 'succeeded',
               actual_total_tokens = COALESCE(
                   actual_total_tokens, reserved_total_tokens),
               actual_cost_microunits = COALESCE(
                   actual_cost_microunits, reserved_cost_microunits)
         WHERE state IN ('reserved', 'indeterminate')
           AND lease_deadline IS NOT NULL AND lease_deadline < ?{scope}
        """,
        params,
    )
    return int(cur.rowcount or 0)


def reconcile_served_budget_leases(base_path: str | Path) -> int:
    """Settle UNSETTLED reservations whose per-call lease expired; return count.

    The token budget bounds only IN-FLIGHT ('reserved'/'indeterminate') rows, so
    a turn that crashed or hung mid-call leaves a hold that never settles during a
    healthy long-lived process — one near-full orphaned reservation can brick
    serving until the next reboot (Codex reject #4: boot-only recovery is not
    enough). This runs PERIODICALLY (not just at boot). It settles only rows past
    their OWN ``lease_deadline`` (Codex re-review #4), so a genuinely live call —
    even one under an unbounded configured timeout — is never reclaimed early.
    """
    from tinyassets.storage.provider_work_authority import (
        SQLiteProviderWorkAuthorityStore,
    )

    store = SQLiteProviderWorkAuthorityStore(base_path)
    try:
        with store.connection() as conn:
            _ensure_served_budget_schema(conn)
            now = time.time()
            conn.execute("BEGIN IMMEDIATE")
            settled = _settle_expired_leases(conn, now)
            _prune_settled_history(conn, now - _RUNAWAY_WINDOW_S)
            conn.commit()
            return settled
    except sqlite3.Error:
        logger.exception("served budget: lease reconciliation failed")
        return 0



def reserve_served_provider_budget(
    base_path: str | Path,
    *,
    universe_dir: str | Path,
    authority: ServedProviderAuthority,
    role: str = "writer",
    requested_output_tokens: int,
    estimated_input_tokens: int,
    call_timeout_s: float | None = None,
) -> ServedProviderBudgetReservation:
    """Atomically reserve remaining durable binding budget before launch.

    ``call_timeout_s`` is this call's configured provider timeout; the row's lease
    deadline is derived from it so the reconciler tracks the real (unbounded)
    timeout instead of a fixed guess (Codex re-review #4). Omitted -> a
    conservative fallback timeout.
    """

    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    held = "Provider authority budget is exhausted; reconnect or rebind your provider."
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (requested_output_tokens, estimated_input_tokens)
    ):
        raise ProviderAuthorityHeldError(held)
    # Explicit authority_kind consistency (Codex reject #5): the credential model is
    # NEVER inferred from the provider-name prefix alone. The kind, the provider shape,
    # and the snapshot presence must agree, or fail closed — an absent subscription
    # snapshot must never be treated as open authority, and vice versa.
    _kind = getattr(authority, "authority_kind", None)
    _open = _is_open_provider(authority.provider)
    if _kind == "connection_grant":
        if not _open or authority.credential_snapshot_dir is not None:
            raise ProviderAuthorityHeldError(held)
    elif _kind == "subscription_snapshot":
        if _open or authority.credential_snapshot_dir is None:
            raise ProviderAuthorityHeldError(held)
    else:
        raise ProviderAuthorityHeldError(held)
    store = SQLiteProviderWorkAuthorityStore(base_path)
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        from tinyassets.provider_serving_binding import current_connected_source_custody

        if authority.budget_owner != "connected_request" or role not in authority.allowed_roles:
            raise ProviderAuthorityHeldError(held)
        universe = Path(universe_dir)
        if universe != Path(base_path) / authority.universe_id:
            raise ProviderAuthorityHeldError(held)
        custody = current_connected_source_custody(
            conn, base=Path(base_path), universe=universe,
            owner=authority.owner_user_id, provider=authority.provider,
        )
        if (custody.reference_id != authority.credential_reference_id
                or custody.generation != authority.credential_reference_generation
                or custody.reference_digest != authority.credential_reference_digest):
            raise ProviderAuthorityHeldError(held)
        _ensure_served_budget_schema(conn)
        now = time.time()
        # Opportunistic reconcile (Codex re-review #4): settle THIS binding's own
        # expired-lease holds before measuring the budget, so a crashed/hung turn
        # is reclaimed on the very next served call — self-healing on EVERY
        # transport (sse/stdio included), not only via the streamable-http
        # periodic thread, and without waiting up to a full reconciler interval.
        _settle_expired_leases(
            conn, now,
            binding_id=authority.binding_id,
            binding_generation=authority.binding_generation,
        )
        rows = conn.execute(
            """
            SELECT state, reserved_total_tokens, reserved_cost_microunits,
                   actual_total_tokens, actual_cost_microunits, created_at
              FROM served_provider_budget_reservations
             WHERE binding_id = ? AND binding_generation = ?
            """,
            (authority.binding_id, authority.binding_generation),
        ).fetchall()
        # Two INDEPENDENT guards share this one row scan. They were deliberately
        # decoupled 2026-08-19 — folding them together silently deleted the
        # runaway guard while "fixing the budget cap" (Codex-gated area).
        #
        # (1) INVOCATION RUNAWAY GUARD — a ROLLING-WINDOW ceiling on how many
        #     times this binding generation may reach the provider within the
        #     last ``_RUNAWAY_WINDOW_S``. Counts ALL rows created in the window
        #     (settled + in-flight), so it survives settlement and bounds a
        #     fast-settling self-invoking loop (e.g. run_graph triggering
        #     itself). Because it only looks at the recent window, old
        #     invocations AGE OUT and it NEVER permanently bricks a 24/7 binding —
        #     unlike the earlier lifetime count, which was at once a routine
        #     brick (~2500 conversations) and, since it depended on how many
        #     settled audit rows retention happened to keep, a restart-resettable
        #     runaway guard (Codex reject #3). NULL created_at is FAIL-SAFE: the
        #     migration backfills genuine pre-migration rows to 0 (ancient,
        #     excluded), so a NULL here can only be a stray insert from an
        #     old/rollback binary mid-upgrade — counted as IN-window (over-count,
        #     never under-count, so a runaway cannot slip through an upgrade
        #     window — Codex re-review #3). The engine-run path has its own tighter
        #     rolling rate limit (`_engine_run_admit`, 20/hr); this is the coarser
        #     binding backstop.
        #
        # (2) TOKEN / COST BUDGET — bounds only IN-FLIGHT (unsettled) reserved
        #     spend: a concurrency + per-turn runaway guard, NOT a cumulative
        #     lifetime ceiling. A SETTLED reservation ('succeeded'/'exceeded')
        #     already spent on the founder's OWN deposited subscription, which
        #     Anthropic itself meters and rate-limits. Counting settled tokens
        #     against a fixed per-generation ceiling made the binding permanently
        #     BRICK after ~max_tokens of lifetime serving and demand a manual
        #     re-bind — the opposite of "24/7 on the resources the user gave it,"
        #     and the reason THIS cap kept being raised as a band-aid. Only
        #     UNSETTLED holds consume token budget now, so each settled turn
        #     RELEASES and the binding serves indefinitely, bounded per-turn by
        #     ``max_tokens`` and overall by the user's real subscription limits.
        #     Stale unsettled rows left by a crashed/hung turn are settled by the
        #     periodic lease reconciler (`reconcile_served_budget_leases`), so a
        #     dead turn's hold cannot slowly re-accumulate into a mid-run brick
        #     (Codex reject #4). NOTE (Codex reject #1): ``max_tokens`` is not a
        #     HARD cap — the CLI subprocess is not passed a token limit and Claude
        #     reports no usage, so settlement is a best-effort byte estimate. The
        #     real spend bound is the user's own metered subscription; this ledger
        #     is best-effort accounting + runaway detection, not a hard boundary.
        window_start = now - _RUNAWAY_WINDOW_S
        in_window = [
            row for row in rows
            if row[5] is None or float(row[5]) >= window_start
        ]
        if len(in_window) >= authority.max_invocations:
            conn.rollback()
            raise ProviderAuthorityHeldError(held)
        _IN_FLIGHT_STATES = ("reserved", "indeterminate")
        in_flight = [row for row in rows if row[0] in _IN_FLIGHT_STATES]
        used_tokens = sum(int(row[1]) for row in in_flight)
        used_cost = sum(int(row[2]) for row in in_flight)
        remaining_tokens = authority.max_tokens - used_tokens
        remaining_cost = authority.max_cost_microunits - used_cost
        affordable_total_tokens = remaining_cost // _SERVED_COST_MICROUNITS_PER_TOKEN
        output_tokens = min(
            requested_output_tokens,
            remaining_tokens - estimated_input_tokens,
            affordable_total_tokens - estimated_input_tokens,
        )
        if authority.selected_model is not None and output_tokens > 0:
            selected_affordable = authority.selected_model.affordable_output(
                remaining_cost, output_limit=output_tokens,
            )
            if selected_affordable is not None:
                output_tokens = min(output_tokens, selected_affordable)
        if output_tokens < 1:
            conn.rollback()
            raise ProviderAuthorityHeldError(held)
        reserved_total = estimated_input_tokens + output_tokens
        reserved_cost = reserved_total * _SERVED_COST_MICROUNITS_PER_TOKEN
        if authority.selected_model is not None:
            # Keep the legacy conservative accounting floor, but never reserve
            # less than the selected HTTP request can cost at its accepted caps.
            reserved_cost = max(
                reserved_cost, authority.selected_model.cost_upper_bound(output_tokens)
            )
        if reserved_cost > remaining_cost:
            conn.rollback()
            raise ProviderAuthorityHeldError(held)
        # Per-call lease deadline: this call's OWN worst-case healthy duration.
        # The reconciler settles a row only past this, so it never reclaims a live
        # call even under an unbounded configured timeout (Codex re-review #4).
        effective_timeout = (
            float(call_timeout_s)
            if isinstance(call_timeout_s, (int, float))
            and not isinstance(call_timeout_s, bool)
            and call_timeout_s > 0
            else _FALLBACK_CALL_TIMEOUT_S
        )
        lease_deadline = now + effective_timeout + _LEASE_MARGIN_S
        reservation = ServedProviderBudgetReservation(
            reservation_id=f"served_budget_{secrets.token_hex(16)}",
            binding_id=authority.binding_id,
            binding_generation=authority.binding_generation,
            output_tokens=output_tokens,
            reserved_total_tokens=reserved_total,
            reserved_cost_microunits=reserved_cost,
        )
        conn.execute(
            """
            INSERT INTO served_provider_budget_reservations (
                reservation_id, binding_id, binding_generation, state,
                reserved_total_tokens, reserved_cost_microunits, created_at,
                lease_deadline
            ) VALUES (?, ?, ?, 'reserved', ?, ?, ?, ?)
            """,
            (
                reservation.reservation_id,
                reservation.binding_id,
                reservation.binding_generation,
                reservation.reserved_total_tokens,
                reservation.reserved_cost_microunits,
                now,
                lease_deadline,
            ),
        )
        conn.commit()
        return reservation


def finalize_served_provider_budget(
    base_path: str | Path,
    *,
    authority: ServedProviderAuthority,
    reservation: ServedProviderBudgetReservation,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_microunits: int | None,
    fallback_output: str = "",
) -> None:
    """Persist actual usage and hold when one call crossed its reservation."""

    from tinyassets.exceptions import ProviderAuthorityHeldError

    measured_input = (
        input_tokens
        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool)
        and input_tokens >= 0
        else max(1, reservation.reserved_total_tokens - reservation.output_tokens)
    )
    measured_output = (
        output_tokens
        if isinstance(output_tokens, int) and not isinstance(output_tokens, bool)
        and output_tokens >= 0
        else len(fallback_output.encode("utf-8"))
    )
    actual_total = measured_input + measured_output
    measured_cost = (
        cost_microunits
        if isinstance(cost_microunits, int) and not isinstance(cost_microunits, bool)
        and cost_microunits >= 0
        else actual_total * _SERVED_COST_MICROUNITS_PER_TOKEN
    )
    if authority.selected_model is not None and (
        type(cost_microunits) is not int or cost_microunits < 0
    ):
        # No reported cost is not a zero-cost receipt. Retain the conservative
        # full reservation estimate; ProviderResponse still reports unknown.
        measured_cost = max(measured_cost, reservation.reserved_cost_microunits)
    exceeded = (
        actual_total > reservation.reserved_total_tokens
        or measured_cost > reservation.reserved_cost_microunits
        or actual_total > authority.max_tokens
        or measured_cost > authority.max_cost_microunits
    )
    conn = sqlite3.connect(db_path(base_path), isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_served_budget_schema(conn)
        cursor = conn.execute(
            """
            UPDATE served_provider_budget_reservations
               SET state = ?, actual_total_tokens = ?, actual_cost_microunits = ?
             WHERE reservation_id = ? AND binding_id = ?
               AND binding_generation = ? AND state = 'reserved'
            """,
            (
                "exceeded" if exceeded else "succeeded",
                actual_total,
                measured_cost,
                reservation.reservation_id,
                authority.binding_id,
                authority.binding_generation,
            ),
        )
        if cursor.rowcount != 1:
            # The row was not 'reserved' when we went to finalize. The expected
            # cause (Codex re-review #4) is that this call outran its own lease
            # deadline and the reconciler already settled the row — a genuinely
            # hung/slow call, not an accounting bug. Tolerate that case (log +
            # return) instead of raising a hard error: the late result is simply
            # not re-charged. Only a truly MISSING row is anomalous.
            already_settled = conn.execute(
                """
                SELECT 1 FROM served_provider_budget_reservations
                 WHERE reservation_id = ? AND binding_id = ?
                   AND binding_generation = ?
                   AND state IN ('succeeded', 'exceeded')
                """,
                (
                    reservation.reservation_id,
                    authority.binding_id,
                    authority.binding_generation,
                ),
            ).fetchone()
            conn.rollback()
            if already_settled is not None:
                logger.warning(
                    "served budget: reservation %s already settled by the lease "
                    "reconciler before finalize (call outran its lease); late "
                    "result not re-charged",
                    reservation.reservation_id,
                )
                return
            raise ProviderAuthorityHeldError("Provider authority budget accounting failed.")
        conn.commit()
    finally:
        conn.close()
    # NOTE (2026-08-22): a per-call overrun is NO LONGER withheld. The reply was
    # already generated and paid for on the founder's own subscription, and a
    # served provider's real input is dominated by context it injects itself
    # (codex mounts a workspace + tool schemas, ~10k+ tokens), so a normal turn
    # routinely exceeds the prompt-byte reservation estimate — withholding it
    # discarded a legitimate reply on essentially every turn (live e2e). The
    # overrun is RECORDED (state='exceeded', actual usage stored) as
    # audit/accounting data — the spend is metered on the founder's own
    # subscription upstream, not re-enforced here (admission counts only IN-FLIGHT
    # reservations, not settled actuals). The aggregate anti-runaway guard is the
    # invocation high-water within the rolling window (max_invocations), which is
    # unchanged. We never throw away delivered work.
    if exceeded:
        logger.warning(
            "served budget: reservation %s exceeded its per-call estimate "
            "(actual_total=%d reserved=%d) — charged actual, reply delivered",
            reservation.reservation_id,
            actual_total,
            reservation.reserved_total_tokens,
        )


def abandon_served_provider_budget(
    base_path: str | Path,
    reservation: ServedProviderBudgetReservation,
) -> None:
    """Conservatively consume a reservation when provider usage is unknown.

    Use ONLY when the provider call began and could have spent tokens before
    dying. A call that never reached the provider (:class:`ProviderUnavailableError`)
    consumed nothing and must be RELEASED instead — see
    :func:`release_served_provider_budget` — or a flaky provider permanently
    exhausts its own budget one failed turn at a time.
    """

    conn = sqlite3.connect(db_path(base_path), isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_served_budget_schema(conn)
        conn.execute(
            """
            UPDATE served_provider_budget_reservations SET state = 'indeterminate'
             WHERE reservation_id = ? AND state = 'reserved'
            """,
            (reservation.reservation_id,),
        )
        conn.commit()
    finally:
        conn.close()


def release_served_provider_budget(
    base_path: str | Path,
    reservation: ServedProviderBudgetReservation,
) -> None:
    """Settle a reservation as NO-SPEND for a call that produced no output.

    A provider that never became available spent no tokens, so its reservation
    must not count against the TOKEN budget — but the row must NOT be deleted.
    Deleting it also erased the invocation from the runaway guard, and
    ``ProviderUnavailableError`` is only a heuristic (a launched subprocess that
    exited quickly), not proof the provider was never reached (Codex reject #5).
    So SETTLE it as a zero-spend 'succeeded' row: the launch still counts toward
    the rolling-window runaway guard, while the released in-flight hold means a
    flaky provider does not read as permanently "budget exhausted". Rolling-
    window aging then keeps even a burst of failed launches from bricking.
    """

    conn = sqlite3.connect(db_path(base_path), isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_served_budget_schema(conn)
        conn.execute(
            """
            UPDATE served_provider_budget_reservations
               SET state = 'succeeded',
                   actual_total_tokens = 0,
                   actual_cost_microunits = 0
             WHERE reservation_id = ? AND state = 'reserved'
            """,
            (reservation.reservation_id,),
        )
        conn.commit()
    finally:
        conn.close()


class _AdmissionState:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.readers = 0
        self.writer: object | None = None
        self.waiting_writers = 0
        self.reader_threads: set[object] = set()


def _acquire_windows_file_lock(
    handle: object,
    *,
    locking=None,
    sleep=time.sleep,
    max_attempts: int = _WINDOWS_LOCK_RETRY_ATTEMPTS,
) -> None:
    """Acquire the Windows byte lock with a bounded fail-closed retry."""

    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts < 1
    ):
        raise ValueError("max_attempts must be a positive integer")
    if locking is None:
        import msvcrt

        locking = msvcrt.locking
        nonblocking_mode = msvcrt.LK_NBLCK
    else:
        nonblocking_mode = 1
    last_error: OSError | None = None
    for attempt in range(max_attempts):
        try:
            handle.seek(0)
            locking(handle.fileno(), nonblocking_mode, 1)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < max_attempts:
                sleep(_WINDOWS_LOCK_RETRY_SECONDS)
    raise TimeoutError(
        "provider assignment admission lock remained unavailable"
    ) from last_error


class ProviderAssignmentAdmission:
    """Cross-process shared-reader/exclusive-writer admission by universe path."""

    def __init__(self) -> None:
        self._states_lock = threading.Lock()
        self._states: dict[str, _AdmissionState] = {}

    @staticmethod
    def _key(universe_dir: str | Path) -> str:
        return str(Path(universe_dir).resolve(strict=False))

    def _state(self, universe_dir: str | Path) -> _AdmissionState:
        key = self._key(universe_dir)
        with self._states_lock:
            return self._states.setdefault(key, _AdmissionState())

    @staticmethod
    def _open_lock_file(universe_dir):
        from tinyassets.workspace_fs import open_assignment_lock

        universe = Path(universe_dir).absolute()
        return open_assignment_lock(universe.parent, universe.name)

    @staticmethod
    @contextmanager
    def _file_lock(universe_dir: str | Path, *, exclusive: bool) -> Iterator[None]:
        handle = ProviderAssignmentAdmission._open_lock_file(universe_dir)
        try:
            if os.name == "nt":
                import msvcrt

                # Windows exposes only exclusive byte-range locks here. That
                # serializes readers conservatively while still excluding
                # credential/assignment writers across processes.
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                _acquire_windows_file_lock(handle)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(handle.fileno(), mode)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @asynccontextmanager
    async def _async_admission(self, universe_dir, *, exclusive):
        """Task identity and writer priority shared with synchronous callers.

        Neither an in-process wait nor a cross-process flock can block the loop.
        Cancelling a waiting writer removes its priority before waking readers.
        """
        from tinyassets.agent_loop.execution import off_loop

        state = self._state(universe_dir)
        identity = asyncio.current_task()
        admitted = False
        handle = None
        with state.condition:
            if state.writer == identity or identity in state.reader_threads:
                raise RuntimeError("provider assignment admission is not reentrant")
            if exclusive:
                state.waiting_writers += 1
        try:
            while not admitted:
                with state.condition:
                    if state.writer is None and (not state.readers if exclusive
                                                 else not state.waiting_writers):
                        if exclusive:
                            state.writer = identity
                        else:
                            state.readers += 1
                            state.reader_threads.add(identity)
                        admitted = True
                if not admitted:
                    await asyncio.sleep(0.01)

            # Opening a local file can block too; retain the handle in this
            # scope even when cancellation arrives during its creation.
            def open_lock():
                nonlocal handle
                handle = self._open_lock_file(universe_dir)
                if os.name == "nt":
                    handle.seek(0, os.SEEK_END)
                    if not handle.tell():
                        handle.write(b"\0")
                        handle.flush()

            await off_loop(open_lock)
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                        fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, PermissionError):
                    await asyncio.sleep(0.01)
            yield
        finally:
            if handle is not None:
                handle.close()
            with state.condition:
                if exclusive:
                    state.waiting_writers -= 1
                    if admitted:
                        state.writer = None
                elif admitted:
                    state.readers -= 1
                    state.reader_threads.remove(identity)
                state.condition.notify_all()

    def shared_async(self, universe_dir):
        return self._async_admission(universe_dir, exclusive=False)

    def exclusive_async(self, universe_dir):
        return self._async_admission(universe_dir, exclusive=True)

    @contextmanager
    def shared(self, universe_dir: str | Path) -> Iterator[None]:
        state = self._state(universe_dir)
        thread_id = threading.get_ident()
        with state.condition:
            if state.writer == thread_id or thread_id in state.reader_threads:
                raise RuntimeError("provider assignment admission is not reentrant")
            while state.writer is not None or state.waiting_writers:
                state.condition.wait()
            state.readers += 1
            state.reader_threads.add(thread_id)
        try:
            with self._file_lock(universe_dir, exclusive=False):
                yield
        finally:
            with state.condition:
                state.readers -= 1
                state.reader_threads.remove(thread_id)
                state.condition.notify_all()

    @contextmanager
    def exclusive(self, universe_dir: str | Path) -> Iterator[None]:
        state = self._state(universe_dir)
        thread_id = threading.get_ident()
        with state.condition:
            if state.writer == thread_id or thread_id in state.reader_threads:
                raise RuntimeError("provider assignment admission is not reentrant")
            state.waiting_writers += 1
            try:
                while state.writer is not None or state.readers:
                    state.condition.wait()
                state.writer = thread_id
            finally:
                state.waiting_writers -= 1
        try:
            with self._file_lock(universe_dir, exclusive=True):
                yield
        finally:
            with state.condition:
                state.writer = None
                state.condition.notify_all()


_ADMISSION = ProviderAssignmentAdmission()


def provider_assignment_admission() -> ProviderAssignmentAdmission:
    return _ADMISSION


def ensure_provider_assignment_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_assignments (
            universe_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('pending', 'ready', 'failed', 'unassigned')),
            generation INTEGER NOT NULL CHECK (generation >= 1),
            provider TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
            binding_digest TEXT NOT NULL,
            credential_reference_id TEXT NOT NULL,
            credential_reference_generation INTEGER NOT NULL
                CHECK (credential_reference_generation >= 1),
            credential_reference_digest TEXT NOT NULL,
            assignment_digest TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(provider_assignments)")}
    if "manifest_digest" not in columns:
        conn.execute(
            "ALTER TABLE provider_assignments ADD COLUMN manifest_digest TEXT NOT NULL DEFAULT ''"
        )
    ensure_manifest_schema(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_provider_assignment_owner
        ON provider_assignments(owner_user_id, universe_id, state)
        """
    )


def provider_assignment_digest(
    *,
    owner_user_id: str,
    universe_id: str,
    provider: str,
    generation: int,
    binding_id: str,
    credential_reference_id: str,
    credential_reference_generation: int,
    credential_reference_digest: str,
    manifest_digest: str = "",
) -> str:
    payload = {
        "binding_id": binding_id,
        "credential_reference_digest": credential_reference_digest,
        "credential_reference_generation": credential_reference_generation,
        "credential_reference_id": credential_reference_id,
        "generation": generation,
        "owner_user_id": owner_user_id,
        "provider": provider,
        "schema_version": 1,
        "universe_id": universe_id,
    }
    if manifest_digest:
        payload["schema_version"] = 2
        payload["manifest_digest"] = manifest_digest
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assignment_from_row(row: sqlite3.Row | tuple[object, ...]) -> ProviderAssignment:
    values = list(row)
    assignment = ProviderAssignment(
        universe_id=str(values[0]),
        owner_user_id=str(values[1]),
        state=str(values[2]),
        generation=int(values[3]),
        provider=str(values[4]),
        binding_id=str(values[5]),
        binding_generation=int(values[6]),
        binding_digest=str(values[7]),
        credential_reference_id=str(values[8]),
        credential_reference_generation=int(values[9]),
        credential_reference_digest=str(values[10]),
        assignment_digest=str(values[11]),
        updated_at=str(values[12]),
        manifest_digest=str(values[13]),
    )
    expected = provider_assignment_digest(
        owner_user_id=assignment.owner_user_id,
        universe_id=assignment.universe_id,
        provider=assignment.provider,
        generation=assignment.generation,
        binding_id=assignment.binding_id,
        credential_reference_id=assignment.credential_reference_id,
        credential_reference_generation=assignment.credential_reference_generation,
        credential_reference_digest=assignment.credential_reference_digest,
        manifest_digest=assignment.manifest_digest,
    )
    if assignment.assignment_digest != expected:
        raise RuntimeError("provider assignment digest is invalid")
    return assignment


def _validate_assignment_manifest(assignment: ProviderAssignment) -> None:
    if not assignment.manifest_digest:
        if assignment.candidates:
            raise ValueError("legacy assignment cannot contain candidates")
        return
    expected = manifest_digest(assignment.provider, assignment.candidates)
    if expected != assignment.manifest_digest:
        raise ValueError("provider assignment manifest digest is invalid")
    anchor = next(c for c in assignment.candidates if c.provider == assignment.provider)
    for name in (
        "provider", "binding_id", "binding_generation", "binding_digest",
        "credential_reference_id", "credential_reference_generation", "credential_reference_digest",
    ):
        if getattr(anchor, name) != getattr(assignment, name):
            raise ValueError("provider assignment anchor does not match its candidate")


def _load_assignment_manifest(
    conn: sqlite3.Connection, assignment: ProviderAssignment,
) -> ProviderAssignment:
    if not assignment.manifest_digest:
        return assignment  # Old child rows never grant authority to a legacy root.
    try:
        assignment = replace(assignment, candidates=load_candidates(
            conn, assignment.universe_id, assignment.generation,
        ))
        _validate_assignment_manifest(assignment)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("provider assignment manifest is invalid") from exc
    return assignment


def load_provider_assignment_in_transaction(
    conn: sqlite3.Connection,
    *,
    universe_id: str,
) -> ProviderAssignment | None:
    ensure_provider_assignment_schema(conn)
    row = conn.execute(
        """
        SELECT universe_id, owner_user_id, state, generation, provider,
               binding_id, binding_generation, binding_digest,
               credential_reference_id, credential_reference_generation,
               credential_reference_digest, assignment_digest, updated_at, manifest_digest
          FROM provider_assignments WHERE universe_id = ?
        """,
        (universe_id.strip(),),
    ).fetchone()
    return _load_assignment_manifest(conn, _assignment_from_row(row)) if row is not None else None


def store_provider_assignment_in_transaction(
    conn: sqlite3.Connection,
    assignment: ProviderAssignment,
) -> None:
    if not conn.in_transaction:
        raise ValueError("provider assignment write requires an active transaction")
    expected = provider_assignment_digest(
        owner_user_id=assignment.owner_user_id,
        universe_id=assignment.universe_id,
        provider=assignment.provider,
        generation=assignment.generation,
        binding_id=assignment.binding_id,
        credential_reference_id=assignment.credential_reference_id,
        credential_reference_generation=assignment.credential_reference_generation,
        credential_reference_digest=assignment.credential_reference_digest,
        manifest_digest=assignment.manifest_digest,
    )
    if assignment.assignment_digest != expected:
        raise ValueError("provider assignment digest is invalid")
    _validate_assignment_manifest(assignment)
    ensure_provider_assignment_schema(conn)
    conn.execute(
        """
        INSERT INTO provider_assignments (
            universe_id, owner_user_id, state, generation, provider,
            binding_id, binding_generation, binding_digest,
            credential_reference_id, credential_reference_generation,
            credential_reference_digest, assignment_digest, updated_at, manifest_digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(universe_id) DO UPDATE SET
            owner_user_id = excluded.owner_user_id,
            state = excluded.state,
            generation = excluded.generation,
            provider = excluded.provider,
            binding_id = excluded.binding_id,
            binding_generation = excluded.binding_generation,
            binding_digest = excluded.binding_digest,
            credential_reference_id = excluded.credential_reference_id,
            credential_reference_generation = excluded.credential_reference_generation,
            credential_reference_digest = excluded.credential_reference_digest,
            assignment_digest = excluded.assignment_digest,
            updated_at = excluded.updated_at,
            manifest_digest = excluded.manifest_digest
        """,
        (
            assignment.universe_id,
            assignment.owner_user_id,
            assignment.state,
            assignment.generation,
            assignment.provider,
            assignment.binding_id,
            assignment.binding_generation,
            assignment.binding_digest,
            assignment.credential_reference_id,
            assignment.credential_reference_generation,
            assignment.credential_reference_digest,
            assignment.assignment_digest,
            assignment.updated_at,
            assignment.manifest_digest,
        ),
    )
    if assignment.manifest_digest:
        store_candidates(conn, assignment.universe_id, assignment.generation, assignment.candidates)


def load_provider_assignment(
    base_path: str | Path,
    *,
    universe_id: str,
) -> ProviderAssignment | None:
    conn = sqlite3.connect(db_path(base_path))
    try:
        ensure_provider_assignment_schema(conn)
        conn.execute("BEGIN")  # Root and children must come from one read snapshot.
        return load_provider_assignment_in_transaction(conn, universe_id=universe_id)
    finally:
        conn.close()


def _served_request_agent(base_path, universe, request_carrier, role, operation):
    """One validator shared by discovery preflight and final launch admission."""
    from tinyassets.auth.middleware import validate_provider_request_carrier
    from tinyassets.commons_catalog import get_binding

    uid = universe.name
    binding_id = str(getattr(request_carrier, "agent_binding_id", ""))
    revision = getattr(request_carrier, "binding_revision", 0)
    if str(getattr(request_carrier, "universe_id", "")) != uid or not binding_id:
        raise PermissionError("provider request does not match command center")
    capability = validate_provider_request_carrier(
        request_carrier, universe_id=uid, agent_binding_id=binding_id,
        binding_revision=revision, operation=operation,
    )
    from tinyassets.request_answers import OWNER_ANSWER_SOURCE

    accepted_sources = {
        ("tinyassets.authenticated-request.v1", "tinyassets.auth.middleware", "converse"),
        OWNER_ANSWER_SOURCE,
    }
    if (capability.mechanism, capability.issuer, capability.tool_name) not in accepted_sources:
        raise PermissionError("provider request source is not trusted")
    if role != "writer" or operation != "converse":
        raise PermissionError("served authority is converse/writer only")
    agent = get_binding(base_path, universe_id=uid, binding_id=binding_id)
    if agent is None or not all((
        agent["status"] == "serving",
        agent["created_by"] == capability.principal_id,
        int(agent["revision"]) == revision,
    )):
        raise PermissionError("agent binding is not current serving authority")
    return capability, agent


def accepted_connected_model_access(assignment, owner, provider):
    """Read the owner's accepted scope and cost authority from the assignment."""
    from tinyassets.provider_assignment_manifest import ModelAccess

    if assignment is None or assignment.owner_user_id != owner or assignment.state != "ready":
        raise PermissionError("connected model access is unavailable")
    if assignment.manifest_digest:
        member = next((item for item in assignment.candidates if item.provider == provider), None)
        if member is not None:
            return member.access
    elif assignment.provider == provider and provider in _SERVED_PROVIDER_SERVICE:
        # A legacy native binding accepted only its provider default.
        return ModelAccess("explicit", ("",))
    elif assignment.provider == provider and provider.startswith("api_key_http:"):
        # A legacy single-provider HTTP binding accepted exactly the model the
        # owner connected it with, free-only: what the legacy lane always ran
        # and what the model picker shows as its `legacy_source`. Nothing
        # discovered, nothing priced, until the owner confirms model access.
        from tinyassets.providers.definition import get_definition

        definition = get_definition(
            assignment.universe_id, provider.removeprefix("api_key_http:"),
        )
        if (definition is not None and definition.owner_user_id == owner
                and definition.access_method == "api_key_http" and definition.model):
            return ModelAccess("explicit", (definition.model,), None)
    raise PermissionError("model source is outside accepted access")


def _selected_chain(store, base_path, universe, capability, agent, selection):
    """Read the selected owner's live custody and accepted model access."""
    from tinyassets.provider_serving_binding import current_connected_source_custody
    from tinyassets.providers.model_policy import ModelRef

    if not isinstance(selection, ModelRef):
        raise PermissionError("invalid model selection")
    with store.connection() as conn:
        conn.execute("BEGIN")
        custody = current_connected_source_custody(
            conn, base=Path(base_path), universe=universe,
            owner=capability.principal_id, provider=selection.connection_id,
        )
        assignment = load_provider_assignment_in_transaction(conn, universe_id=universe.name)
        access = accepted_connected_model_access(
            assignment, capability.principal_id, selection.connection_id,
        )
        conn.rollback()
    if selection.connection_id.startswith("api_key_http:"):
        from tinyassets.providers.discovery_snapshot import refresh_model_discovery

        refresh_model_discovery(
            owner_user_id=capability.principal_id, universe_id=universe.name,
            definition_id=selection.connection_id.removeprefix("api_key_http:"),
        )
    return selection.connection_id, custody, access


def check_served_agent_tool_authority(universe_context) -> str:
    """Fresh engine-side execution fence, without a provider launch or held IO lock."""
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    universe = universe_context.universe_dir
    if universe is None or universe_context.provider_invocation is not None:
        raise PermissionError("interactive agent requires a current served request")
    with provider_assignment_admission().shared(universe):
        capability, agent = _served_request_agent(
            universe.parent, universe, universe_context.provider_request, "writer", "converse",
        )
        _selected_chain(
            SQLiteProviderWorkAuthorityStore(universe.parent), universe.parent,
            universe, capability, agent, universe_context.model_selection,
        )
        return capability.principal_id


@contextmanager
def authorize_served_provider_call(
    base_path: str | Path, *, universe_dir: str | Path, request_carrier: object,
    role: str, operation: str, model_selection: ModelRef | None = None,
) -> Iterator[ServedProviderAuthority]:
    """Synchronous authority entrypoint; caller-supplied discovery is not accepted."""
    with _authorize_served_provider_call(
        base_path, universe_dir=universe_dir, request_carrier=request_carrier,
        role=role, operation=operation, model_selection=model_selection,
    ) as authority:
        yield authority


@asynccontextmanager
async def authorize_served_provider_call_async(
    base_path: str | Path, *, universe_dir: str | Path, request_carrier: object,
    role: str, operation: str, model_selection: ModelRef,
    agent_turn: bool = False,
):
    """Release admission for discovery; revalidate the exact chain before launch.

    Task-owned admission never blocks the event-loop thread.
    Synchronous authority and credential IO runs in a worker. Neither callers nor configuration may
    supply the prepared result, and the final fence rechecks request + custody.
    """
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.model_selection import prepare_selected_model_async
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore
    from tinyassets.subscription_refresh import refresh_deposited_subscriptions

    universe = Path(universe_dir)
    # THE interactive launch path. Wiring only the workflow lanes left the
    # founder's own served turn -- the surface the outage is on -- never calling
    # the refresh at all (Codex refute-review, P1 #7).
    #
    # Here, at the very top: before the shared admission is taken (the refresh
    # needs the EXCLUSIVE one, which cannot nest inside it), before `_selected_chain`
    # resolves custody, and before the snapshot below pins the credential. A refresh
    # after any of those would make the launch refuse with "credential changed
    # before launch snapshot".
    #
    # `launching` is the source this call actually selected, so another deposited
    # source's dead credential cannot fail this one.
    #
    # The owner is the request's principal, proved by the same check the launch
    # makes, under the shared admission (released again before the refresh takes
    # the exclusive one). It is what carries the accepted binding onto a rotated
    # document. Passing no owner rotated the founder's sign-in and then could not
    # renew the binding, so custody pinned the old digest and every turn after it
    # was refused "connect your provider" (live 2026-09-28). A request that proves
    # no owner refreshes nothing: it is refused below, and it must not spend the
    # owner's single-use refresh token on its way there.
    #
    # A selection that is not a `ModelRef` is refused below, by the checks that
    # already exist for it, and NOTHING is refreshed for it. Reading a field off an
    # unvalidated caller-supplied object turned that refusal into an AttributeError
    # (`test_async_unaccepted_selection_refused_before_discovery`, which passes a
    # bare object precisely to pin the ordering) -- and more importantly, the
    # selection decides WHICH source is refreshed and which one may fail the
    # launch, so an unrecognized one must steer neither.
    from tinyassets.agent_loop.execution import off_loop
    from tinyassets.providers.model_policy import ModelRef

    admission = provider_assignment_admission()
    if isinstance(model_selection, ModelRef):
        try:
            async with admission.shared_async(universe):
                facts = await off_loop(
                    _served_request_agent, base_path, universe, request_carrier, role, operation,
                )
                owner = facts[0].principal_id
        except Exception:  # noqa: BLE001 - refused with its own words below
            owner = ""
        if owner:
            await off_loop(
                refresh_deposited_subscriptions,
                base_path=base_path, universe_dir=universe, owner_user_id=owner,
                universe_id=universe.name,
                launching=_SERVED_PROVIDER_SERVICE.get(model_selection.connection_id, ""),
            )
    try:
        async with admission.shared_async(universe):
            capability, agent = await off_loop(
                _served_request_agent, base_path, universe, request_carrier, role, operation,
            )
            chain = await off_loop(
                _selected_chain, SQLiteProviderWorkAuthorityStore(base_path), base_path, universe,
                capability, agent, model_selection,
            )
        from tinyassets.providers.model_selection import saved_effort_level

        effort = await off_loop(
            saved_effort_level, base_path, capability.principal_id, universe.name, model_selection,
        )
        selected, recheck = await prepare_selected_model_async(
            base_path=Path(base_path), owner_user_id=capability.principal_id,
            universe_id=universe.name, provider=chain[0],
            model_id=model_selection.model_id, access=chain[2],
            needs_tools=agent_turn, effort=effort,
        )
    except ProviderAuthorityHeldError:
        raise
    except Exception as exc:
        raise ProviderAuthorityHeldError(_SERVED_AUTHORITY_HELD) from exc
    from tinyassets.agent_loop.execution import off_loop_context

    # Final validation runs under task admission; it releases before inference.
    manager = _authorize_served_provider_call(
        base_path, universe_dir=universe, request_carrier=request_carrier,
        role=role, operation=operation, model_selection=model_selection,
        _prepared_selection=(agent, chain, selected, recheck),
        agent_turn=agent_turn, _admitted=True,
    )
    async with admission.shared_async(universe):
        # Keep resource cleanup outside the assignment lock, as for sync calls.
        context = off_loop_context(manager)
        authority = await context.__aenter__()
    try:
        yield authority
    finally:
        await context.__aexit__(None, None, None)


#: Which subscription record a served provider launches from. Hoisted out of the
#: snapshot branch below so the pre-launch refresh names the same source the
#: launch will, rather than repeating the mapping.
_SERVED_PROVIDER_SERVICE = {"codex": "codex", "claude-code": "claude"}

#: Imported, not re-declared: `providers.owner_binding` owns the sentence.
_SERVED_AUTHORITY_HELD = _CONNECT_PROVIDER_MESSAGE


@contextmanager
def _authorize_served_provider_call(
    base_path: str | Path, *, universe_dir: str | Path,
    request_carrier: object, role: str, operation: str,
    model_selection: ModelRef | None = None, _prepared_selection=None,
    agent_turn: bool = False, _admitted: bool = False,
) -> Iterator[ServedProviderAuthority]:
    """Authorize one model on the request principal's live connected set."""
    from tinyassets.commons_catalog import get_binding
    from tinyassets.credential_vault import (
        cleanup_llm_credential_snapshot,
        snapshot_llm_subscription_credential,
    )
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_serving_binding import current_connected_source_custody
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_selection import prepare_selected_model, saved_effort_level
    from tinyassets.providers.served_model_plan import connected_sources
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    universe = Path(universe_dir)
    base = Path(base_path)
    authority = None
    snapshot = None
    try:
        with (nullcontext() if _admitted else provider_assignment_admission().shared(universe)):
            capability, agent = _served_request_agent(
                base, universe, request_carrier, role, operation,
            )
            sources = tuple(sorted(connected_sources(base, universe, capability.principal_id)))
            if not sources:
                raise PermissionError("no connected model source for this owner")
            if model_selection is None:
                from tinyassets.providers.work_candidate_data import prepare_captured_choices

                candidates = prepare_captured_choices(
                    base, owner=capability.principal_id, universe=universe.name,
                    sources=sources, document={
                        "version": 1, "saved": None,
                        "observed_generation": 0, "current": None,
                    },
                )
                model_selection = candidates.order[0]
            if type(model_selection) is not ModelRef:
                raise PermissionError("invalid selected model")
            store = SQLiteProviderWorkAuthorityStore(base)
            chain = _selected_chain(store, base, universe, capability, agent, model_selection)
            if _prepared_selection is None:
                selected, selection_recheck = prepare_selected_model(
                    base_path=base, owner_user_id=capability.principal_id,
                    universe_id=universe.name, provider=chain[0],
                    model_id=model_selection.model_id, access=chain[2],
                    needs_tools=agent_turn,
                    effort=saved_effort_level(
                        base, capability.principal_id, universe.name, model_selection,
                    ),
                )
            else:
                before_agent, before_chain, selected, selection_recheck = _prepared_selection
                if before_agent != agent or before_chain != chain:
                    raise PermissionError("selected source changed during discovery")
                if selection_recheck is not None:
                    selection_recheck()
            with store.connection() as conn:
                conn.execute("BEGIN")
                custody = current_connected_source_custody(
                    conn, base=base, universe=universe,
                    owner=capability.principal_id, provider=model_selection.connection_id,
                )
                if custody != chain[1]:
                    raise PermissionError("connected source custody changed during discovery")
                conn.rollback()
            provider = model_selection.connection_id
            if _is_open_provider(provider):
                kind, service, snapshot_dir = "connection_grant", "http", None
            else:
                service = _SERVED_PROVIDER_SERVICE.get(provider)
                if service is None:
                    raise PermissionError("connected subscription executor is unavailable")
                snapshot = snapshot_llm_subscription_credential(
                    universe_dir=universe, custody=custody,
                )
                kind, snapshot_dir = "subscription_snapshot", snapshot.directory
            import hashlib

            budget_digest = "sha256:" + hashlib.sha256(
                f"connected:{capability.principal_id}:{universe.name}".encode("utf-8")
            ).hexdigest()
            budget_id = "connected_" + budget_digest.removeprefix("sha256:")[:32]

            def before_launch() -> None:
                if selection_recheck is not None:
                    selection_recheck()
                if get_binding(
                    base, universe_id=universe.name,
                    binding_id=str(request_carrier.agent_binding_id),
                ) != agent:
                    raise PermissionError("agent binding changed before model launch")
                with store.connection() as current_conn:
                    current_conn.execute("BEGIN")
                    current = current_connected_source_custody(
                        current_conn, base=base, universe=universe,
                        owner=capability.principal_id, provider=provider,
                    )
                    if current != custody:
                        raise PermissionError("connected source custody changed before launch")
                    current_conn.rollback()

            authority = ServedProviderAuthority(
                authority_kind=kind, provider=provider,
                max_invocations=10_000,
                request_max_invocations=_SERVED_REQUEST_MAX_INVOCATIONS,
                max_tokens=4_000_000, max_cost_microunits=400_000_000,
                owner_user_id=capability.principal_id, universe_id=universe.name,
                agent_binding_id=str(request_carrier.agent_binding_id),
                binding_revision=int(request_carrier.binding_revision),
                binding_id=budget_id, binding_generation=1,
                binding_digest=budget_digest,
                credential_reference_id=custody.reference_id,
                credential_reference_generation=custody.generation,
                credential_reference_digest=custody.reference_digest,
                credential_service=service, credential_snapshot_dir=snapshot_dir,
                request_capability=capability, budget_owner="connected_request",
                selected_model=selected if _is_open_provider(provider) else None,
                native_selection=selected if not _is_open_provider(provider) else None,
                before_provider_launch=before_launch,
            )
        yield authority
    except ProviderAuthorityHeldError:
        raise
    except Exception as exc:
        if authority is not None:
            raise
        raise ProviderAuthorityHeldError(_SERVED_AUTHORITY_HELD) from exc
    finally:
        cleanup_llm_credential_snapshot(snapshot)


__all__ = [
    "ProviderAssignment",
    "ProviderAssignmentAdmission",
    "ServedProviderAuthority",
    "ServedProviderBudgetReservation",
    "abandon_served_provider_budget",
    "release_served_provider_budget",
    "reconcile_served_budget_leases",
    "authorize_served_provider_call",
    "ensure_provider_assignment_schema",
    "load_provider_assignment",
    "load_provider_assignment_in_transaction",
    "provider_assignment_admission",
    "provider_assignment_digest",
    "reserve_served_provider_budget",
    "finalize_served_provider_budget",
    "store_provider_assignment_in_transaction",
]

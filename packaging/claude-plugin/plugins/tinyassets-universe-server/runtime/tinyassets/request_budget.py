"""Per-turn dispatch limits and separate advisory daily evidence; no quota probes."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.storage import DB_FILENAME

UNBOUNDED = None
LEARNING_MIN_REMAINING = 10

# Execution policy, NOT an assertion about a provider's remaining allowance.
FREE_TURN_ATTEMPTS = 6
TEXT_TURN_ATTEMPTS = 3
FREE_CONSECUTIVE_FAILURES = 2


class RequestBudgetExceeded(ProviderAuthorityHeldError):
    """A local turn stopped; no reconnect, approval, cooldown or automatic wake."""

    SOURCE_LIMIT_REASONS = frozenset({
        "free_attempt_limit", "free_pool_attempt_limit", "consecutive_failures",
    })

    def __init__(self, reason, receipt):
        self.reason = reason
        self.request_receipt = receipt
        self.continuation = (
            f"This turn stopped at its request budget ({receipt['dispatched']} "
            "provider attempts). Any progress recorded in the turn journal is preserved. "
            "You can request a continuation; none is scheduled automatically."
        )
        super().__init__(self.continuation)


@dataclass(frozen=True)
class RequestAttempt:
    ordinal: int
    source_ref: str
    model: str
    purpose: str
    free: bool
    state: str
    reserved_at: float
    dispatched_at: str | None = None


class TurnRequestBudget:
    """One parent turn's locked ledger, shared by retries, helpers and children.

    This object restricts dispatch but grants no provider/tool authority. Bind it
    outside the writer and keep it through secondary work. Child tasks share the
    object; independent turns create independent objects. Closing is final, so a
    copied task context cannot spend after the parent returns. No crash replay.
    ``dispatched`` means an invocation crossed the local dispatch boundary, not
    proof the upstream received it or a confirmed provider billing count.
    """

    def __init__(self, owner, universe, *, max_requests=None,
                 free_limit=FREE_TURN_ATTEMPTS, failure_limit=FREE_CONSECUTIVE_FAILURES,
                 free_pool_limit=FREE_TURN_ATTEMPTS,
                 deadline=None, clock=time.monotonic, source_limits=None, wall_clock=None):
        for limit in (max_requests, free_limit, failure_limit, free_pool_limit):
            if limit is not None and (type(limit) is not int or limit < 1):
                raise ValueError("request limits must be positive integers")
        if not owner or not universe:
            raise ValueError("parent turn owner and command center required")
        self.owner, self.universe = owner, universe
        self.max_requests, self.free_limit = max_requests, free_limit
        self.free_pool_limit = free_pool_limit
        self.failure_limit, self.deadline, self.clock = failure_limit, deadline, clock
        self.wall_clock = wall_clock or _now
        self.source_limits = dict(source_limits or {})
        for limit in self.source_limits.values():
            if limit is not None and (type(limit) is not int or limit < 1):
                raise ValueError("source request limits must be positive integers")
        self._lock = threading.RLock()
        self._attempts = []
        self._failures = {}
        self._closed = False
        self._store = None
        self.usage_id = None
        self._lease = None

    @property
    def _scope(self):
        return self.owner, self.universe, self.usage_id

    def persist(self, base_path):
        """Attach once, using the router/session's trusted owner data root."""
        from tinyassets.storage.agent_request_usage import UsageStore

        with self._lock:
            if self._store is not None:
                if self._store.base != Path(base_path).resolve():
                    raise ProviderAuthorityHeldError("parent request budget data root changed")
                return
            store = UsageStore(base_path)
            import weakref

            self.usage_id, self._lease = store.create(self)
            weakref.finalize(self, self._lease.close)
            self._store = store

    def link(self, kind, subject_id):
        with self._lock:
            if self._store is not None:
                self._store.link(self._scope, kind, subject_id)

    def issue_reference(self, ordinal, **kwargs):
        with self._lock:
            if self._store is None:
                raise ProviderAuthorityHeldError("HTTP inference requires durable usage accounting")
            return self._store.issue_reference(self._scope, ordinal, **kwargs)

    def settle_invocation(self, ordinal, outcome):
        with self._lock:
            if self._store is not None:
                return self._store.settle_invocation(self._scope, ordinal, outcome)
            attempt = self._attempts[ordinal - 1]
            if attempt.state == "reserved":
                self.settle(ordinal, "not_sent")
            if attempt.state == "dispatched":
                self.settle(ordinal, outcome)
            return int(attempt.dispatched_at is not None)

    def _stored(self, operation, *args, **kwargs):
        return self._store.mutate(self._scope, operation, *args, clock=self.clock,
                                  wall_clock=self.wall_clock, **kwargs)

    def check_scope(self, owner, universe):
        if (owner, universe) != (self.owner, self.universe):
            raise ProviderAuthorityHeldError("parent request budget scope changed")

    def _check(self, *, source_ref, free, purpose):
        active = [a for a in self._attempts if a.state != "not_sent"]
        limit = self.source_limits.get(source_ref, self.free_limit)
        source_attempts = sum(a.free and a.source_ref == source_ref for a in active)
        reason = None
        if self._closed:
            reason = "parent_closed"
        elif self.deadline is not None and self.clock() >= self.deadline:
            reason = "dispatch_deadline"
        elif free and purpose == "learning":
            reason = "automatic_learning_disabled"
        elif self.max_requests is not None and len(active) >= self.max_requests:
            reason = "turn_attempt_limit"
        elif (free and self.free_pool_limit is not None
              and sum(a.free for a in active) >= self.free_pool_limit):
            reason = "free_pool_attempt_limit"
        elif free and limit is not None and source_attempts >= limit:
            reason = "free_attempt_limit"
        elif (free and self.failure_limit is not None
              and self._failures.get(source_ref, 0) >= self.failure_limit):
            reason = "consecutive_failures"
        if reason is not None:
            raise RequestBudgetExceeded(reason, self.receipt())

    def check_available(self, *, source_ref, free, purpose="reply"):
        with self._lock:
            if self._store is not None:
                return self._stored("check_available", source_ref=source_ref,
                                    free=free, purpose=purpose)
            self._check(source_ref=source_ref, free=free, purpose=purpose)

    def reserve(self, *, owner, universe, source_ref, model, free, purpose="reply"):
        """Reserve BEFORE dispatch, after existing grant/admission checks.

        ``free`` means an admitted metered free source, NOT merely zero price.
        Local/unmetered models pass False. Only explicit owner policy supplies
        max_requests. The free pool allocation counts only metered free attempts;
        adding connections cannot multiply it or restrict paid/local work.
        """
        self.check_scope(owner, universe)
        if purpose not in {"reply", "tool_review", "review", "helper", "learning"}:
            raise ValueError("unknown request purpose")
        if type(free) is not bool or not source_ref or not model:
            raise ValueError("admitted source, model and price classification required")
        with self._lock:
            if self._store is not None:
                return self._stored("reserve", owner=owner, universe=universe,
                                    source_ref=source_ref, model=model, free=free, purpose=purpose)
            self._check(source_ref=source_ref, free=free, purpose=purpose)
            attempt = RequestAttempt(len(self._attempts) + 1, source_ref, model, purpose,
                                     free, "reserved", self.clock())
            self._attempts.append(attempt)
            return attempt.ordinal

    def dispatched(self, ordinal):
        from dataclasses import replace

        with self._lock:
            if self._store is not None:
                return self._stored("dispatched", ordinal)
            if type(ordinal) is not int or not 1 <= ordinal <= len(self._attempts):
                raise ValueError("unknown request reservation")
            attempt = self._attempts[ordinal - 1]
            if attempt.state != "reserved":
                raise ValueError("request reservation already dispatched or settled")
            # A reservation is no licence to launch after Stop/parent completion.
            if self._closed or self.deadline is not None and self.clock() >= self.deadline:
                self._attempts[ordinal - 1] = replace(attempt, state="not_sent")
                raise RequestBudgetExceeded("dispatch_closed", self.receipt())
            self._attempts[ordinal - 1] = replace(
                attempt, state="dispatched",
                dispatched_at=self.wall_clock().astimezone(timezone.utc).isoformat(),
            )

    def settle(self, ordinal, outcome):
        from dataclasses import replace

        with self._lock:
            if self._store is not None:
                return self._stored("settle", ordinal, outcome)
            if type(ordinal) is not int or not 1 <= ordinal <= len(self._attempts):
                raise ValueError("unknown request reservation")
            attempt = self._attempts[ordinal - 1]
            allowed = {"not_sent"} if attempt.state == "reserved" else {
                "succeeded", "failed", "unknown",
            } if attempt.state == "dispatched" else set()
            if outcome not in allowed:
                raise ValueError("invalid request settlement")
            self._attempts[ordinal - 1] = replace(attempt, state=outcome)
            if attempt.free:
                # Settlement completion order is not request order: an older
                # success arriving late must not erase newer failed requests.
                failures = 0
                for item in reversed(self._attempts):
                    if item.source_ref != attempt.source_ref or not item.free:
                        continue
                    if item.state == "succeeded":
                        break
                    if item.state in {"failed", "unknown"}:
                        failures += 1
                self._failures[attempt.source_ref] = failures

    def close(self):
        with self._lock:
            self._closed = True
            if self._lease is not None:
                self._lease.close()
            if self._store is not None:
                return self._stored("close")

    def receipt(self):
        """Detached source/purpose counters; no prompts, results or credentials."""
        from dataclasses import asdict

        with self._lock:
            if self._store is not None:
                return self._store.receipt(self._scope, clock=self.clock,
                                           wall_clock=self.wall_clock)
            groups = {}
            for attempt in self._attempts:
                group = groups.setdefault((attempt.source_ref, attempt.purpose), {
                    "source_ref": attempt.source_ref, "purpose": attempt.purpose,
                    "reserved": 0, "dispatched": 0, "succeeded": 0,
                    "failed": 0, "unknown": 0, "not_sent": 0,
                })
                group["reserved"] += 1
                if attempt.dispatched_at is not None:
                    group["dispatched"] += 1
                if attempt.state not in {"reserved", "dispatched"}:
                    group[attempt.state] += 1
            return {
                "dispatched": sum(a.dispatched_at is not None for a in self._attempts),
                "reserved": len(self._attempts), "closed": self._closed,
                "sources": list(groups.values()),
                "attempts": [asdict(a) for a in self._attempts],
                "quota_authoritative": False,
                "count_basis": "local_provider_dispatch",
            }


_TURN_REQUEST_BUDGET = ContextVar("parent_turn_request_budget", default=None)


def current_request_budget():
    return _TURN_REQUEST_BUDGET.get()


@contextmanager
def request_budget_scope(budget, *, close_on_exit=True):
    """Propagate a parent object without permitting a nested reset of its limit."""
    current = current_request_budget()
    if current is not None and current is not budget:
        raise ProviderAuthorityHeldError("cannot replace an active parent request budget")
    token = _TURN_REQUEST_BUDGET.set(budget)
    try:
        yield budget
    finally:
        try:
            if current is None and close_on_exit:
                budget.close()
        finally:
            _TURN_REQUEST_BUDGET.reset(token)


def selection_is_free(selection):
    """Use admitted ceilings; never infer this from an account name or balance."""
    from tinyassets.providers.declared_models import DeclaredModelContract

    if isinstance(getattr(selection, "execution_contract", None), DeclaredModelContract):
        # Declared free/flat contracts are explicitly unmetered. A host's
        # separate free-tier offer cannot turn that plan into a metered one.
        return False
    caps = getattr(selection, "cost_caps", ())
    return bool(caps) and all(type(value) is int and value == 0 for _, value in caps)


def _now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RequestBudget:
    used: int
    cap: int
    source_name: str
    reset_timezone: str
    credit_amount: str | None = None
    credit_url: str = ""
    credit_requests_per_day: int | None = None

    @property
    def remaining(self):
        return self.cap - self.used

    @property
    def next_reset(self):
        local = _now().astimezone(ZoneInfo(self.reset_timezone))
        return (local.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)).astimezone(timezone.utc)


@dataclass(frozen=True)
class PooledBudget:
    sources: tuple[tuple[str, RequestBudget], ...]

    @property
    def remaining(self):
        return sum(max(0, budget.remaining) for _, budget in self.sources)

    @property
    def next_reset(self):
        return min(budget.next_reset for _, budget in self.sources)

    def reset_description(self):
        reset = self.next_reset
        hours = max(1, math.ceil((reset - _now()).total_seconds() / 3600))
        return f"{reset:%Y-%m-%d %H:%M UTC} (in about {hours} hours)"

    def connect_suggestion(self):
        text = (
            f"The local free-request estimate is low ({self.remaining} left). "
            "Your account may have a higher allowance; work can continue. "
            "Connect another free AI source"
        )
        for _, budget in self.sources:
            if (budget.credit_amount and budget.credit_url
                    and budget.credit_requests_per_day
                    and budget.credit_requests_per_day > budget.cap):
                return (text + f", or check {budget.source_name}'s credit tier "
                        f"({budget.credit_url}): {budget.credit_amount} of purchased credit "
                        f"qualifies for {budget.credit_requests_per_day} daily requests; "
                        "your account may already qualify.")
        return text + "."

    def prompt_line(self):
        split = ", ".join(
            f"{budget.source_name} ({max(0, budget.remaining)} left, "
            f"resets 00:00 {budget.reset_timezone})" for _, budget in self.sources
        )
        return (
            f"Compute today: local estimate of about {self.remaining} requests left "
            f"across {split}. "
            "This estimate uses installed limits, local dispatch records and historical journal "
            "estimates, not a confirmed "
            "account quota. A higher allowance or usage elsewhere may change it. "
            "Even at zero I continue the requested work while the provider accepts requests; "
            "this is not a final-request signal. I save progress to notes/<project>-progress.md "
            "as I work. If the provider actually refuses for capacity, I describe the known "
            "progress and remaining work without claiming an unsaved file exists or an "
            "automatic wake is armed. "
            f"The earliest installed daily reset is {self.reset_description()}, "
            "not a confirmed recovery time for a provider refusal. "
            "The owner can connect another source for more compute."
        )


def _read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)


def requests_today(base_path, owner, source_ref, *, reset_timezone,
                   zero_priced_models=(), now=None):
    """Local dispatch evidence plus legacy round estimates, never remote quota.

    Durable attempts use their own UTC dispatch time, including failed/helper
    requests and midnight crossings. Linked journal rounds are excluded, so the
    same request is not counted twice. Older unlinked rounds remain estimates
    bucketed by turn creation because they have no dispatch timestamp.
    """
    try:
        current = now or _now()
        reset = current.astimezone(ZoneInfo(reset_timezone)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ).astimezone(timezone.utc)
        events = []
        with closing(_read_only(Path(base_path) / DB_FILENAME)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            )}
            if "agent_request_attempts" in tables:
                rows = conn.execute(
                    "SELECT usage_id, ordinal, attempt_json FROM agent_request_attempts "
                    "WHERE owner=? AND source_ref=? AND dispatched_at IS NOT NULL "
                    "AND julianday(dispatched_at) >= julianday(?) "
                    "AND julianday(dispatched_at) <= julianday(?)",
                    (owner, source_ref, reset.isoformat(), current.isoformat()),
                )
                for usage_id, ordinal, raw in rows:
                    attempt = json.loads(raw)
                    instant = datetime.fromisoformat(attempt["dispatched_at"])
                    if attempt["free"] is True and reset <= instant <= current:
                        events.append((instant, usage_id, ordinal, attempt["state"] == "succeeded"))
            if "agent_turns" in tables:
                unlinked = (
                    "AND NOT EXISTS (SELECT 1 FROM agent_request_usage_links u "
                    "WHERE u.owner=t.owner_user_id AND u.universe=t.universe_id "
                    "AND u.kind='turn' AND u.subject_id=t.turn_id) "
                    if "agent_request_usage_links" in tables else ""
                )
                rows = conn.execute(
                    "SELECT t.created_at, t.turn_id, r.ordinal, r.candidate_json, r.state, "
                    "r.reply_json FROM agent_turns t JOIN agent_turn_rounds r "
                    "ON (t.owner_user_id=r.owner_user_id AND t.universe_id=r.universe_id "
                    "AND t.turn_id=r.turn_id) WHERE t.owner_user_id=? "
                    "AND julianday(t.created_at) >= julianday(?) "
                    "AND julianday(t.created_at) <= julianday(?) " + unlinked,
                    (owner, reset.isoformat(), current.isoformat()),
                )
                for created_at, turn_id, ordinal, raw, state, reply in rows:
                    instant = datetime.fromisoformat(created_at)
                    candidate = json.loads(raw)
                    model = candidate.get("model", "")
                    if (reset <= instant <= current and candidate.get("source_ref") == source_ref
                            and (model.endswith(":free") or model in zero_priced_models)):
                        events.append((instant, turn_id, ordinal, reply is not None
                                       and state not in {"failed", "inference_started"}))
            elif "agent_request_attempts" not in tables:
                return None
        successful = 0
        for count, event in enumerate(sorted(events), 1):
            if event[3]:
                successful = count
        return len(events), successful
    except Exception:  # noqa: BLE001 - unavailable advisory evidence never breaks a turn
        return None


def request_budget(base_path, owner, source_ref, model, *, preset, zero_priced_models=(), now=None):
    """One data-driven cap policy; successful requests beyond a cap correct it."""
    try:
        cap = preset.get("requests_per_day")
        if (type(cap) is not int or cap <= 0
                or not (model.endswith(":free") or model in zero_priced_models)):
            return None
        counts = requests_today(
            base_path, owner, source_ref, reset_timezone=preset["reset_timezone"],
            zero_priced_models=zero_priced_models, now=now,
        )
        if counts is None:
            return None
        used, successful = counts
        if successful > cap:
            cap = preset.get("credit_requests_per_day")
            if type(cap) is not int or cap < successful:
                return None
        return RequestBudget(
            used, cap, preset["name"],
            preset["reset_timezone"], preset.get("credit_amount"),
            preset.get("credit_url", ""), preset.get("credit_requests_per_day"),
        )
    except Exception:  # noqa: BLE001 - advisory only
        return None


def _source_budget_facts(context, *, owner=None, require_known_free_model=False):
    """Resolve installed source facts and captured prices locally, with no IO to a model."""
    try:
        from tinyassets.providers.definition import get_definition
        from tinyassets.providers.free_sources import daily_cap_for_host

        selection = context.model_selection
        if selection is None or not selection.connection_id.startswith("api_key_http:"):
            return None
        root = context.universe_dir
        definition = get_definition(
            root.name, selection.connection_id.removeprefix("api_key_http:"),
        )
        if definition is None or (owner is not None and definition.owner_user_id != owner):
            return None
        owner = definition.owner_user_id
        with closing(_read_only(root.parent / "outbound.db")) as conn:
            row = conn.execute(
                "SELECT c.allowed_endpoints_json FROM outbound_connections c "
                "JOIN outbound_connection_grants g ON c.connection_id = g.connection_id "
                "WHERE g.grant_id = ? AND g.owner_user_id = ? AND c.owner_user_id = ? "
                "AND g.universe_id = ? AND g.revoked_at IS NULL AND c.revoked_at IS NULL",
                (definition.ref, owner, owner, root.name),
            ).fetchone()
        if row is None:
            return None
        hosts = {ep["host"] for ep in json.loads(row[0])}
        if len(hosts) != 1:
            return None
        host = hosts.pop()
        if require_known_free_model and not (
            host == "openrouter.ai" and selection.model_id.endswith(":free")
            and len(selection.model_id) > len(":free")
        ):
            return None
        preset = daily_cap_for_host(host)
        return owner, preset
    except Exception:  # noqa: BLE001 - unavailable source facts do not invent limits
        return None


def metered_free_source(context, selection, *, owner):
    if context is None or selection is not None and not selection_is_free(selection):
        return False
    # Legacy admitted calls have no captured price selection. Only exact
    # source-specific free model identities supply missing price evidence;
    # a provider's free tier never classifies its paid/default models.
    facts = _source_budget_facts(context, owner=owner,
                                 require_known_free_model=selection is None)
    return bool(facts and facts[1] and facts[1].get("requests_per_day"))


def candidate_is_metered_free(context, catalog, *, owner):
    """Local allocation classification only; invocation still admits afresh."""
    if catalog.owner_id != owner:
        return False
    facts = _source_budget_facts(context, owner=owner)
    if not (facts and facts[1] and facts[1].get("requests_per_day")):
        return False
    ref = context.model_selection
    for connection in catalog.connections:
        if connection.connection_id == ref.connection_id:
            for model in connection.models:
                if model.model_id == ref.model_id:
                    price = model.pricing
                    return (price.freshness == "fresh" and not price.unknown_components
                            and bool(price.charges)
                            and all(c.amount_micros == 0 for c in price.charges))
    return False


def budget_for_context(context, *, owner=None):
    """Advisory daily evidence, separate from per-turn dispatch admission."""
    try:
        facts = _source_budget_facts(context, owner=owner)
        if facts is None:
            return None
        owner, preset = facts
        selection, root = context.model_selection, context.universe_dir
        zero = set()
        plan = context.agent_model_plan
        if plan is not None and plan.catalog.owner_id == owner:
            for connection in plan.catalog.connections:
                if connection.connection_id == selection.connection_id:
                    for model in connection.models:
                        price = model.pricing
                        if (price.freshness == "fresh" and not price.unknown_components
                                and price.charges
                                and all(c.amount_micros == 0 for c in price.charges)):
                            zero.add(model.model_id)
        return request_budget(
            root.parent, owner, selection.connection_id, selection.model_id,
            preset=preset, zero_priced_models=zero,
        )
    except Exception:  # noqa: BLE001 - no prompt or guard on unknown budgets
        return None


def pooled_budget(base_path, owner, universe_context, *, exhaustion=()):
    """The accepted turn order, counted once per source; doubt never throttles.

    Discovery and admission belong to served_model_plan, not this local reader.
    An absent plan does not prove that the selected source is the only usable one.
    """
    try:
        from dataclasses import replace

        context = universe_context
        if Path(base_path).resolve() != context.universe_dir.parent.resolve():
            return UNBOUNDED
        plan = context.agent_model_plan
        if plan is None:
            return UNBOUNDED
        candidates = plan.order(owner, context.universe_dir.name, exhaustion).candidates
        if not candidates:
            return UNBOUNDED
        sources = {}
        for candidate in candidates:
            budget = budget_for_context(
                replace(context, model_selection=candidate.ref), owner=owner,
            )
            if budget is None:
                return UNBOUNDED
            sources[candidate.ref.connection_id] = budget
        return PooledBudget(tuple(sources.items()))
    except Exception:  # noqa: BLE001 - unknown evidence must not throttle
        return UNBOUNDED


def budget_for_rail(base_path, owner, universe_dir):
    """Rebuild the current owned pool for display; never store a setup request."""
    try:
        from tinyassets.config import load_universe_config
        from tinyassets.provider_assignment import load_provider_assignment
        from tinyassets.provider_serving_binding import resolve_serving_agent_binding
        from tinyassets.providers.base import UniverseContext
        from tinyassets.providers.served_model_plan import prepare_owned_model_plan

        assignment = load_provider_assignment(base_path, universe_id=universe_dir.name)
        if (assignment is None or assignment.owner_user_id != owner
                or not assignment.candidates
                or any(not member.provider.startswith("api_key_http:")
                       for member in assignment.candidates)):
            # Subscription/native sources have no installed daily request cap.
            # Rail polling must not discover or initialize their executors.
            return UNBOUNDED
        agent = resolve_serving_agent_binding(
            base_path, universe_id=universe_dir.name, owner_user_id=owner,
        )
        config = load_universe_config(universe_dir)
        prepared = prepare_owned_model_plan(
            base=base_path, universe=universe_dir, owner=owner, agent=agent,
            config=config, allow_empty=True,
        )
        if prepared is None:
            return UNBOUNDED
        return pooled_budget(base_path, owner, UniverseContext(
            universe_dir=universe_dir, config=config, agent_model_plan=prepared.plan,
        ))
    except Exception:  # noqa: BLE001 - unavailable advisory evidence omits the suggestion
        return UNBOUNDED

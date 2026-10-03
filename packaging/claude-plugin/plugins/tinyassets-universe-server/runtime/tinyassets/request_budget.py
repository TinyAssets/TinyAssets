"""Advisory daily request budgets from local evidence, never a remote quota probe."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tinyassets.storage import DB_FILENAME

UNBOUNDED = None
LEARNING_MIN_REMAINING = 10


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
            "This estimate uses installed limits and local journal counts, not a confirmed "
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
    """Return (requests, latest successful request ordinal), or None if unreadable.

    Every free-model round counts, including failed and in-flight requests.
    Rounds have no timestamp: bucket by their turn's created_at, including turns
    spanning midnight. Source refs are the journal's opaque connection selectors.
    Price-zero IDs come from the captured catalogue; suffix :free needs no price.
    """
    try:
        current = now or _now()
        reset = current.astimezone(ZoneInfo(reset_timezone)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ).astimezone(timezone.utc)
        count = successful = 0
        with closing(_read_only(Path(base_path) / DB_FILENAME)) as conn:
            rows = conn.execute(
                "SELECT t.created_at, r.candidate_json, r.state, r.reply_json "
                "FROM agent_turns t JOIN agent_turn_rounds r "
                "ON (t.owner_user_id = r.owner_user_id AND t.universe_id = r.universe_id "
                "AND t.turn_id = r.turn_id) WHERE t.owner_user_id = ? "
                "AND julianday(t.created_at) >= julianday(?) "
                "AND julianday(t.created_at) <= julianday(?) "
                "ORDER BY t.created_at, t.turn_id, r.ordinal",
                (owner, reset.isoformat(), current.isoformat()),
            )
            for created_at, raw, state, reply in rows:
                # SQLite date arithmetic rounds sub-millisecond timestamps. Keep
                # the SQL range as a coarse filter, then honor the exact reset.
                instant = datetime.fromisoformat(created_at)
                if not reset <= instant <= current:
                    continue
                candidate = json.loads(raw)
                if candidate.get("source_ref") != source_ref:
                    continue
                model = candidate.get("model", "")
                if not (model.endswith(":free") or model in zero_priced_models):
                    continue
                count += 1
                if reply is not None and state not in {"failed", "inference_started"}:
                    successful = count
        return count, successful
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


def budget_for_context(context, *, owner=None):
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
        preset = daily_cap_for_host(hosts.pop())
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

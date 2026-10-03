"""Finite run-session advisory choices, never an invocation authority carrier."""

import json
import threading
from dataclasses import replace

from tinyassets.exceptions import WorkModelExhaustedError
from tinyassets.providers.model_policy import ModelPolicy, ModelRef, order_models


def fit_orders(groups, *, ceiling):
    """Fit only Automatic discovered prefixes into already accepted authority.

    Each group is (ordered refs, aggregate compiler retry weight, pure Automatic).
    Explicit choices remain complete and ordered or the graph refuses.
    """
    if type(ceiling) is not int or ceiling < 1 or not groups:
        raise PermissionError("workflow exceeds the shared invocation allowance")
    fitted, minimum = {}, 0
    for key in sorted(groups):
        order, weight, automatic = groups[key]
        if (type(order) is not tuple or not order or len(set(order)) != len(order)
                or any(type(ref) is not ModelRef for ref in order)
                or type(weight) is not int or weight < 1 or type(automatic) is not bool):
            raise ValueError("invalid finite work model order")
        fitted[key] = order[:1] if automatic else order
        minimum += weight * len(fitted[key])
    if minimum > ceiling:
        raise PermissionError("workflow exceeds the shared invocation allowance")
    while True:
        advanced = False
        for key in sorted(groups):
            order, weight, automatic = groups[key]
            size = len(fitted[key])
            if automatic and size < len(order) and minimum + weight <= ceiling:
                fitted[key] = order[:size + 1]
                minimum += weight
                advanced = True
        if not advanced:
            return fitted, minimum


def policy_key(policy):
    return json.dumps(policy or {}, sort_keys=True, separators=(",", ":"), allow_nan=False)


def prepare_captured_choices(base, *, owner, universe, document):
    """Discover current owned facts using original preference DATA, outside SQL."""
    from pathlib import Path

    from tinyassets.provider_serving_binding import resolve_serving_agent_binding
    from tinyassets.providers.model_preferences import ModelPreferences, capture_preference_policy
    from tinyassets.providers.served_model_plan import prepare_owned_model_plan
    from tinyassets.storage.model_preferences import PreferenceSnapshot

    if (not isinstance(document, dict)
            or set(document) != {"version", "saved", "observed_generation", "current"}
            or type(document["version"]) is not int or document["version"] != 1):
        raise ValueError("invalid captured work preference document")
    saved = None if document["saved"] is None else ModelPreferences.from_document(document["saved"])
    current = (None if document["current"] is None else
               ModelPreferences.from_document(document["current"]))
    generation = document["observed_generation"]
    capture_preference_policy(saved=saved, observed_generation=generation, current=current)
    agent = resolve_serving_agent_binding(base, universe_id=universe, owner_user_id=owner)
    prepared = prepare_owned_model_plan(
        base=base, universe=Path(base) / universe, owner=owner, agent=agent, current=current,
        preference_snapshot=PreferenceSnapshot(generation, saved),
    )
    return None if prepared is None else WorkCandidateData(prepared.plan)


def _matches(ref, pin):
    if not isinstance(pin, dict):
        raise PermissionError("invalid graph model constraint")
    provider = pin.get("provider")
    model = pin.get("model_id", pin.get("model"))
    return (not provider or ref.connection_id == provider) and (not model or ref.model_id == model)


class WorkCandidateData:
    """One run's captured advisory order/capacity facts; no model-call methods.

    Keep the original catalogue facts to interpret exhaustion if a subsequent
    discovery loses that source. Fresh _authorize_attempt still decides every
    invocation. Never attach this object to a served context or progress journal.
    """

    def __init__(self, plan):
        self.automatic = (plan.policy.mode == "automatic"
                          and plan.policy.current_selection is None
                          and plan.policy.saved_default is None)
        if not self.automatic:
            # A workflow's explicit order is "complete and ordered or the graph
            # refuses", checked position by position below and against each
            # node's pinned primary. Demoting a recently refused model would
            # reorder it and refuse the run outright, so an explicit order is
            # kept and the run steps past a refusal as it meets one.
            plan = replace(plan, refused_models=())
        # An Automatic order only ranks, so it keeps the chat turn's demotion: a
        # model this owner's source refused (``storage.refused_models``) goes
        # last, and a run does not spend a request rediscovering the refusal a
        # chat turn or an earlier run already met (live 2026-10-01).
        self.owner, self.universe = plan.catalog.owner_id, plan.catalog.universe_id
        self.catalog, self.interaction = plan.catalog, plan.interaction
        self.source_policies = plan.source_policies
        # Includes the existing Automatic-only source-health demotion.
        self.order = tuple(item.ref for item in plan.order(self.owner, self.universe).candidates)
        explicit = plan.policy.current_selection or plan.policy.saved_default
        if explicit is not None:
            requested = (explicit, *plan.policy.fallbacks)
            if len(self.order) != len(requested) or any(
                actual.connection_id != expected.connection_id
                or (expected.model_id and actual.model_id != expected.model_id)
                for actual, expected in zip(self.order, requested)
            ):
                raise PermissionError("explicit work model order is not fully eligible")
        if not self.order:
            # TYPED, not a bare PermissionError: `_admit`'s handler converts any
            # other exception into held authority, and "your accepted sources
            # produced no runnable model" is a different owner action from
            # "connect a provider". The run taxonomy keys on this MESSAGE, which
            # is why it is the class constant rather than a local literal.
            raise WorkModelExhaustedError(WorkModelExhaustedError.MESSAGE)
        self._fitted = None
        self._exhaustion = ()
        self._lock = threading.RLock()

    def _admitted_refs(self, pin):
        """Every admitted model of this owner matching ``pin``, catalogue order.

        `self.order` is the ADVISORY order, and for a subscription/local source
        that is only its advertised default -- `order_models` deliberately keeps
        a subscription's other ids "visible in the catalogue for explicit
        selection" rather than ranking them. So a node naming an accepted native
        id is never in the order, and treating the order as the whole world
        refused that node outright once every run started capturing one.

        The catalogue read here is the plan's ADMITTED catalogue: this owner's
        accepted sources with the candidate-only (learned, not-yet-granted) rows
        already removed, so a pin still cannot reach a model the owner's own
        `ModelAccess` does not admit -- and `_authorize_attempt` and
        `_validate_work_selection` revalidate the exact id afterwards regardless.
        """
        return tuple(
            ref
            for connection in self.catalog.connections
            for ref in (
                ModelRef(connection.connection_id, model.model_id)
                for model in connection.models
            )
            if _matches(ref, pin)
        )

    def resolved_pin(self, pin):
        """``pin`` with its provider resolved to one of this owner's sources.

        A bare access method (``api_key_http``) names the single admitted source
        of that method, or the one offering the pinned model
        (``providers.model_pins``); ambiguity refuses with the choices.
        """
        if not isinstance(pin, dict) or not pin.get("provider"):
            return pin
        from tinyassets.providers.model_pins import resolve_pin_source

        sources = {
            connection.connection_id: tuple(model.model_id for model in connection.models)
            for connection in self.catalog.connections
        }
        provider = resolve_pin_source(
            pin["provider"], pin.get("model_id", pin.get("model", "")), sources,
        )
        return pin if provider == pin["provider"] else {**pin, "provider": provider}

    def _constrained(self, policy):
        if not policy:
            return self.order
        if policy.get("difficulty_override"):
            raise PermissionError("dynamic graph model overrides conflict with captured selection")
        pin = self.resolved_pin(policy.get("preferred", {}))
        matching = tuple(ref for ref in self.order if _matches(ref, pin))
        if not matching and self.automatic:
            # The owner chose nothing, so the captured order ranks, it does not
            # decide. A graph pin IS an explicit choice and `order_models`
            # already serves one from the catalogue; honour it rather than
            # refusing the node for not appearing in an advisory ranking.
            matching = self._admitted_refs(pin)
        if not matching or (not self.automatic and matching[0] != self.order[0]):
            raise PermissionError("graph model constraint conflicts with captured primary")
        primary = matching[0]
        tail = tuple(ref for ref in self.order if ref != primary)
        if "fallback_chain" in policy:
            permitted = policy["fallback_chain"]
            permitted = [self.resolved_pin(item) for item in permitted]
            narrowed = tuple(ref for ref in tail if any(_matches(ref, pin) for pin in permitted))
            if not self.automatic and narrowed != tail:
                raise PermissionError("graph model constraint conflicts with explicit fallbacks")
            tail = narrowed
        return (primary, *tail)

    def fit(self, snapshot, *, ceiling, retry_multiplier):
        groups = {}
        for node in snapshot["node_defs"]:
            if not str(node.get("prompt_template") or "").strip():
                continue
            policy = node.get("llm_policy") or snapshot.get("default_llm_policy")
            key = policy_key(policy)
            prior = groups.get(key, (self._constrained(policy), 0, self.automatic))
            groups[key] = (prior[0], prior[1] + (retry_multiplier if policy else 1), prior[2])
        fitted, minimum = fit_orders(groups, ceiling=ceiling)
        with self._lock:
            if self._fitted is not None and self._fitted != fitted:
                raise PermissionError("admitted work model order changed")
            self._fitted = fitted
        return minimum

    @property
    def exhaustion(self):
        with self._lock:
            return self._exhaustion

    def exhausted_error(self, boundaries=()):
        """Typed "the order ran out", carrying only owner-visible classified facts.

        Evidence is every exhausted ref and scope this run retained, plus -- for
        a boundary the caller validated itself -- its classified failure class
        and retry-after. Never the provider's response body: that stays on the
        chained cause, in process, and is not copied into the run record.
        """
        parts = []
        for item in self.exhaustion:
            detail = [f"{item.scope} scope"]
            for boundary in boundaries:
                if boundary.exhaustion == item:
                    if boundary.failure_class:
                        detail.append(boundary.failure_class)
                    if boundary.refusal_detail:
                        # The source refused THIS model; its own words say why
                        # (an agentic-harness gate, a withdrawn model). Not a
                        # rate limit, and its siblings stayed eligible.
                        detail.append("the source refused this model: "
                                      + boundary.refusal_detail)
                    if boundary.cooling_s is not None:
                        # Skipped, not asked: an earlier failure on this source
                        # (named by the router's detail) is being waited out.
                        detail.append(
                            f"source cooling down, {boundary.cooling_s:g}s left: "
                            f"{boundary.cooling_detail or 'provider cooldown gate'}; "
                            "not a spent allowance"
                        )
                    if boundary.daily_detail:
                        detail.append(boundary.daily_detail)
                        detail.append("Connect another free source, or add credit at that provider")
                    if boundary.retry_after_s is not None:
                        detail.append(f"retry after {boundary.retry_after_s:g}s")
                    break
            parts.append(f"{item.ref.model_id or 'default model'} on "
                         f"{item.ref.connection_id} ({', '.join(detail)})")
        message = WorkModelExhaustedError.MESSAGE
        if parts:
            message += ": " + "; ".join(parts)
        return WorkModelExhaustedError(message)

    def next_candidate(self, policy, exhaustion=(), *, min_context=None):
        """The next admitted ref, or None.

        ``min_context`` is ONE agent turn's measured need (its own pre-send
        overflow), applied to this call only: other nodes of the same run keep
        the models whose windows fit THEIR context. It raises the minimum on the
        plan's interaction and on every per-source policy, because a per-source
        interaction replaces the plan's for that source's models.
        """
        with self._lock:
            if self._fitted is None or policy_key(policy) not in self._fitted:
                raise PermissionError("work model order was not admitted")
            for item in exhaustion:
                if item not in self._exhaustion:
                    self._exhaustion += (item,)
            refs = self._fitted[policy_key(policy)]
            interaction, source_policies = self.interaction, self.source_policies
            if min_context is not None:
                interaction = _at_least(interaction, min_context)
                source_policies = tuple(
                    replace(item, interaction=_at_least(item.interaction, min_context))
                    for item in source_policies
                )
            # This is a DATA filter over retained capacity identity, not discovery
            # or fresh authority. It cannot throw for a now-absent old source.
            ordered = order_models(
                self.catalog, ModelPolicy(0, "explicit", refs[1:], saved_default=refs[0]),
                interaction, owner_id=self.owner, universe_id=self.universe,
                exhaustion=self._exhaustion, source_policies=source_policies,
            )
            return ordered.candidates[0].ref if ordered.candidates else None


def _at_least(interaction, tokens):
    """The interaction with its minimum context raised to ``tokens``, never lowered."""
    if interaction.min_context is not None and interaction.min_context >= tokens:
        return interaction
    return replace(interaction, min_context=tokens)

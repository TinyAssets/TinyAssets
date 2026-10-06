"""Shared agent progress; the injected adapter owns admission and identity.

A journal is progress, never authority or permission to replay actions.
Selected native agents and engine-managed inference share one finite coordinator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import replace

from tinyassets.engine_steering import session_of, turn_of
from tinyassets.engine_tool_client import EngineToolError, open_engine_tools
from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    ProviderAuthorityHeldError,
    ProviderProtocolError,
    SelectedModelContextError,
)
from tinyassets.providers import agent_chat_codec as codec
from tinyassets.providers.agent_capacity_boundary import (
    capacity_boundary,
    uniform_pre_generation_failure,
)
from tinyassets.providers.agent_inference import AgentInferenceRequest
from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.model_capacity import (
    MAX_FREE_SIBLING_RETRIES as _MAX_FREE_SIBLING_RETRIES,
)
from tinyassets.providers.native_agent_input import render_native_input
from tinyassets.request_budget import (
    FREE_TURN_ATTEMPTS,
    TEXT_TURN_ATTEMPTS,
    RequestBudgetExceeded,
    TurnRequestBudget,
    candidate_is_metered_free,
    current_request_budget,
    metered_free_source,
    pooled_budget,
    request_budget_scope,
)
from tinyassets.served_tools import granted_tools
from tinyassets.storage.agent_native_records import NativeInput, NativeTerminal
from tinyassets.storage.agent_turn_boot import BOOT
from tinyassets.storage.agent_turn_journal import AgentTurnJournal, JournalUnavailable
from tinyassets.storage.agent_turn_records import load_result
from tinyassets.turn_interrupt import TurnInterrupted

_LOG = logging.getLogger(__name__)


def _at_least(interaction, tokens):
    """The interaction with its minimum context raised to ``tokens``, never lowered."""
    if interaction.min_context is not None and interaction.min_context >= tokens:
        return interaction
    return replace(interaction, min_context=tokens)


def turn_effects(turn):
    """What a turn's own ledger proves ran: ``(effects, stage, ref)``.

    ``none`` only when no tool started and no native agent launched; ``some``
    once any tool completed; ``unknown`` for anything in flight or indeterminate.
    ``stage`` is ``tool`` only when the last recorded step was a tool that did
    not complete. Read from the journal, never guessed.

    A free function over a snapshot, not a method over the running coordinator:
    the startup reconciliation answers the SAME question about a turn no process
    is running any more, and a second implementation of it would be a second
    definition of what the ledger proves.
    """
    if turn is None:
        return "none", None, None
    effects, stage = "none", None
    for position, previous in enumerate(turn.rounds):
        last = position == len(turn.rounds) - 1
        if type(previous.candidate) is NativeInput:
            if not (type(previous.reply) is NativeTerminal
                    and previous.reply.status == "capacity_no_effects"):
                effects = "some" if effects == "some" else "unknown"
            continue
        for tool in previous.tools:
            if tool.state == "completed":
                effects = "some"
            elif tool.state in {"started", "unknown"} and effects == "none":
                effects = "unknown"
            if last and tool.state in {"started", "unknown", "not_sent"}:
                stage = "tool"
    return effects, stage, turn.turn_id


class AgentTurnCoordinator:
    """One in-process turn; no crash resurrection or automatic effect replay."""

    #: (failed selection, boundary) of a source whose cooldown the capacity path
    #: withheld to try a sibling on it; cooled when the turn leaves it.
    _hot_capacity = None

    def __init__(self, *, adapter, router, prompt, system, universe_context, config,
                 interrupt=None):
        self.adapter = adapter
        #: The owner's stop request for an interactive turn
        #: (:class:`tinyassets.turn_interrupt.LiveTurn`), or ``None``. A workflow
        #: or automation turn never has one, so nothing can stop it from here.
        self.interrupt = interrupt
        self.router = router
        self.prompt = prompt
        self.system = system
        self.inference_system = system
        self.context = universe_context
        self.config = config
        self.journal = None
        self.turn = None
        self.owner = None
        self.plan = universe_context.agent_model_plan
        if self.plan is not None and type(self.plan) is not AgentModelPlan:
            raise ValueError("invalid interactive candidate plan")
        self.exhaustion = ()
        self.capacity_recovery = False
        self.capacity_switch = None
        self.retrying_capacity = False
        self.visited = set()
        self.execution_kind = None
        self.native_input = None
        # Narrowed sibling retries used so far, and the diagnostics of the
        # rounds they replaced -- a turn that tried four models must not report
        # one attempt (live 2026-09-25 read "attempts=1" for a dead end).
        self.free_sibling_retries = 0
        self.spent_attempts = []
        # Replies that failed in flight (in-band source error, unreadable
        # body) retried so far this turn, and the models already given their
        # one same-model retry. See ``_next_after_bad_reply``.
        self.bad_reply_retries = 0
        self.bad_reply_models = set()
        # How hard the rendered history is compacted to fit a small window;
        # 0 renders every completed result whole. See ``_compact_to_fit``.
        self.compaction = 0
        self.request_budget = config.request_budget or current_request_budget()
        self._owns_request_budget = self.request_budget is None
        self._free_request = False
        self._budget_skipped = set()
        self._text_only = (
            type(config.agent_request) is AgentInferenceRequest and config.agent_request.text_only
        )

    def _remaining(self, turn_deadline):
        """This turn's config with its absolute cap cut to what is left of the turn.

        Never zero or negative: the profile would read that as "unset" and hand
        back the full default cap, which is the bug this exists to prevent.
        """
        left = max(turn_deadline - time.monotonic(), 1.0)
        return replace(self.config, absolute_cap_s=left)

    def _check_scope(self):
        owner = self.adapter.check(self.context, self.config)
        if self.owner is not None and owner != self.owner:
            raise ProviderAuthorityHeldError("interactive agent owner changed")
        return owner

    def _has_candidate_order(self):
        return self.plan is not None or bool(getattr(self.adapter, "has_candidate_order", False))

    def _next_candidate(self):
        if getattr(self.adapter, "has_candidate_order", False):
            return self.adapter.next_candidate(
                self.owner, self.context.universe_dir.name, self.exhaustion,
            )
        order_fn = self.plan.capacity_order if self.capacity_recovery else self.plan.order
        order = order_fn(self.owner, self.context.universe_dir.name, self.exhaustion)
        return next((item.ref for item in order.candidates
                     if item.ref not in self._budget_skipped
                     and (not self._text_only or self.router.selected_agent_execution_kind(
                         item.ref,
                     ) == "engine_inference")), None)

    def _accept(self, transition):
        if transition.status != "applied":
            raise JournalUnavailable("agent progress changed; action was not replayed")
        self.turn = transition.snapshot

    def _begin(self, authority, reservation, config):
        candidate = self.adapter.round_input(
            authority, reservation, config, owner=self.owner, context=self.context,
            prompt=self.prompt, system=self.inference_system,
            native_input=None, kind="engine_inference",
        )
        self._free_request = metered_free_source(
            self.context, config.selected_model, owner=self.owner,
        )
        self._accept(self.journal.begin_round(
            self.owner, self.context.universe_dir.name, self.turn.turn_id,
            expected_generation=self.turn.generation, candidate=candidate,
            after_failed_inference=self.retrying_capacity,
        ))
        self.retrying_capacity = False
        self._note_round()

    def _note_round(self):
        """Tell the status surface which step and model this turn is waiting on."""
        try:
            BOOT.note_round(
                self.context.universe_dir.name, self.turn.turn_id,
                round=len(self.turn.rounds),
                model=getattr(self.context.model_selection, "model_id", "") or "",
            )
        except Exception:  # noqa: BLE001 - a status hint never fails a turn
            _LOG.warning("could not note agent turn progress")

    def _history(self):
        history = []
        for previous in self.turn.rounds:
            if type(previous.candidate) is NativeInput:
                if (type(previous.reply) is NativeTerminal
                        and previous.reply.status == "capacity_no_effects"):
                    continue
                raise JournalUnavailable("native continuation is incomplete")
            if previous.state == "failed":
                continue
            if (
                previous.reply is None
                or previous.reply.stop != "tool_requests"
                or any(
                    tool.state != "completed" or tool.content_kind != "text_only"
                    for tool in previous.tools
                )
            ):
                raise JournalUnavailable("agent continuation is incomplete")
            outcomes = tuple(
                codec.tool_outcome(
                    tool.request,
                    load_result(tool.result_json)[0],
                )
                for tool in previous.tools
            )
            history.append(
                codec.CapturedToolRound(
                    round=codec.ToolRound(previous.reply, outcomes),
                    tools=json.loads(previous.candidate.tools_json)["tools"],
                )
            )
        return tuple(history)

    def _begin_native(self, authority, reservation, config):
        candidate = self.adapter.round_input(
            authority, reservation, config, owner=self.owner, context=self.context,
            prompt=self.prompt, system=self.system, native_input=self.native_input,
            kind="native_agent",
        )
        self._accept(self.journal.begin_round(
            self.owner, self.context.universe_dir.name, self.turn.turn_id,
            expected_generation=self.turn.generation, candidate=candidate,
            after_failed_inference=self.retrying_capacity,
        ))
        self.retrying_capacity = False
        self._note_round()

    def _finish_native_failure(self, exc):
        terminal = NativeTerminal("indeterminate")
        if isinstance(exc, AllProvidersExhaustedError):
            proofs = getattr(exc, "native_evidence", ())
            boundary = capacity_boundary(
                self.context.model_selection, exc.attempts,
                execution_kind="native_agent", native_evidence=proofs,
            )
            # One claimed native intent corresponds to exactly one executed
            # provider attempt. Never compress several unknown launches to one proof.
            if boundary is not None and boundary.attempted and len(proofs) == 1:
                terminal = NativeTerminal(
                    "capacity_no_effects", evidence=proofs[0],
                    failure_class=boundary.failure_class,
                    capacity_scope=boundary.exhaustion.scope,
                    retry_after_s=boundary.retry_after_s,
                )
        self._accept(self.journal.finish_native(
            self.owner, self.context.universe_dir.name, self.turn.turn_id,
            expected_generation=self.turn.generation, ordinal=len(self.turn.rounds),
            terminal=terminal,
        ))

    def _learn_verified_model(self, response):
        """Record a model id that just answered, for every universe on this KIND.

        Only reached from a committed success. It records the id on THIS OWNER's own
        list and nowhere else -- there is no shared store any more, so nothing here can
        reach another user. See ``tinyassets/storage/learned_models.py``; sharing is a
        reviewed file per source kind (``models/``), merged by a person.

        The id recorded is the one THIS UNIVERSE ASKED FOR and that then succeeded
        -- its own ``model_selection.model_id`` -- and never a string the source
        chose.

        The first version preferred ``response.reported_model``, and Codex refuted
        it on #4028: that field is source-controlled, so a source could publish
        anything to every other user of its kind (it reproduced
        ``owner-alice@example.com-private-9``), and ``codex_provider`` deliberately
        reports the literal ``provider-default`` when it cannot resolve a model,
        which would then have been published as a verified model id. What this
        universe REQUESTED is the only id worth sharing: it is a name its owner
        already held, it is exactly what another owner would need to grant, and a
        source cannot inject it.

        An empty requested id is the provider default -- a position, not a model --
        so there is nothing to teach anyone and it is skipped.

        Recording is not publishing, and here there is no publishing at all: a typed
        id is PERSONAL forever (founder, 2026-09-26). That is what keeps a private
        account-bearing selector on its own owner's list and nowhere else. Ids reach
        everyone by a different route entirely -- a reviewed file in the repo.
        """
        from tinyassets.storage.learned_models import (
            LEARNED_SOURCE_KIND,
            record_verified_model,
        )

        selection = getattr(self.context, "model_selection", None)
        model_id = (getattr(selection, "model_id", "") or "").strip()
        if not model_id:
            return
        # The OWNER, not the universe: the founder's threshold counts distinct
        # owners, so one person's two universes must not promote an id between
        # them. `self.owner` is the capability principal this turn ran under, which
        # is the same identity the journal scopes its rows by.
        record_verified_model(
            self.context.universe_dir.parent,
            source_kind=LEARNED_SOURCE_KIND,
            model_id=model_id,
            owner_user_id=self.owner,
        )

    def _remember_refusal(self, failed, attempts):
        """Keep a source's refusal of THIS model past this turn.

        The next turn's order puts it last instead of spending a request to be
        refused again (``storage.refused_models``; live 2026-09-28, the free
        account's 403 and withdrawn 404 models were rediscovered every turn).
        Recorded whether or not this turn finds another model: a refusal is a
        fact about the owner's key either way. Best-effort, never the turn's
        failure.
        """
        if failed is None or self.owner is None:
            return
        from tinyassets.storage.refused_models import record_refused_model

        record_refused_model(
            self.context.universe_dir.parent, owner_user_id=self.owner,
            connection_id=failed.connection_id, model_id=failed.model_id,
            failure_class="provider_refused",
            detail=str(getattr(attempts[-1], "detail", "") or "") if attempts else "",
        )

    def _forget_refusal(self):
        """The selected model just answered, so any standing refusal is stale."""
        selection = getattr(self.context, "model_selection", None)
        if selection is None or self.owner is None:
            return
        from tinyassets.storage.refused_models import clear_refused_model

        clear_refused_model(
            self.context.universe_dir.parent, owner_user_id=self.owner,
            connection_id=selection.connection_id, model_id=selection.model_id,
        )

    def effects_evidence(self):
        """This running turn's own ledger evidence; see :func:`turn_effects`."""
        return turn_effects(self.turn)

    def _release_turn(self):
        """This boot has stopped executing the turn, whatever state it reached.

        Deliberately not "the turn is terminal": a cancelled or timed-out task
        leaves a progressing row behind with nothing running it, and that row is
        exactly the one a status surface must stop painting as activity.
        """
        if self.turn is None:
            return
        try:
            BOOT.release(self.context.universe_dir.name, self.turn.turn_id)
        except Exception:  # noqa: BLE001 - bookkeeping never replaces the outcome
            _LOG.warning("could not release agent turn boot ownership")

    def _open_tools(self, timeout):
        """The turn's tool session: the adapter's own, else the engine route."""
        opener = getattr(self.adapter, "open_tools", None)
        if opener is not None:
            return opener(self, timeout=timeout)
        actor_id, graph_id = self.adapter.engine_identity(self.context, self.config)
        return open_engine_tools(
            actor_id=actor_id, graph_id=graph_id,
            enabled_tools=granted_tools(self.config), timeout=timeout,
            **self.steering(),
        )

    def steering(self):
        """The session and live turn the owner's mid-turn messages are bound to."""
        return {
            "session_key": session_of(self.config),
            "turn": getattr(self.interrupt, "live_id", "") or turn_of(),
        }

    def _interrupted(self):
        return self.interrupt is not None and self.interrupt.requested()

    def _requests_sent(self):
        """Model requests this turn sent, failed ones included: each one counts
        against a free tier's daily allowance, so the owner is told the number."""
        if self.request_budget is not None:
            return self.request_budget.receipt()["dispatched"]
        if self.turn is None:
            return 0
        return sum(1 for previous in self.turn.rounds
                   if type(previous.candidate) is not NativeInput)

    def _completed_tools(self):
        """Names of the tool calls this turn's ledger proves completed, in order."""
        if self.turn is None:
            return ()
        return tuple(
            tool.request.name
            for previous in self.turn.rounds
            for tool in previous.tools
            if tool.state == "completed"
        )

    def _stop_before_tool(self, uid, call_ordinal, tool):
        """Record a requested tool the owner's stop kept from running.

        ``not_sent``, the state the journal proves for a call it recorded as
        started and never dispatched -- the same two steps the startup
        reconciliation takes for a planned call (``agent_turn_reconcile``). The
        turn's frontier becomes ``held_tool_not_sent``: stopped, not working, and
        honest that nothing after this point ran.
        """
        self._accept(self.journal.start_tool(
            self.owner, uid, self.turn.turn_id, expected_generation=self.turn.generation,
            ordinal=len(self.turn.rounds), call_ordinal=call_ordinal,
        ))
        self._accept(self.journal.finish_tool(
            self.owner, uid, self.turn.turn_id, expected_generation=self.turn.generation,
            ordinal=len(self.turn.rounds), call_ordinal=call_ordinal,
            request=tool.request, failure="not_sent",
        ))
        raise TurnInterrupted("the owner stopped this turn before a tool call")

    async def run(self):
        owner = self._check_scope()
        if self.request_budget is None:
            self.request_budget = TurnRequestBudget(
                owner, self.context.universe_dir.name,
                free_limit=TEXT_TURN_ATTEMPTS if self._text_only else FREE_TURN_ATTEMPTS,
                free_pool_limit=TEXT_TURN_ATTEMPTS if self._text_only else FREE_TURN_ATTEMPTS,
            )
        self.request_budget.check_scope(owner, self.context.universe_dir.name)
        try:
            with request_budget_scope(
                self.request_budget, close_on_exit=self._owns_request_budget,
            ):
                result = await self._run()
                from tinyassets.extension_hooks import turn_event

                await turn_event(self, "turn_end", {"status": "completed"})
                return result
        except BaseException as exc:
            try:
                exc.turn_effects, exc.turn_stage, exc.turn_ref = self.effects_evidence()
                exc.turn_requests = self._requests_sent()
                exc.request_receipt = self.request_budget.receipt()
                if isinstance(exc, RequestBudgetExceeded):
                    exc.completed_tools = self._completed_tools()
                if isinstance(exc, TurnInterrupted):
                    exc.completed_tools = self._completed_tools()
                self._carry_spent_attempts(exc)
            except Exception:  # noqa: BLE001 - evidence never replaces the failure
                _LOG.warning("agent turn effects evidence unavailable")
            # A later pre-intent failure has no uncertain action to preserve.
            # Keep zero-round roots ready for the writer's one all-skipped retry.
            if self.turn is not None and self.turn.state == "ready" and self.turn.rounds:
                try:
                    self.close_quiescent()
                except Exception:
                    _LOG.exception("could not close settled interactive agent progress")
            raise
        finally:
            if self._owns_request_budget:
                self.request_budget.close()
            self._release_turn()

    def _daily_budget(self):
        """Refresh advisory evidence without manufacturing provider exhaustion.

        Installed caps do not identify this account's tier. In particular, a
        successful request beyond the free tier is how local evidence learns
        a larger allowance; stopping at that estimate prevents the correction.
        Actual capacity failures still use the existing exhaustion policy.
        """
        return pooled_budget(
            self.context.universe_dir.parent, self.owner, self.context,
            exhaustion=self.exhaustion,
        )

    async def _run(self):
        self.owner = self._check_scope()
        uid = self.context.universe_dir.name
        if self._has_candidate_order():
            first = self._next_candidate()
            if first is None:
                raise ProviderAuthorityHeldError("no eligible interactive model remains")
            if self.context.model_selection != first:
                raise ProviderAuthorityHeldError("interactive selection contradicts its plan")
            self._check_scope()
        if self.turn is None:
            self.journal = AgentTurnJournal(self.context.universe_dir.parent)
            self.turn = self.adapter.create_turn(
                self.journal, owner=self.owner, context=self.context,
                prompt=self.prompt, system=self.system, plan=self.plan,
            )
        elif self.turn.state != "ready" or self.turn.rounds:
            raise JournalUnavailable("agent turn cannot be replayed")

        self.request_budget.persist(self.context.universe_dir.parent)
        self.request_budget.link("turn", self.turn.turn_id)
        from tinyassets.extension_hooks import turn_event

        await turn_event(self, "input", {"input": self.prompt})
        await turn_event(self, "turn_start", {"turn_id": self.turn.turn_id})
        await turn_event(self, "context", {"system": self.system})
        timeout = self.config.stream_timeout_profile().absolute_cap_s
        # Every round is told what is LEFT of the turn, not the whole cap again:
        # a provider that cannot be cancelled mid-request (the HTTP broker) is
        # then bounded by the turn's own end, not by a fresh cap from a late
        # round (Codex, 2026-09-29).
        turn_deadline = time.monotonic() + timeout
        async with asyncio.timeout(timeout):
            async with AsyncExitStack() as stack:
                engine = None
                while True:
                    # Between rounds: the owner's stop ends the turn here, with
                    # every settled round and tool result kept as it is.
                    if self.interrupt is not None:
                        self.interrupt.check()
                    budget = self._daily_budget()
                    self.execution_kind = self.router.selected_agent_execution_kind(
                        self.context.model_selection,
                    )
                    if self._text_only and self.execution_kind != "engine_inference":
                        raise ProviderAuthorityHeldError(
                            "text-only mode requires an admitted text inference route"
                        )
                    if self.execution_kind == "engine_inference":
                        if engine is None and not self._text_only:
                            engine = await stack.enter_async_context(
                                self._open_tools(timeout),
                            )
                        config = replace(
                            self._remaining(turn_deadline),
                            agent_request=AgentInferenceRequest(
                                tools=(
                                    () if self._text_only else codec.tool_definitions(engine.tools)
                                ),
                                history=codec.compact_history(
                                    self._history(), self.compaction,
                                ),
                                tool_choice="none" if self._text_only else "auto",
                            ),
                        )
                        prompt, system, observer = self.prompt, self.system, self._begin
                        if budget is not None:
                            system += "\n\n" + budget.prompt_line()
                    else:
                        self.native_input = render_native_input(
                            self.prompt, self.system, self._history(),
                        )
                        prompt, system = self.native_input
                        config = replace(
                            self._remaining(turn_deadline), agent_request=None,
                            selected_model=None,
                        )
                        observer = self._begin_native
                    config = replace(
                        config, request_budget=self.request_budget,
                        request_purpose="tool_review" if self._completed_tools() else "reply",
                    )
                    self.inference_system = system
                    try:
                        inference = self.adapter.infer(
                            router=self.router, prompt=prompt, system=system, config=config,
                            context=self.context, observer=observer, kind=self.execution_kind,
                        )
                        if self.interrupt is not None and self.execution_kind == "native_agent":
                            # A native agent runs until it is done, and only
                            # cancelling it ends its process family; see
                            # tinyassets/turn_interrupt.py for why an HTTP round
                            # is left to return instead.
                            response = await self.interrupt.run(inference)
                        else:
                            response = await inference
                    except BaseException as exc:
                        # No engine tool can start before a validated inference
                        # is committed. Preserve failure, never restart this turn.
                        if self.turn.state == "native_started":
                            self._finish_native_failure(exc)
                        elif self.turn.state == "inference_started":
                            self._accept(
                                self.journal.finish_inference(
                                    self.owner,
                                    uid,
                                    self.turn.turn_id,
                                    expected_generation=self.turn.generation,
                                    ordinal=len(self.turn.rounds),
                                    reply=None,
                                )
                            )
                        if (
                            self._next_after_request_budget(exc)
                            or self._next_after_capacity(exc)
                            or self._next_after_signin(exc)
                            or self._next_after_refusal(exc)
                            or self._next_after_overflow(exc)
                        ):
                            continue
                        if self._next_after_bad_reply(exc):
                            await self._pause_before_retry(turn_deadline)
                            continue
                        raise
                    if self.execution_kind == "native_agent":
                        try:
                            if response.provider != self.context.model_selection.connection_id:
                                raise ProviderProtocolError("native response source changed")
                            terminal = NativeTerminal(
                                "completed", evidence=response.native_evidence,
                                text=response.text, configured_model=response.model,
                                reported_model=response.reported_model or None,
                                input_tokens=response.input_tokens,
                                output_tokens=response.output_tokens,
                            )
                            self._accept(self.journal.finish_native(
                                self.owner, uid, self.turn.turn_id,
                                expected_generation=self.turn.generation,
                                ordinal=len(self.turn.rounds), terminal=terminal,
                                cost_microusd=response.cost_microunits,
                            ))
                        except BaseException as exc:
                            if self.turn.state == "native_started":
                                self._finish_native_failure(exc)
                            raise
                        # The call SUCCEEDED and the journal has committed it, so
                        # this model id provably works on this kind of source.
                        # Learn it for every universe with that kind. After the
                        # commit and outside the try, so a catalog write can
                        # neither be mistaken for a turn failure nor rewrite one.
                        self._learn_verified_model(response)
                        return self._capacity_notice(response)
                    if response.agent_reply is None:
                        raise ProviderProtocolError("HTTP agent response lacks validated progress")
                    self._accept(
                        self.journal.finish_inference(
                            self.owner,
                            uid,
                            self.turn.turn_id,
                            expected_generation=self.turn.generation,
                            ordinal=len(self.turn.rounds),
                            reply=response.agent_reply,
                            cost_microusd=response.cost_microunits,
                        )
                    )
                    # The model answered: whatever refused it before does not now.
                    self._forget_refusal()
                    if self.turn.state == "completed":
                        return self._capacity_notice(response)
                    if self.turn.state != "tools_pending":
                        raise ProviderProtocolError(
                            "agent response requires attention: " + self.turn.state,
                        )
                    for call_ordinal, tool in enumerate(self.turn.rounds[-1].tools, 1):
                        if self._interrupted():
                            self._stop_before_tool(uid, call_ordinal, tool)
                        self._check_scope()
                        try:
                            self.request_budget.check_available(
                                source_ref=self.context.model_selection.connection_id,
                                free=self._free_request, purpose="tool_review",
                            )
                        except RequestBudgetExceeded as exc:
                            if (exc.reason not in RequestBudgetExceeded.SOURCE_LIMIT_REASONS
                                    or self._request_budget_fallback() is None):
                                # A held tool is never approved or silently resumed.
                                try:
                                    self._stop_before_tool(uid, call_ordinal, tool)
                                except TurnInterrupted:
                                    pass
                                raise
                        self._accept(
                            self.journal.start_tool(
                                self.owner,
                                uid,
                                self.turn.turn_id,
                                expected_generation=self.turn.generation,
                                ordinal=len(self.turn.rounds),
                                call_ordinal=call_ordinal,
                            )
                        )
                        try:
                            if getattr(engine, "takes_op_id", False):
                                # The journal position names the operation, so a
                                # lost reply is asked about, never re-run.
                                result = await engine.call(
                                    tool.request.name, tool.request.arguments(),
                                    op_id=f"{self.turn.turn_id}:{len(self.turn.rounds)}"
                                          f":{call_ordinal}",
                                )
                            else:
                                result = await engine.call(
                                    tool.request.name, tool.request.arguments(),
                                )
                        except BaseException as exc:
                            failure = (
                                "not_sent"
                                if (isinstance(exc, EngineToolError) and exc.outcome == "not_sent")
                                else "unknown"
                            )
                            self._accept(
                                self.journal.finish_tool(
                                    self.owner,
                                    uid,
                                    self.turn.turn_id,
                                    expected_generation=self.turn.generation,
                                    ordinal=len(self.turn.rounds),
                                    call_ordinal=call_ordinal,
                                    request=tool.request,
                                    failure=failure,
                                )
                            )
                            raise
                        self._accept(
                            self.journal.finish_tool(
                                self.owner,
                                uid,
                                self.turn.turn_id,
                                expected_generation=self.turn.generation,
                                ordinal=len(self.turn.rounds),
                                call_ordinal=call_ordinal,
                                request=tool.request,
                                result=result,
                            )
                        )
                        if self.turn.state not in {"ready", "tools_pending"}:
                            raise ProviderProtocolError("agent tool result requires attention")

    def _request_budget_fallback(self):
        # Do not manufacture remote Exhaustion records from a local allocation:
        # even model-scoped exhaustion can exclude another connection when its
        # provider account identity is unknown. Filter the accepted order only.
        if self.plan is None:
            fallback = getattr(self.adapter, "budget_fallback", None)
            return (fallback(self.owner, self.context.universe_dir.name, self.request_budget)
                    if fallback is not None else None)
        order = self.plan.order(self.owner, self.context.universe_dir.name, self.exhaustion)
        for item in order.candidates:
            candidate = item.ref
            if (candidate == self.context.model_selection or candidate in self.visited
                    or candidate in self._budget_skipped):
                continue
            if (self._text_only
                    and self.router.selected_agent_execution_kind(candidate) != "engine_inference"):
                continue
            limited = candidate_is_metered_free(
                replace(self.context, model_selection=candidate), self.plan.catalog,
                owner=self.owner,
            )
            try:
                self.request_budget.check_available(
                    source_ref=candidate.connection_id, free=limited,
                )
                return candidate
            except RequestBudgetExceeded as exc:
                if exc.reason not in RequestBudgetExceeded.SOURCE_LIMIT_REASONS:
                    return None
        return None

    def _next_after_request_budget(self, exc):
        # A local allocation is not a provider refusal and writes no cooldown.
        # The existing accepted order still controls explicit/automatic fallback.
        if (not isinstance(exc, RequestBudgetExceeded)
                or exc.reason not in RequestBudgetExceeded.SOURCE_LIMIT_REASONS
                or self.turn.state not in {"ready", "held_transport"}):
            return False
        fallback = self._request_budget_fallback()
        if fallback is None:
            return False
        candidate = fallback
        failed = self.context.model_selection
        self._budget_skipped.add(failed)
        self.visited.add(failed)
        self.context = replace(self.context, model_selection=candidate)
        self.retrying_capacity = self.turn.state != "ready"
        return True

    #: How many times one turn may narrow an UNPROVEN account exhaustion to the
    #: model that actually failed. Small on purpose: the narrowing is a policy
    #: bet that the source's window was per-model, and a bet re-taken without
    #: limit is just hammering. Three covers the live case (a busy free model
    #: with eligible siblings) without turning one message into a sweep.
    #:
    #: Imported, not re-declared: a workflow node acts on the same guess and
    #: must not get its own number (`providers.model_capacity`).
    MAX_FREE_SIBLING_RETRIES = _MAX_FREE_SIBLING_RETRIES

    def _narrowed(self, boundary):
        """Exclude only the failed MODEL when excluding the account is a guess.

        A source that reported the account, or a class that is not a passing
        window, keeps the conservative exhaustion. Any source with an
        ``unknown`` scope is narrowed, for every account alike, and only a
        bounded number of times per turn. The replacement comes from the SAME
        order under the SAME per-attempt ceilings, which is what bounds money.

        Engine inference only. A native executor runs on ONE subscription, so a
        rate limit there is a fact about that account, not about a model within
        it — and narrowing a source whose members are unmetered rather than
        free-per-model is a guess with nothing behind it.

        Returns ``(exhaustion, narrowed)``; ``narrowed`` tells the caller its
        next candidate rests on a guess and must stay inside the same grant.
        """
        if self.execution_kind != "engine_inference":
            return boundary.exhaustion, False
        if self.plan is None or self.free_sibling_retries >= self.MAX_FREE_SIBLING_RETRIES:
            return boundary.exhaustion, False
        if not self._free_source_refusal(boundary, window=True):
            return boundary.exhaustion, False
        self.free_sibling_retries += 1
        return replace(boundary.exhaustion, scope="model"), True

    def _free_source_refusal(self, boundary, *, window):
        """Is this the refusal whose cooldown the router withholds?

        Mirrors the router's capacity handler. Neither side reads the owner's
        ceilings any more (2026-09-25): the same refusal must mean the same thing
        for every account, and a price branch here made a paid source's 429 a
        dead end its free neighbour never hit. What still narrows this to
        engine-inference rounds is the EXECUTION KIND, a fact about the source —
        a native round runs on one subscription, so its account IS the source and
        the router already cooled it.

        ``window`` decides whether the source's own ``Retry-After`` may rule the
        sibling out. Deliberately asymmetric between the two callers:

        * choosing to TRY a sibling passes it, so a window longer than a turn
          does not buy a round the quota gate would skip anyway;
        * deciding to COOL afterwards does not, because the boundary reports the
          MAXIMUM delay across attempts while the router saw one signal. Erring
          toward cooling re-applies a window the router already set, which costs
          nothing; erring the other way leaves a capped source hot, which is the
          bug being fixed.
        """
        from tinyassets.providers.model_capacity import free_sibling_retry

        if self.plan is None or self.execution_kind != "engine_inference":
            return False
        return free_sibling_retry(
            scope=boundary.observed_scope, failure_class=boundary.failure_class,
            retry_after_s=boundary.retry_after_s if window else None,
            turn_budget_s=(
                self.config.stream_timeout_profile().absolute_cap_s if window else None
            ),
        )

    def _cool_abandoned_source(self, failed, boundary):
        """Cool a source the router left hot once no sibling attempt will follow.

        The withheld cooldown buys exactly one thing: another model on the same
        grant. When the budget is spent, or the order has no sibling left, that
        purchase is over and the source must be cooled — otherwise a source at a
        DAILY free cap, which refuses every model, has every turn pay the full
        budget of requests again, forever. Honours its own ``Retry-After``.

        Never raises: a failing turn must not be replaced by a cooling error.
        """
        try:
            if not self._free_source_refusal(boundary, window=False):
                return
            self.router.cool_source(
                failed.connection_id, owner=self.owner, retry_after_s=boundary.retry_after_s,
                reason=boundary.failure_class or "",
            )
        except Exception:  # noqa: BLE001 - cooling is hygiene, never the failure
            _LOG.warning("could not cool a spent free source")

    def _next_after_signin(self, exc):
        """Advance to the owner's next model when THIS source's sign-in is done.

        Only a CAPACITY exhaustion advanced the turn, so a source whose stored
        sign-in had expired ended it: the founder's subscription failed every turn
        from 2026-09-24 and each one stopped rather than answering on the next
        model they had allowed. A finished sign-in is as final for this turn as a
        spent quota and as fixable by moving on, and unlike a quota it will not
        clear on its own -- so excluding it was the harsher of the two.

        The exclusion the capacity path enforces is kept exactly: only candidates
        already in the owner's accepted order are reachable
        (``_next_candidate``), so this never widens authority. It advances only
        when EVERY attempt of the round was a sign-in refusal -- a round that also
        hit capacity is the capacity path's to reason about, with its own
        narrowing -- and never when a native attempt may have committed a side
        effect, which is the state ``_next_after_capacity`` fences too.
        """
        # The source is excluded for the REST OF THIS TURN by the same mechanism
        # capacity uses -- an ``account``-scoped exclusion, because a finished
        # sign-in is the whole connection's, never one model's. It is separately
        # marked for reconnect by the router, so the owner's next turn does not
        # start here either.
        return self._advance_past(exc, "auth_invalid", "account")

    def _next_after_refusal(self, exc):
        """Advance to the owner's next model when the source refused THIS one.

        HTTP 403/404/410 on an inference request (``provider_refused``): access
        to this model was refused, or the source no longer serves it. Live
        2026-09-28 on the free-only account: its first free model was rate
        limited, the second had been withdrawn from the catalog, and the third
        answered 403 -- and the turn died there with free models still in the
        owner's order. Same fences as a finished sign-in (every attempt of the
        round refused, none may have acted, only candidates already in the
        owner's accepted order) with a MODEL-scoped exclusion: the refusal names
        the model, and its siblings on the same connection stay eligible.

        Bounded by the owner's list, not a count: each accepted model is tried
        at most once per turn (``visited`` plus the exclusion), so a pool with
        several dead models in a row is walked to the end, and a key refused for
        every model costs one request per accepted model, once.
        """
        return self._advance_past(exc, "provider_refused", "model")

    def _next_after_overflow(self, exc):
        """Move to an accepted model whose window fits, when this one's does not.

        Our own pre-send measurement (``SelectedModelContextError``), so nothing
        was sent, spent or run, and no round was opened. Live 2026-09-26 a large
        tool result overflowed a 262k-token model while the owner's order held a
        1M-token free model. The order is re-asked with the measured size as its
        minimum context, so every model too small is skipped in one step rather
        than tried one by one; the failed model is excluded too.

        A workflow agent node's candidates come from its adapter: the measured
        need is handed to the adapter for this turn only, and the next model
        launches through the adapter's own fresh authorization, exactly as the
        capacity and refusal paths already move a workflow turn on.
        """
        if (
            not self._has_candidate_order()
            or not isinstance(exc, SelectedModelContextError)
            or self.turn.state not in {"ready", "held_transport"}
        ):
            return False
        needed = exc.required_tokens
        if type(needed) is not int or needed < 1:
            return False
        from tinyassets.providers.model_policy import Exhaustion

        failed = self.context.model_selection
        self.visited.add(failed)
        # Kept to stay on THIS model if no larger one fits (``_compact_to_fit``).
        before = (self.exhaustion, self.plan, getattr(self.adapter, "min_context", None))
        if self.plan is None:
            # The work adapter raises every interaction its order reads, for
            # THIS turn. No Exhaustion: a work run's exhaustion is shared by all
            # its nodes, and a model too small for this node's context is not
            # exhausted for a later, smaller one (gpt-6-astra on #4093). The
            # measured minimum already rules the failed model out here -- its
            # window is exactly what the measurement exceeded.
            self.adapter.require_context(needed)
        else:
            self.exhaustion = self.exhaustion + (Exhaustion("model", failed),)
            # Every interaction the order reads: a per-source policy REPLACES the
            # plan's own for that source's models, and production plans carry one
            # per source -- raising only the plan's left them admitting a model too
            # small (Codex, 2026-09-28).
            self.plan = replace(
                self.plan,
                interaction=_at_least(self.plan.interaction, needed),
                source_policies=tuple(
                    replace(item, interaction=_at_least(item.interaction, needed))
                    for item in self.plan.source_policies
                ),
            )
        candidate = self._next_candidate()
        if candidate is None or candidate in self.visited:
            self.exhaustion, self.plan = before[0], before[1]
            if self.plan is None and hasattr(self.adapter, "min_context"):
                self.adapter.min_context = before[2]
            if self._compact_to_fit():
                return True
            self._leave_hot_source(None)
            return False
        self._leave_hot_source(candidate)
        self.context = replace(self.context, model_selection=candidate)
        self.retrying_capacity = self.turn.state != "ready"
        return True

    #: Compaction levels: (rounds kept whole at the end, result characters,
    #: argument-string characters) for every older round. Level 3 trims every
    #: round, the last too. Three is the number of steps a small window needs to
    #: drop from "a long build" to "the latest work, and the head and tail of the rest".
    COMPACTION_LEVELS = codec.COMPACTION_LEVELS

    def _compact_to_fit(self):
        """Stay on this model and render older tool results shorter.

        Only when no accepted model with a larger window exists (the caller
        tried that first), and only while a stronger level still shrinks what
        is sent. The journal keeps every result whole: this changes what the
        model is SHOWN, never what ran or what is recorded, and a trimmed result
        says so and that calling the tool again returns it in full.

        Live 2026-09-26 (turn 8dc8ada5) a free model's turn died after four
        tool rounds with nothing larger in the owner's order.
        """
        if not getattr(self.adapter, "relaunches_same_model", False):
            return False
        history = self._history()
        current = codec.compact_history(history, self.compaction)
        for level in range(self.compaction + 1, len(self.COMPACTION_LEVELS) + 1):
            if codec.history_size(codec.compact_history(history, level)) < codec.history_size(
                current,
            ):
                self.compaction = level
                self.retrying_capacity = self.turn.state != "ready"
                return True
        return False

    #: Classes of a reply that FAILED in flight: the source reported an error in
    #: place of a reply, sent one we could not read, or its stream stopped
    #: arriving. Never a reply that is merely slow: a streamed reply that keeps
    #: arriving is not cut, and a non-streamed one that outruns the broker's
    #: ceiling (``provider_reply_timeout``) is NOT retried or moved off -- on a
    #: capped free tier, re-asking a slow model spends another of the day's
    #: requests on the same slowness (founder, 2026-10-02).
    BAD_REPLY_CLASSES = frozenset({
        "provider_reply_error", "provider_unreadable_reply", "provider_stalled",
    })
    #: Per-turn bound on those retries, across every model. Two -- this model
    #: once, then at most one other accepted model -- because every request
    #: counts against a free tier's daily allowance, and the notice says how
    #: many the turn used.
    MAX_BAD_REPLY_RETRIES = 2
    #: Seconds before each retry; an upstream error is usually a moment's.
    BAD_REPLY_BACKOFF_S = (2.0, 5.0, 10.0)

    def _next_after_bad_reply(self, exc):
        """Retry a reply that failed in flight: this model once, then the next one.

        Live 2026-09-30 and 2026-10-02, the free-only account: nemotron served
        7-19 good tool rounds of a build, then OpenRouter answered HTTP 200 with
        an error object in place of the reply, and the turn ended there -- every
        model in the order had already been visited, so only a retry of the SAME
        model could have saved it.

        Safe to repeat because a failed engine inference ran nothing: a tool is
        dispatched only from a reply the journal accepted, and the history the
        retry renders is the journal's completed rounds -- no tool is re-run.
        What a retry can cost is a second generation, so each one is a fresh
        request under the same per-attempt ceilings and the turn takes at most
        ``MAX_BAD_REPLY_RETRIES`` of them. The NEXT model comes only from the
        owner's accepted order, model-scoped (the reply is one model's), so this
        never widens authority. Native agents are out of scope: their failures
        can follow real work.
        """
        if (
            self.execution_kind != "engine_inference"
            or not getattr(self.adapter, "relaunches_same_model", False)
            or not isinstance(exc, AllProvidersExhaustedError)
            or self.turn.state not in {"ready", "held_transport"}
            or self.bad_reply_retries >= self.MAX_BAD_REPLY_RETRIES
        ):
            return False
        attempts = tuple(exc.attempts or ())
        if not attempts or any(a.failure_class not in self.BAD_REPLY_CLASSES for a in attempts):
            return False
        failed = self.context.model_selection
        if failed in self.bad_reply_models:
            if not self._has_candidate_order():
                return False
            from tinyassets.providers.model_policy import Exhaustion

            self.visited.add(failed)
            self.exhaustion = self.exhaustion + (Exhaustion("model", failed),)
            candidate = self._next_candidate()
            if candidate is None or candidate in self.visited:
                # Ending exactly as a plain failure would: a slow or broken
                # reply is no reason to cool a source the owner retries next.
                return False
            self._leave_hot_source(candidate)
            self.context = replace(self.context, model_selection=candidate)
        self.bad_reply_models.add(failed)
        self.bad_reply_retries += 1
        self.spent_attempts += list(attempts)
        self.retrying_capacity = self.turn.state != "ready"
        return True

    async def _pause_before_retry(self, turn_deadline):
        """A short, growing wait before a bad-reply retry, inside the turn's time.

        Polled, so the owner's Stop is answered within a quarter second rather
        than after the whole wait; the loop's own check then ends the turn.
        """
        index = min(self.bad_reply_retries, len(self.BAD_REPLY_BACKOFF_S)) - 1
        until = time.monotonic() + min(
            self.BAD_REPLY_BACKOFF_S[index], max(turn_deadline - time.monotonic(), 0.0),
        )
        while not self._interrupted() and time.monotonic() < until:
            await asyncio.sleep(min(0.25, max(until - time.monotonic(), 0.0)))

    def _advance_past(self, exc, failure_class, scope):
        """Exclude the failed selection at ``scope`` and take the next candidate.

        Advances only when EVERY attempt of the round carried ``failure_class``
        -- a round that also hit capacity is the capacity path's to reason about
        -- and never when an attempt may have committed a side effect. Only
        candidates already in the owner's accepted order are reachable
        (``_next_candidate``), so this never widens authority.
        """
        if (
            not self._has_candidate_order()
            or not isinstance(exc, AllProvidersExhaustedError)
            or self.turn.state not in {"ready", "held_transport", "held_native_capacity"}
        ):
            return False
        attempts = tuple(exc.attempts or ())
        # Shared with the workflow run's loop, so a refusal means the same thing
        # on both surfaces. A round that may have acted is not replayable on
        # another model; the turn's own held state is the honest answer.
        if not uniform_pre_generation_failure(attempts, failure_class):
            return False
        failed = self.context.model_selection
        self.visited.add(failed)
        if failure_class == "provider_refused":
            self._remember_refusal(failed, attempts)
        from tinyassets.providers.model_policy import Exhaustion

        self.exhaustion = self.exhaustion + (Exhaustion(scope, failed),)
        candidate = self._next_candidate()
        if candidate is None or candidate in self.visited:
            self._leave_hot_source(None)
            return False
        self._leave_hot_source(candidate)
        if self.execution_kind == "engine_inference":
            self.spent_attempts += list(attempts)
        self.context = replace(self.context, model_selection=candidate)
        self.retrying_capacity = self.turn.state != "ready"
        return True

    def _leave_hot_source(self, candidate):
        """Cool the source a capacity sibling retry left hot, once the turn leaves it.

        The capacity path withholds a source's cooldown only while the next try
        is a sibling on that same source. A refusal or sign-in step that then
        moves to ANOTHER source would otherwise leave it hot: 429 -> sibling ->
        403 -> another source, and the capped source is asked again next turn
        (Codex, 2026-09-28). ``None`` means the turn is ending: leave it too.
        """
        hot = self._hot_capacity
        if hot is None or (
            candidate is not None and candidate.connection_id == hot[0].connection_id
        ):
            return
        self._hot_capacity = None
        self._cool_abandoned_source(*hot)

    def _next_after_capacity(self, exc):
        if (
            not self._has_candidate_order() or not isinstance(exc, AllProvidersExhaustedError)
            or self.turn.state not in {"ready", "held_transport", "held_native_capacity"}
        ):
            return False
        boundary = capacity_boundary(
            self.context.model_selection, exc.attempts, execution_kind=self.execution_kind,
            native_evidence=(getattr(exc, "native_evidence", ())
                             if self.execution_kind == "native_agent" else ()),
        )
        if boundary is None:
            return False
        # Only a validated, replay-safe capacity boundary can extend a chat's
        # preference order. Work adapters retain their admitted graph order.
        if self.plan is not None:
            self.capacity_recovery = True
        failed = self.context.model_selection
        self.visited.add(failed)
        base = self.exhaustion
        narrowed_exhaustion, narrowed = self._narrowed(boundary)
        candidate = None
        if narrowed:
            self.exhaustion = base + (narrowed_exhaustion,)
            candidate = self._next_candidate()
            # A narrowed exhaustion is a guess about ONE source's window, never
            # evidence that a different connection sharing its scope is healthy
            # — deciding THAT is exactly what the conservative account exclusion
            # does. So a narrowed candidate must be a sibling on the same grant;
            # anything else falls back to the unnarrowed exclusion and asks
            # again, which is what was already allowed. Narrowing may only ever
            # add a candidate, never remove one.
            if candidate is None or candidate.connection_id != failed.connection_id:
                candidate, narrowed = None, False
                self.free_sibling_retries -= 1
        if not narrowed:
            self.exhaustion = base + (boundary.exhaustion,)
            candidate = self._next_candidate()
        if candidate is None or candidate in self.visited:
            self._cool_abandoned_source(failed, boundary)
            return False
        if candidate.connection_id != failed.connection_id:
            if self.plan is not None and self.capacity_switch is None:
                delay = boundary.cooling_s or boundary.retry_after_s
                self.capacity_switch = (failed.connection_id,
                                        None if delay is None else time.time() + delay)
            # Moving to another source: this one is done for the turn, so the
            # cooldown the router withheld for it now applies.
            self._cool_abandoned_source(failed, boundary)
            self._leave_hot_source(candidate)
        elif self._free_source_refusal(boundary, window=False):
            # Staying on it for a sibling: its cooldown stays withheld until the
            # turn leaves the source by any path.
            self._hot_capacity = (failed, boundary)
        # Only engine-inference rounds. A native round's diagnostics are paired
        # positionally with its own ``native_evidence``, and carrying them onto
        # a later exception would leave the two lists mismatched, which
        # ``capacity_boundary`` correctly refuses to read.
        if self.execution_kind == "engine_inference":
            self.spent_attempts += list(exc.attempts or ())
        self.context = replace(self.context, model_selection=candidate)
        self.retrying_capacity = self.turn.state != "ready"
        return True

    def _capacity_notice(self, response):
        """One local notice after the final reply; never provider-authored facts."""
        if self.capacity_switch is None:
            return response
        from datetime import datetime, timezone

        original, reset_at = self.capacity_switch
        reset = ("The original source did not report a reset time." if reset_at is None else
                 "The original source can be retried after "
                 + datetime.fromtimestamp(reset_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                 + ".")
        notice = (f"Answered by {response.provider_display or response.provider} because "
                  f"{original} is cooling down or out of capacity. {reset}")
        return replace(response, text=response.text + "\n\n" + notice)

    def _carry_spent_attempts(self, exc):
        """Prepend the replaced rounds' diagnostics to the failure that escapes.

        Only the last round's exception propagates, so without this a turn that
        tried four models reports one attempt -- and the owner's failure record
        and the server log both describe a dead end that never happened.
        """
        if not self.spent_attempts or not isinstance(exc, AllProvidersExhaustedError):
            return
        attempts = list(exc.attempts or ())
        evidence = getattr(exc, "native_evidence", ())
        # ``native_evidence`` is positional against ``attempts``; a pairing this
        # hop does not recognize is left alone rather than repaired blind.
        if type(evidence) is not tuple or len(evidence) != len(attempts):
            return
        # Every carried round was engine inference, which has no native proof.
        exc.attempts = self.spent_attempts + attempts
        exc.native_evidence = (None,) * len(self.spent_attempts) + evidence

    def close_quiescent(self):
        if self.turn is not None and self.turn.state == "ready":
            self._accept(
                self.journal.abandon(
                    self.owner,
                    self.context.universe_dir.name,
                    self.turn.turn_id,
                    expected_generation=self.turn.generation,
                )
            )

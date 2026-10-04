"""Served-chat authority adapter for the shared agent progress coordinator."""

from __future__ import annotations

import hashlib
import json

from tinyassets.agent_turn_coordinator import AgentTurnCoordinator
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.provider_assignment import check_served_agent_tool_authority
from tinyassets.storage.agent_native_records import NativeInput
from tinyassets.storage.agent_turn_records import RoundInput, dump


class ServedChatAgentAdapter:
    """Chat retains a current served request; this adapter grants no work authority."""

    #: Every round admits afresh through ``router.call`` under the served
    #: authority, so a failed round can be asked again of the SAME model. A
    #: workflow node cannot: its failed round settles the one launch carrier it
    #: holds (Codex, 2026-10-02), so the coordinator's same-model retries and
    #: compaction are offered only to an adapter that says this.
    relaunches_same_model = True

    def check(self, context, config):
        owner = check_served_agent_tool_authority(context)
        from tinyassets.providers.agent_inference import AgentInferenceRequest

        if type(config.agent_request) is AgentInferenceRequest and config.agent_request.text_only:
            # The same fresh owner/binding fence above applies, with no tool
            # identity or engine session required for an empty inventory.
            return owner
        if (
            not config.engine_mcp_enabled
            or config.engine_mcp_actor_id != owner
            or config.engine_mcp_graph_id != context.universe_dir.name
        ):
            raise ProviderAuthorityHeldError("interactive engine tool identity changed")
        return owner

    def engine_identity(self, context, config):
        # The coordinator has checked this immutable configuration. Admission
        # remains in infer(), preserving the router's typed refusal on fallback.
        return config.engine_mcp_actor_id, config.engine_mcp_graph_id

    def create_turn(self, journal, *, owner, context, prompt, system, plan):
        # ``agent_id`` names which of the owner's agents ran the turn (harness
        # §4.18). The journal column has been per-agent since #4228; this
        # adapter left it at its ``main`` default, so every served turn --
        # including a custom agent's -- was recorded as main, and the per-agent
        # journal, status projection and history all read the wrong agent.
        #
        # Taken from the context, which authenticated ingress set, and from
        # nowhere else: not turn_interrupt.current() (absent on a workflow-node
        # turn, so attribution would depend on whether a Stop was registrable)
        # and not the session key (which cannot establish an identity).
        return journal.create(
            owner, context.universe_dir.name, prompt=prompt, system=system,
            policy_generation=None if plan is None else plan.policy.generation,
            policy_source="unknown" if plan is None else plan.policy_source,
            agent_id=context.agent_id,
        )

    async def infer(self, *, router, prompt, system, config, context, observer, kind):
        return await router.call(
            "writer", prompt, system, config, operation="converse",
            universe_context=context, _agent_observer=observer, _agent_execution_kind=kind,
        )

    def round_input(self, authority, reservation, config, *, owner, context,
                    prompt, system, native_input, kind):
        if kind == "native_agent":
            if (authority.owner_user_id != owner
                    or authority.universe_id != context.universe_dir.name
                    or authority.provider != context.model_selection.connection_id
                    or authority.selected_model is not None or native_input is None
                    or config.agent_request is not None):
                raise ProviderAuthorityHeldError("native agent scope changed")
            native_prompt, native_system = native_input
            return NativeInput(
                authority.provider, context.model_selection.model_id,
                authority.binding_id, reservation.reservation_id, authority.binding_generation,
                authority.binding_digest,
                "sha256:" + hashlib.sha256(
                    dump({"prompt": native_prompt, "system": native_system}).encode("utf-8"),
                ).hexdigest(),
            )
        if (kind != "engine_inference" or config.agent_request is None
                or authority.owner_user_id != owner
                or authority.universe_id != context.universe_dir.name):
            raise ProviderAuthorityHeldError("agent inference scope changed")
        request = config.agent_request
        _, body = request.encode(
            prompt=prompt, system=system, selection=authority.selected_model,
            temperature=config.temperature, max_tokens=config.max_tokens,
        )
        return RoundInput(
            authority.provider, authority.selected_model.model_id,
            dump({"version": 1, "tools": request.tools()}),
            authority.binding_id, reservation.reservation_id, authority.binding_generation,
            authority.binding_digest,
            "sha256:" + hashlib.sha256(json.dumps(body).encode("utf-8")).hexdigest(),
        )


class InteractiveHttpAgentTurn(AgentTurnCoordinator):
    """Compatibility entry point for the ordinary served-chat provider bridge."""

    def __init__(self, *, router, prompt, system, universe_context, config, adapter=None):
        from tinyassets.turn_interrupt import current

        # The served handler registered this turn under its verified caller;
        # only that caller's stop request can reach it (tinyassets/turn_interrupt).
        super().__init__(
            adapter=adapter or ServedChatAgentAdapter(), router=router, prompt=prompt,
            system=system, universe_context=universe_context, config=config,
            interrupt=current(),
        )

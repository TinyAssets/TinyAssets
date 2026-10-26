"""Serve HTTP chat on the owner loop with local tools and a turn-bound ta bridge.

The coordinator keeps the journal; open_loop_tools supplies OwnerTools.
Chat and workflow both construct that coordinator through ``start_coordinator``.
"""

from __future__ import annotations

import logging

from tinyassets.agent_loop.tool_session import open_loop_tools
from tinyassets.interactive_http_agent import ServedChatAgentAdapter
from tinyassets.served_tools import granted_tools, model_tools

logger = logging.getLogger(__name__)


class ThinLoopChatAdapter(ServedChatAgentAdapter):
    """Served chat on the thin loop; admission and identity are unchanged."""

    def open_tools(self, coordinator, *, timeout):
        owner = coordinator.owner
        universe_dir = coordinator.context.universe_dir
        turn_id = coordinator.turn.turn_id
        return open_loop_tools(
            granted=model_tools(coordinator.config),
            capability_grant=granted_tools(coordinator.config),
            owner=owner,
            universe_dir=universe_dir,
            engine_identity=lambda: self.engine_identity(coordinator.context,
                                                         coordinator.config),
            timeout=timeout,
            ta_turn=turn_id,
            model_budget=coordinator.request_budget,
            model_parent=("turn", turn_id,
                          getattr(coordinator.context.provider_request,
                                  "agent_binding_id", ""),
                          getattr(coordinator.context.provider_request,
                                  "binding_revision", None))
            if coordinator.context.provider_request is not None else None,
            **coordinator.steering(),
        )


def open_served_turn(*, prompt, system, universe_context, config, adapter=None):
    """Build the served writer on the one coordinator.

    ``adapter`` is the test seam. Production uses :class:`ThinLoopChatAdapter`.
    """
    from tinyassets.agent_turn_coordinator import start_coordinator
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.call import get_provider_router, register_open_providers_for
    from tinyassets.turn_interrupt import current

    router = get_provider_router()
    if router is None:
        raise ProviderAuthorityHeldError("interactive agent requires a provider router")
    register_open_providers_for(universe_context)
    return start_coordinator(
        adapter=adapter or ThinLoopChatAdapter(), router=router, prompt=prompt,
        system=system, universe_context=universe_context, config=config,
        interrupt=current(),
    )


def _finish_served_turn(turn, *, response_observer=None) -> str:
    from tinyassets.agent_loop.execution import execution_owner

    result = execution_owner().run(turn.run())
    if response_observer is not None:
        response_observer(result)
    return result.text


def run_served_writer(
    turn_input, *, system, universe_context, config, response_observer=None,
    tools_observer=None,
):
    """Run one served writer turn; retry ONCE immediately only if nothing ran.

    ``tools_observer``, when given, is called with the set of brain-recording tool
    names this turn proved wrote something. A returned refusal is not a write.

    The interactive request must never block a worker on a sleep. The sole-writer
    retry policy is one immediate fresh coordinator only when every provider was
    skipped. A writer that has an agent plan or an HTTP model runs the one loop.
    Anything else is one model call, not a second agent executor.
    """
    from tinyassets.exceptions import AllProvidersExhaustedError, ProviderAuthorityHeldError
    from tinyassets.providers.agent_inference import AgentInferenceRequest
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.model_call import text_sync
    from tinyassets.request_budget import RequestBudgetExceeded

    config = config or ModelConfig()
    ordinary_text = (type(config.agent_request) is AgentInferenceRequest
                     and config.agent_request.text_only)
    http_turn = None
    selection = getattr(universe_context, "model_selection", None)
    if (selection is not None and universe_context.agent_model_plan is not None
            and not (getattr(config, "engine_mcp_enabled", False) or ordinary_text)):
        raise ProviderAuthorityHeldError("selected interactive model requires engine tools")
    if ((getattr(config, "engine_mcp_enabled", False) or ordinary_text) and selection is not None
            and (universe_context.agent_model_plan is not None
                 or selection.connection_id.startswith("api_key_http:"))):
        http_turn = open_served_turn(
            prompt=turn_input, system=system, universe_context=universe_context, config=config,
        )

    def _attempt():
        if http_turn is not None:
            return _finish_served_turn(http_turn, response_observer=response_observer)
        observe = {} if response_observer is None else {"response_observer": response_observer}
        return text_sync(
            turn_input,
            system=system,
            role="writer",
            universe_context=universe_context,
            config=config,
            operation="converse",
            retry_on_exhaustion=False,
            **observe,
        )

    try:
        return _attempt()
    except RequestBudgetExceeded as exc:
        completed = getattr(exc, "completed_tools", ())
        detail = f" Completed tool calls: {len(completed)}." if completed else ""
        answer = exc.continuation + detail
        if response_observer is not None:
            from tinyassets.providers.base import ProviderResponse

            try:
                response_observer(ProviderResponse(
                    text=answer, provider="", model="", family="", latency_ms=0,
                    degraded=True, request_receipt=exc.request_receipt,
                ))
            except Exception:  # noqa: BLE001 - telemetry cannot lose earned progress
                logger.warning("writer budget stop could not be reported")
        return answer
    except AllProvidersExhaustedError as exc:
        attempts = getattr(exc, "attempts", None) or []
        all_skipped = bool(attempts) and all(
            getattr(attempt, "status", "") == "skipped" for attempt in attempts
        )
        if not all_skipped or (http_turn is not None and (
            http_turn.plan is not None or http_turn.turn.rounds
        )):
            raise
        logger.warning(
            "writer chain fully cooled (all providers skipped, nothing ran); "
            "one immediate fresh-process retry (no sleep)",
        )
        if http_turn is not None:
            http_turn = open_served_turn(
                prompt=turn_input, system=system, universe_context=universe_context, config=config,
            )
        return _attempt()
    finally:
        if http_turn is not None:
            if tools_observer is not None:
                try:
                    from tinyassets.universe_intelligence import _brain_recording_tools

                    tools_observer(_brain_recording_tools(http_turn))
                except Exception:  # noqa: BLE001 - evidence never breaks the turn
                    logger.warning("could not read this turn's brain writes")
            try:
                http_turn.close_quiescent()
            except Exception:
                logger.exception("could not close quiescent interactive agent progress")

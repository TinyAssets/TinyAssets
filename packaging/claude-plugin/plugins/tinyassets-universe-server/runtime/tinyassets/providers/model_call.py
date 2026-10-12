"""The owner-bound model call boundary.

Routing and dispatch remain in :class:`ProviderRouter`; this module gives every
caller the same request, parent, result and receipt contract.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Callable

from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.base import ProviderResponse, UniverseContext
from tinyassets.providers.execution_receipt import (
    ExecutionReceipt,
    receipt_from_response,
)
from tinyassets.providers.model_policy import ModelRef

_last_provider: contextvars.ContextVar[str] = contextvars.ContextVar(
    "last_model_source", default="",
)
_helper_runner: contextvars.ContextVar[BoundModelCall | None] = contextvars.ContextVar(
    "owner_model_helper_runner", default=None,
)


@contextmanager
def owner_model_scope(bound_call: BoundModelCall):
    """Bind a run's one admitted call to its nested platform helpers."""
    if not isinstance(bound_call, BoundModelCall):
        raise TypeError("owner model helper scope requires a bound model call")
    runner_token = _helper_runner.set(bound_call)
    try:
        yield
    finally:
        _helper_runner.reset(runner_token)


def last_provider() -> str:
    return _last_provider.get()


get_last_provider = last_provider


@dataclass(frozen=True, slots=True)
class ModelContext:
    prompt: str
    system: str = ""
    role: str = "writer"
    config: Any = None
    universe_context: UniverseContext | None = None
    operation: str | None = None
    agent_observer: Any = None
    agent_execution_kind: str | None = None
    router: Any = None


@dataclass(frozen=True, slots=True)
class ModelCall:
    model: ModelRef | None
    fallbacks: tuple[ModelRef, ...]
    context: ModelContext
    purpose: str = "reply"
    origin: str = "workflow"


@dataclass(frozen=True, slots=True)
class Parent:
    owner: str
    command_center: str
    usage_id: str | None = None
    subject: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ModelResult:
    response: ProviderResponse
    receipt: ExecutionReceipt

    @property
    def text(self) -> str:
        return self.response.text


def _dispatch_args(request: ModelCall, parent: Parent) -> tuple[Any, ModelContext]:
    from tinyassets.providers.call import get_provider_router, register_open_providers_for

    router = request.context.router or get_provider_router()
    if router is None:
        raise RuntimeError("No provider router is available for this model call")
    context = request.context
    universe = context.universe_context
    if universe is None or universe.universe_dir is None:
        raise ProviderAuthorityHeldError(
            "Connect your provider: an owner-bound command center is required"
        )
    if universe.universe_dir.name != parent.command_center:
        raise PermissionError("Model call parent does not match its command center")
    authority = universe.served_provider
    if authority is not None and parent.owner and authority.owner_user_id != parent.owner:
        raise PermissionError("Model call parent does not match its owner")
    # The current router revalidates the selected source and owner authority at
    # each dispatch. This is a request, never an authority token.
    if request.model is not None:
        universe = replace(universe, model_selection=request.model)
        context = replace(context, universe_context=universe)
    register_open_providers_for(universe)
    return router, context


def _result(response: ProviderResponse, request: ModelCall) -> ModelResult:
    _last_provider.set(response.provider)
    receipt = response.execution_receipt or receipt_from_response(
        response, requested=request.model, purpose=request.purpose,
    )
    if request.model is not None:
        receipt = replace(receipt, requested_model=request.model.model_id)
    return ModelResult(response=response, receipt=receipt)


async def call(request: ModelCall, parent: Parent) -> ModelResult:
    router, context = _dispatch_args(request, parent)
    response = await router.call(
        context.role, context.prompt, context.system, context.config,
        operation=context.operation, universe_context=context.universe_context,
        _agent_observer=context.agent_observer,
        _agent_execution_kind=context.agent_execution_kind,
    )
    return _result(response, request)


def call_sync(request: ModelCall, parent: Parent) -> ModelResult:
    router, context = _dispatch_args(request, parent)
    response = router.call_sync(
        context.role, context.prompt, context.system, context.config,
        operation=context.operation, universe_context=context.universe_context,
    )
    return _result(response, request)


def _policy_models(policy: dict[str, Any] | None) -> tuple[ModelRef | None, tuple[ModelRef, ...]]:
    if not policy:
        return None, ()

    def _ref(value: Any) -> ModelRef | None:
        if not isinstance(value, dict):
            return None
        source = value.get("provider") or value.get("connection_id")
        model = value.get("model_id", value.get("model", ""))
        return ModelRef(str(source), str(model)) if source else None

    primary = _ref(policy.get("preferred"))
    fallbacks = tuple(ref for entry in policy.get("fallback_chain", ())
                      if (ref := _ref(entry)) is not None)
    return primary, fallbacks


def text_sync(
    prompt: str,
    system: str = "",
    *,
    role: str = "writer",
    config: Any = None,
    universe_context: UniverseContext | None = None,
    operation: str | None = None,
    policy: dict[str, Any] | None = None,
    response_observer: Callable[[ProviderResponse], None] | None = None,
    retry_on_exhaustion: bool = False,
) -> str:
    """Text adapter for older engine callbacks; all dispatch uses ``call_sync``."""
    del retry_on_exhaustion
    if universe_context is None:
        runner = _helper_runner.get()
        if runner is not None:
            return runner(
                prompt, system, role=role, config=config, policy=policy,
                response_observer=response_observer,
                operation=operation,
            )
    if universe_context is None or universe_context.universe_dir is None:
        raise ProviderAuthorityHeldError(
            "Connect your provider: an owner-bound command center is required"
        )
    preferred, fallbacks = _policy_models(policy)
    model = universe_context.model_selection or preferred
    authority = universe_context.served_provider
    parent = Parent(
        owner=authority.owner_user_id if authority is not None else "",
        command_center=universe_context.universe_dir.name,
    )
    result = call_sync(ModelCall(
        model=model, fallbacks=fallbacks,
        context=ModelContext(
            prompt=prompt, system=system, role=role, config=config,
            universe_context=universe_context, operation=operation,
        ),
    ), parent)
    if response_observer is not None:
        response_observer(result.response)
    return result.text


@dataclass(frozen=True, slots=True)
class BoundModelCall:
    """Keep a run's exact command center and operation across graph workers."""

    runner: Callable[..., str]
    universe_context: UniverseContext
    operation: str

    def __call__(self, prompt: str, system: str = "", **kwargs: Any) -> str:
        supplied = kwargs.pop("universe_context", None)
        if supplied is not None and supplied is not self.universe_context:
            raise PermissionError("model call cannot substitute command center context")
        operation = kwargs.pop("operation", None)
        if operation is not None and operation != self.operation:
            raise PermissionError("model call cannot substitute its bound operation")
        return self.runner(
            prompt, system, universe_context=self.universe_context,
            operation=self.operation, **kwargs,
        )


def bind_model_call(
    runner: Callable[..., str], universe_context: UniverseContext, *, operation: str,
) -> BoundModelCall:
    if universe_context is None or not operation:
        raise ValueError("A bound model call requires context and operation")
    return BoundModelCall(runner, universe_context, operation)

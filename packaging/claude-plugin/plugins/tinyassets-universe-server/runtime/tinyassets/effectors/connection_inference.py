"""Account model calls made through the ordinary owner connection capability.

The broker identifies model connections and refuses unaccounted inference BEFORE
network dispatch. That refusal is the trusted signal to reserve an invocation;
neither a node's body nor a user-supplied budget can disable accounting.
"""
from pathlib import Path
from types import SimpleNamespace


def _workflow_budget(run_id):
    """Borrow the runner's allocation across its synchronous effector boundary."""
    from tinyassets.agent_review import _CTX
    from tinyassets.background_served_provider import _BackgroundAssignedProviderSession
    from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
    from tinyassets.providers.call import UniverseBoundProviderCall

    context = _CTX.get()
    if context is None:
        return None
    provider, bound_run = context
    if bound_run != run_id:
        raise PermissionError('connection inference run changed')
    if type(provider) is UniverseBoundProviderCall:
        provider = provider.provider_call
    if type(provider) is _ForegroundRunProviderSession:
        if provider.bound_run_id != run_id or provider._closed:
            raise PermissionError('connection inference run ended')
        provider._validate_run(allowed_statuses={'running'})
        return provider._request_budget
    if type(provider) is _BackgroundAssignedProviderSession:
        return provider._request_budget
    return None


def request(proxy, verb, wire, *, root, owner, grant_id, connection_id, run_id=""):
    from tinyassets.storage.agent_request_usage import InferenceUsageRequired

    try:
        return proxy.request(verb, wire)
    except InferenceUsageRequired:
        # No request reached the vendor. The next broker call carries the same
        # granted request, bound to a single-use accounting reference.
        pass
    from tinyassets.broker.ops import new_op_id
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.definition import list_definitions
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.request_budget import (
        TurnRequestBudget,
        current_request_budget,
        metered_free_source,
    )

    root = Path(root)
    definitions = [d for d in list_definitions(root.name)
                   if d.ref == grant_id and d.owner_user_id == owner
                   and d.access_method == "api_key_http"]
    body = wire.get("body")
    model = body.get("model") if isinstance(body, dict) else None
    if not isinstance(model, str) or not model.strip():
        raise ProviderAuthorityHeldError("model inference requires an explicit model")
    if not definitions:
        raise ProviderAuthorityHeldError("connected model source definition is unavailable")
    source = "api_key_http:" + definitions[0].id
    context = SimpleNamespace(universe_dir=root, model_selection=ModelRef(source, model))
    free = metered_free_source(context, None, owner=owner)
    parent = current_request_budget()
    workflow = _workflow_budget(run_id)
    if parent is not None and workflow is not None and parent is not workflow:
        raise ProviderAuthorityHeldError('connection inference parent allocation changed')
    budget = parent or workflow
    owns_budget = budget is None
    if owns_budget:
        budget = TurnRequestBudget(owner, root.name)
    ordinal = None
    outcome = "unknown"
    try:
        budget.check_scope(owner, root.name)
        budget.persist(root.parent)
        if run_id:
            budget.link("run", run_id)
        ordinal = budget.reserve(owner=owner, universe=root.name, source_ref=source,
                                 model=model, free=free, purpose="helper")
        reference = budget.issue_reference(ordinal, grant_id=grant_id,
            connection_id=connection_id, verb=verb, request=wire, operation_id=new_op_id())
        response = proxy.request(verb, wire, inference_usage=reference)
        status = response.get("status") if isinstance(response, dict) else None
        outcome = "succeeded" if type(status) is int and 200 <= status < 300 else "failed"
        return response
    finally:
        try:
            if ordinal is not None:
                budget.settle_invocation(ordinal, outcome)
        finally:
            if owns_budget:
                budget.close()

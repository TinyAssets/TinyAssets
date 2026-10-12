"""Exercise the production model-call boundary from router-focused tests."""

from tinyassets.providers.model_call import (
    ModelCall,
    ModelContext,
    Parent,
)
from tinyassets.providers.model_call import (
    call as _call,
)
from tinyassets.providers.model_call import (
    call_sync as _call_sync,
)


def _request(router, role, prompt, system, config, operation, universe_context):
    universe = universe_context
    authority = universe.served_provider
    carrier = universe.provider_invocation
    owner = (authority.owner_user_id if authority is not None else
             carrier._receipt.principal_id if carrier is not None else "")
    return ModelCall(
        model=None, fallbacks=(), context=ModelContext(
            role=role, prompt=prompt, system=system, config=config,
            operation=operation, universe_context=universe, router=router,
        ),
    ), Parent(owner=owner, command_center=universe.universe_dir.name)


async def call(router, role, prompt, system, config=None, *, operation=None,
               universe_context=None):
    request, parent = _request(
        router, role, prompt, system, config, operation, universe_context,
    )
    return (await _call(request, parent)).response


def call_sync(router, role, prompt, system, config=None, *, operation=None,
              universe_context=None):
    request, parent = _request(
        router, role, prompt, system, config, operation, universe_context,
    )
    return _call_sync(request, parent).response

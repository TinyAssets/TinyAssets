"""Provider layer -- multi-provider routing with fallback chains.

Re-exports
----------
ProviderRouter    -- routes calls across providers with fallback + quota
ProviderResponse  -- uniform response envelope
ModelConfig       -- per-call configuration
BaseProvider      -- ABC for implementing new providers

Provider implementations are imported on demand to avoid hard
dependencies on optional packages. Importing a sibling module such as
``owner_binding`` must not load the router.
"""

from importlib import import_module

__all__ = [
    "BaseProvider",
    "DEGRADED_JUDGE_RESPONSE",
    "ModelConfig",
    "ProviderResponse",
    "ProviderRouter",
]

_LAZY = {
    "BaseProvider": ("tinyassets.providers.base", "BaseProvider"),
    "DEGRADED_JUDGE_RESPONSE": ("tinyassets.providers.base", "DEGRADED_JUDGE_RESPONSE"),
    "ModelConfig": ("tinyassets.providers.base", "ModelConfig"),
    "ProviderResponse": ("tinyassets.providers.base", "ProviderResponse"),
    "ProviderRouter": ("tinyassets.providers.router", "ProviderRouter"),
}


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'tinyassets.providers' has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

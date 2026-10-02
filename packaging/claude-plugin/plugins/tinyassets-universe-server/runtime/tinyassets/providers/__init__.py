"""Provider layer -- multi-provider routing with fallback chains.

Re-exports
----------
ProviderRouter    -- routes calls across providers with fallback + quota
ProviderResponse  -- uniform response envelope
ModelConfig       -- per-call configuration
BaseProvider      -- ABC for implementing new providers

Provider implementations are imported on demand to avoid hard
dependencies on optional packages.
"""

from tinyassets.providers.base import (
    DEGRADED_JUDGE_RESPONSE,
    BaseProvider,
    ModelConfig,
    ProviderResponse,
)
from tinyassets.providers.router import ProviderRouter

__all__ = [
    "BaseProvider",
    "DEGRADED_JUDGE_RESPONSE",
    "ModelConfig",
    "ProviderResponse",
    "ProviderRouter",
]

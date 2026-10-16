"""Runtime configuration and explicit context-bound canonical memory access.

Knowledge consumers resolve only through memory.runtime.owner_memory_scope.
Legacy non-memory configuration remains here until its owning runtime cutover.
"""

from __future__ import annotations

from typing import Any, Callable

from tinyassets.config import UniverseConfig

# Retrieval backends (set by DaemonController.start())
vector_store: Any | None = None
raptor_tree: Any | None = None
embed_fn: Callable[[str], list[float]] | None = None

# Per-universe configuration (loaded from config.yaml)
universe_config: UniverseConfig = UniverseConfig()


def reset() -> None:
    """Clear all runtime references (used in tests and shutdown).

    MUST be called before re-binding these singletons to a different
    universe. See the module docstring's "Universe isolation invariant"
    section for why.
    """
    for name in (
        "memory_manager",
        "version_store",
        "promise_tracker",
        "knowledge_graph",
        "temporal_store",
    ):
        globals().pop(name, None)
    global vector_store, raptor_tree, embed_fn
    global universe_config
    vector_store = None
    raptor_tree = None
    embed_fn = None
    universe_config = UniverseConfig()


def __getattr__(name):
    if name in {
        "memory_manager",
        "version_store",
        "promise_tracker",
        "knowledge_graph",
        "temporal_store",
    }:
        from tinyassets.memory.runtime import current_memory

        return current_memory().get(name)
    raise AttributeError(name)

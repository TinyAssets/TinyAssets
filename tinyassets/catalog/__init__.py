"""Git-native catalog backend for branch, node, goal, and bid YAML.

Spec: ``openspec/specs/community-patch-loop/spec.md``. The initial scope is
the storage layout + serializer with no git seam yet. Follow-up work
bolts on ``git_bridge.py`` and cuts over the MCP write handlers.

Exports:

- ``YamlRepoLayout`` — resolves repo-relative paths for Branches,
  Goals, Nodes, and sidecar metadata.
- ``branch_to_yaml_payload`` / ``branch_from_yaml_payload`` — YAML
  serialization for ``BranchDefinition`` with per-node extraction.
- ``goal_to_yaml_payload`` / ``goal_from_yaml_payload`` — same for
  Goal dicts.
- ``StorageBackend`` protocol with ``SqliteOnlyBackend`` (current
  default) and ``SqliteCachedBackend`` (writes YAML alongside the
  SQLite mirror; git operation deferred).

Reads stay through the SQLite cache for query performance. Writes go
through both backends when the cached variant is enabled. On clone /
pull the cache rebuilds from YAML via a separate tool.
"""

from tinyassets.catalog.backend import (
    CommitFailedError,
    DirtyFileError,
    SqliteCachedBackend,
    SqliteOnlyBackend,
    StorageBackend,
    get_backend,
    invalidate_backend_cache,
    list_unreconciled_writes,
)
from tinyassets.catalog.layout import YamlRepoLayout
from tinyassets.catalog.serializer import (
    branch_from_yaml_payload,
    branch_to_yaml_payload,
    goal_from_yaml_payload,
    goal_to_yaml_payload,
)

__all__ = [
    "CommitFailedError",
    "DirtyFileError",
    "SqliteCachedBackend",
    "SqliteOnlyBackend",
    "StorageBackend",
    "YamlRepoLayout",
    "branch_from_yaml_payload",
    "branch_to_yaml_payload",
    "get_backend",
    "goal_from_yaml_payload",
    "goal_to_yaml_payload",
    "invalidate_backend_cache",
    "list_unreconciled_writes",
]

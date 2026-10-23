"""Checkpointing utilities for TinyAssets.

Wraps LangGraph's SqliteSaver with WAL mode, retention policies,
and helpers for creating and resuming from checkpoints.

Re-exports
----------
create_checkpointer     -- create a SqliteSaver with WAL mode
get_checkpoint_history  -- list checkpoints for a thread
CheckpointRetentionPolicy -- custom retention policy
prune_checkpoint_history -- direct SQLite retention for large databases
"""

from tinyassets.checkpointing.sqlite_saver import (
    DEFAULT_CHECKPOINT_RETENTION_KEEP_LAST,
    CheckpointRetentionPolicy,
    apply_configured_checkpoint_retention,
    configured_checkpoint_retention_keep_last,
    create_checkpointer,
    get_checkpoint_history,
    prune_checkpoint_history,
)

__all__ = [
    "DEFAULT_CHECKPOINT_RETENTION_KEEP_LAST",
    "CheckpointRetentionPolicy",
    "apply_configured_checkpoint_retention",
    "configured_checkpoint_retention_keep_last",
    "create_checkpointer",
    "get_checkpoint_history",
    "prune_checkpoint_history",
]

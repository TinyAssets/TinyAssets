"""Initialize the in-box application stores and their explicit legacy upgrades.

The one-shot box_layout_migration owns physical placement and copies existing
rows before cutover. These declarations never traverse another owner's store.
"""
from __future__ import annotations

import threading
from pathlib import Path

from tinyassets.storage import (
    accounts_schema,
    authors_schema,
    catalog_schema,
    notes_schema,
    requests_schema,
    universes_schema,
)

STORES = (
    accounts_schema,
    universes_schema,
    authors_schema,
    requests_schema,
    notes_schema,
    catalog_schema,
)
TABLES = {name: ddl for store in STORES for name, ddl in store.TABLES.items()}
INDEXES = tuple(ddl for store in STORES for ddl in store.INDEXES)

_AUTHOR_SERVER_INIT_LOCK = threading.Lock()
_AUTHOR_SERVER_INITIALIZED: set[str] = set()


# Existing layouts differ only in the ordinal of these explicitly migrated
# columns. Keep every historical ADD COLUMN layout; never rebuild owner rows.
APPEND_HISTORY = {
    "branch_definitions": ("goal_id", "visibility", "skills_json", "fork_from",
                           "default_llm_policy_json", "concurrency_budget"),
    "goals": ("gate_ladder_json", "branch_protocol_json", "canonical_branch_version_id",
              "canonical_branch_history_json", "auto_canonical_via_leaderboard",
              "min_completed_runs_for_canonical", "selector_branch_version_id"),
    "gate_claims": ("conformance_pack_id",),
    "founder_home": ("platform_generated",),
}


def initialize_author_server(base_path: str | Path) -> Path:
    # Compatibility entry point; the unfenced writer stays in its existing home.
    from tinyassets.daemon_server import initialize_author_server as initialize

    return initialize(base_path)

"""Per-node paid-market bid mechanics.

Bid surface consists of:
- ``node_bid``: NodeBid dataclass + I/O + claim semantics.
- ``execution_log``: per-universe daemon-local activity log (mutable).
- ``settlements``: cross-host immutable settlement ledger (write-once).

First canonical subpackage of the layout in `docs/architecture.md`.
Promoted end-state 2026-04-19 from the four flat top-level modules
(``tinyassets/node_bid.py`` + ``tinyassets/bid_execution_log.py`` +
``tinyassets/bid_ledger.py`` deprecation shim + ``tinyassets/settlements.py``)
into this single package.

Per the host's foundation-end-state rule (README § Direction: foundational
patches are brought forward): no compat shims at the old top-level paths.
Any remaining external callers must migrate to ``tinyassets.bid.*``.
"""

from __future__ import annotations

from tinyassets.bid.execution_log import (
    LEDGER_FILENAME,
    LEDGER_LOCK_FILENAME,
    append_execution_log_entry,
    append_ledger_entry,
    execution_log_path,
    ledger_path,
    read_execution_log,
)
from tinyassets.bid.node_bid import (
    NodeBid,
    bid_path,
    bids_dir,
    claim_node_bid,
    new_node_bid_id,
    read_node_bid,
    read_node_bids,
    update_node_bid_status,
    validate_node_bid_inputs,
    write_node_bid_post,
)
from tinyassets.bid.settlements import (
    SCHEMA_VERSION,
    SettlementExistsError,
    record_settlement_event,
    settlement_path,
    settlements_dir,
)

__all__ = [
    # node_bid
    "NodeBid",
    "bid_path",
    "bids_dir",
    "claim_node_bid",
    "new_node_bid_id",
    "read_node_bid",
    "read_node_bids",
    "update_node_bid_status",
    "validate_node_bid_inputs",
    "write_node_bid_post",
    # execution_log
    "LEDGER_FILENAME",
    "LEDGER_LOCK_FILENAME",
    "append_execution_log_entry",
    "append_ledger_entry",
    "execution_log_path",
    "ledger_path",
    "read_execution_log",
    # settlements
    "SCHEMA_VERSION",
    "SettlementExistsError",
    "record_settlement_event",
    "settlement_path",
    "settlements_dir",
]

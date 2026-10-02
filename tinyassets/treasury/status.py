"""Read-only cost-ledger and treasury status summaries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from tinyassets.sqlite_connection import ClosingConnection
from tinyassets.storage import DB_FILENAME


def _workflow_db_path(base_path: str | Path) -> Path:
    return Path(base_path) / DB_FILENAME


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True, timeout=30.0, factory=ClosingConnection,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row["name"]) for row in rows}


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    value = conn.execute(sql, params).fetchone()[0]
    return int(value or 0)


def _status_counts(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status"
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def _recent_rows(conn: sqlite3.Connection, sql: str, limit: int) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, (limit,)).fetchall()]


def _empty_status(db_path: Path, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "database_path": str(db_path),
        "read_only": True,
        "autonomous_spend_allowed": False,
        "cost_ledger": {
            "escrow": {
                "count_total": 0,
                "locked_remaining_total": 0,
                "by_status": {},
            },
            "settlements": {
                "count_total": 0,
                "amount_total": 0,
                "treasury_fee_total": 0,
                "net_amount_total": 0,
                "by_status": {},
            },
            "batches": {
                "count_total": 0,
                "amount_total": 0,
                "fee_total": 0,
                "by_status": {},
            },
            "transactions": {
                "count_total": 0,
                "amount_total": 0,
            },
        },
        "treasury": {
            "entry_count": 0,
            "gross_amount_total": 0,
            "fee_collected_total": 0,
            "bounty_share_total": 0,
            "treasury_retained_total": 0,
            "bounty_pool_allocated_total": 0,
            "bounty_pool_disbursed_total": 0,
            "bounty_pool_remaining_total": 0,
            "royalty_payout_total": 0,
            "royalty_pending_total": 0,
        },
        "recent_settlements": [],
        "recent_treasury_entries": [],
        "recent_transactions": [],
        "caveats": [
            "Status surface is read-only and never locks, releases, refunds, "
            "batches, or spends funds.",
        ],
    }


def treasury_status(base_path: str | Path, *, limit: int = 10) -> dict[str, Any]:
    """Return a read-only cost-ledger and treasury summary.

    This function intentionally does not run migrations or create the workflow
    database. Missing tables are reported as zeroed sections so a status check
    cannot become an implicit payment-system write.
    """
    db_path = _workflow_db_path(base_path)
    safe_limit = max(0, min(int(limit), 100))
    if not db_path.exists():
        return _empty_status(db_path, "empty")

    with _connect_readonly(db_path) as conn:
        tables = _tables(conn)
        result = _empty_status(db_path, "ok")

        if "escrow_balance" in tables:
            result["cost_ledger"]["escrow"] = {
                "count_total": _scalar(conn, "SELECT COUNT(*) FROM escrow_balance"),
                "locked_remaining_total": _scalar(
                    conn,
                    """
                    SELECT COALESCE(SUM(total_amount - released_amount), 0)
                    FROM escrow_balance
                    WHERE status IN ('locked', 'partial')
                    """,
                ),
                "by_status": _status_counts(conn, "escrow_balance"),
            }

        if "pending_settlement" in tables:
            result["cost_ledger"]["settlements"] = {
                "count_total": _scalar(conn, "SELECT COUNT(*) FROM pending_settlement"),
                "amount_total": _scalar(
                    conn, "SELECT COALESCE(SUM(amount), 0) FROM pending_settlement"
                ),
                "treasury_fee_total": _scalar(
                    conn,
                    "SELECT COALESCE(SUM(treasury_fee), 0) FROM pending_settlement",
                ),
                "net_amount_total": _scalar(
                    conn, "SELECT COALESCE(SUM(net_amount), 0) FROM pending_settlement"
                ),
                "by_status": _status_counts(conn, "pending_settlement"),
            }
            result["recent_settlements"] = _recent_rows(
                conn,
                """
                SELECT settlement_id, escrow_id, recipient_id, amount, treasury_fee,
                       net_amount, status, event_type, created_at, settled_at, batch_id
                FROM pending_settlement
                ORDER BY created_at DESC, settlement_id DESC
                LIMIT ?
                """,
                safe_limit,
            )

        if "settlement_batch" in tables:
            result["cost_ledger"]["batches"] = {
                "count_total": _scalar(conn, "SELECT COUNT(*) FROM settlement_batch"),
                "amount_total": _scalar(
                    conn, "SELECT COALESCE(SUM(total_amount), 0) FROM settlement_batch"
                ),
                "fee_total": _scalar(
                    conn, "SELECT COALESCE(SUM(total_fee), 0) FROM settlement_batch"
                ),
                "by_status": _status_counts(conn, "settlement_batch"),
            }

        if "transaction_log" in tables:
            result["cost_ledger"]["transactions"] = {
                "count_total": _scalar(conn, "SELECT COUNT(*) FROM transaction_log"),
                "amount_total": _scalar(
                    conn, "SELECT COALESCE(SUM(amount), 0) FROM transaction_log"
                ),
            }
            result["recent_transactions"] = _recent_rows(
                conn,
                """
                SELECT tx_id, kind, escrow_id, settlement_id, batch_id, actor_id,
                       amount, recorded_at, note
                FROM transaction_log
                ORDER BY recorded_at DESC, tx_id DESC
                LIMIT ?
                """,
                safe_limit,
            )

        treasury = result["treasury"]
        if "treasury_balance" in tables:
            treasury["entry_count"] = _scalar(conn, "SELECT COUNT(*) FROM treasury_balance")
            treasury["gross_amount_total"] = _scalar(
                conn, "SELECT COALESCE(SUM(amount), 0) FROM treasury_balance"
            )
            treasury["fee_collected_total"] = _scalar(
                conn, "SELECT COALESCE(SUM(fee_collected), 0) FROM treasury_balance"
            )
            treasury["bounty_share_total"] = _scalar(
                conn, "SELECT COALESCE(SUM(bounty_share), 0) FROM treasury_balance"
            )
            treasury["treasury_retained_total"] = (
                treasury["fee_collected_total"] - treasury["bounty_share_total"]
            )
            result["recent_treasury_entries"] = _recent_rows(
                conn,
                """
                SELECT entry_id, source_tx_id, amount, take_rate_bp, fee_collected,
                       bounty_share, recorded_at, note
                FROM treasury_balance
                ORDER BY recorded_at DESC, entry_id DESC
                LIMIT ?
                """,
                safe_limit,
            )

        if "bounty_pool_balance" in tables:
            treasury["bounty_pool_allocated_total"] = _scalar(
                conn, "SELECT COALESCE(SUM(allocated), 0) FROM bounty_pool_balance"
            )
            treasury["bounty_pool_disbursed_total"] = _scalar(
                conn, "SELECT COALESCE(SUM(disbursed), 0) FROM bounty_pool_balance"
            )
            treasury["bounty_pool_remaining_total"] = (
                treasury["bounty_pool_allocated_total"]
                - treasury["bounty_pool_disbursed_total"]
            )

        if "royalty_payout" in tables:
            treasury["royalty_payout_total"] = _scalar(
                conn, "SELECT COALESCE(SUM(royalty_amount), 0) FROM royalty_payout"
            )
            treasury["royalty_pending_total"] = _scalar(
                conn,
                """
                SELECT COALESCE(SUM(royalty_amount), 0)
                FROM royalty_payout
                WHERE status = 'pending'
                """,
            )

        return result

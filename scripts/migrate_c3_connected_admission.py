"""Offline M2: fence legacy run receipts after a full backup and writer stop."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from scripts.migrate_c3_difficulty_override import _backup_contains_database
from tinyassets.provider_work_authority import (
    ProviderWorkExecutionClaimState,
    ProviderWorkReceiptState,
)
from tinyassets.storage.provider_work_authority import _claim_record, _receipt_record


def migrate(data_root: Path, *, archive: Path | None = None, apply: bool = False,
            writers_stopped: bool = False) -> dict:
    database = data_root.resolve() / ".tinyassets.db"
    if not database.is_file():
        raise FileNotFoundError(database)
    if apply and (not writers_stopped or archive is None or not _backup_contains_database(archive)):
        raise PermissionError("apply requires stopped writers and a full backup archive")
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode={'rw' if apply else 'ro'}",
        uri=True, timeout=0,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN EXCLUSIVE" if apply else "BEGIN")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_work_receipts)")
        }
        if not {"authority_scope", "manifest_digest"} <= columns:
            raise ValueError("provider receipt scope schema is missing; M2 refused")
        rows = connection.execute(
            "SELECT * FROM provider_work_receipts WHERE state = 'active' ORDER BY receipt_id"
        ).fetchall()
        runs = []
        for row in rows:
            receipt = _receipt_record(row)
            runs.append({
                "work_item_kind": receipt.work_item_kind,
                "work_item_id": receipt.work_item_id,
                "receipt_id": receipt.receipt_id,
            })
            if not apply:
                continue
            fenced = replace(receipt, state=ProviderWorkReceiptState.FENCED)
            fenced = replace(fenced, receipt_digest=fenced.expected_digest())
            connection.execute(
                "UPDATE provider_work_receipts SET state=?, receipt_digest=?, record_json=? "
                "WHERE receipt_id=? AND state='active'",
                (fenced.state.value, fenced.receipt_digest,
                 json.dumps(fenced.to_dict(), sort_keys=True, separators=(",", ":")),
                 receipt.receipt_id),
            )
            claim_row = connection.execute(
                "SELECT * FROM provider_work_execution_claims WHERE receipt_id=?",
                (receipt.receipt_id,),
            ).fetchone()
            if claim_row is not None:
                claim = _claim_record(claim_row)
                if claim.state is ProviderWorkExecutionClaimState.ACTIVE:
                    closed = replace(claim, state=ProviderWorkExecutionClaimState.INVALIDATED)
                    closed = replace(closed, claim_digest=closed.expected_digest())
                    connection.execute(
                        "UPDATE provider_work_execution_claims "
                        "SET state='invalidated', claim_digest=?, record_json=? "
                        "WHERE claim_id=? AND state='active'",
                        (closed.claim_digest,
                         json.dumps(closed.to_dict(), sort_keys=True, separators=(",", ":")),
                         claim.claim_id),
                    )
        if apply:
            connection.commit()
        else:
            connection.rollback()
        return {
            "count": len(runs),
            "run_ids": [row["work_item_id"] for row in runs
                        if row["work_item_kind"] == "run"],
            "receipts": runs,
            "applied": apply,
        }
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--backup-archive", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.data_root, archive=args.backup_archive,
                             apply=args.apply, writers_stopped=args.writers_stopped),
                     sort_keys=True))


if __name__ == "__main__":
    main()

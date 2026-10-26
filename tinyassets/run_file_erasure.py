"""Legacy settled-file row ordering for pre-cutover cleanup."""
from tinyassets.storage import run_files as store


def settled_deletion_targets(conn, *, principal):
    """Same-transaction FK children/parents, independent of former home identity."""
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='run_file_operations' AND type='table'"
        ).fetchone()
        is None
    ):
        return []
    unsettled = conn.execute(
        "SELECT 1 FROM run_file_operations o LEFT JOIN run_file_allocations a USING(operation_id) "
        "WHERE o.owner_id=? AND (o.state!='released' OR a.state IS NULL OR a.state!='released' "
        "OR a.amount!=0 OR EXISTS(SELECT 1 FROM run_file_cleanup c "
        "WHERE c.operation_id=o.operation_id) OR EXISTS(SELECT 1 FROM run_file_objects f "
        "WHERE f.operation_id=o.operation_id AND f.state!='released')) LIMIT 1",
        (principal,),
    ).fetchone()
    if unsettled:
        raise store.FileCustodyRefused("file_erasure_pending")
    operations = "SELECT operation_id FROM run_file_operations WHERE owner_id=?"
    files = "SELECT file_id FROM run_file_objects WHERE owner_id=?"
    return [
        ("run_file_bindings", f"file_id IN ({files})", (principal,)),
        ("run_file_objects", "owner_id=?", (principal,)),
        ("run_file_allocations", f"operation_id IN ({operations})", (principal,)),
        ("run_file_cleanup", f"operation_id IN ({operations})", (principal,)),
        ("run_file_operations", "owner_id=?", (principal,)),
    ]

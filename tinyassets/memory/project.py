"""Canonical project memory and history within an explicitly admitted owner box.

Opaque project IDs require reviewed namespace bindings. This consumer never
opens the retired global project database or infers ownership from a writer.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.records import SourceRecords, source_census

SOURCE = "project"
TABLE = "project_memory"
HISTORY = "project_memory_history"


def _records(value):
    if not isinstance(value, SourceRecords) or value.source != SOURCE:
        raise InvalidBundle("Project memory requires an admitted canonical source")
    return value


def _view(value, project_id):
    records = _records(value)
    before = records.snapshot()
    census = source_census(before, SOURCE)
    center, owner, _ = records.bundle.scope
    for table in (TABLE, HISTORY):
        definition = census["tables"].get(table, {})
        if not any(
            b.get("owner") == owner
            and b.get("center") == center
            and b.get("namespace") == project_id
            and b.get("review_ref")
            for b in definition.get("reviewed_bindings", [])
        ):
            raise InvalidBundle("Project namespace has no reviewed binding for this owner")
    return records, before, records.rows(before)


def _result(row):
    return {**row, "value": json.loads(row["value"])} if row is not None else None


def project_memory_set(
    source, *, project_id, key, value, actor="", expected_version=None, operation_id=None
):
    """Atomically append history and update current value; reuse IDs on retries."""
    records, before, tables = _view(source, project_id)
    operation_id = operation_id or uuid.uuid4().hex
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128:
        raise InvalidBundle("Project write needs a bounded stable request ID")
    encoded = json.dumps(value, default=str)
    # Stable source-compatible INTEGER identity; collisions refuse, never replace history.
    history_id = int.from_bytes(hashlib.sha256(operation_id.encode()).digest()[:8], "big") >> 1
    history = list(tables[HISTORY].values())
    prior = next((row for row in history if row["id"] == history_id), None)
    if prior is not None:
        if (prior["project_id"], prior["key"], prior["value"], prior["updated_by"]) != (
            project_id,
            key,
            encoded,
            actor,
        ):
            raise InvalidBundle("Project operation ID already committed different content")
        if expected_version is not None and expected_version != prior["version"] - 1:
            raise InvalidBundle("Project operation ID reused with another expected version")
        return {"status": "ok", "version": prior["version"]}
    old = next(
        (
            row
            for row in tables[TABLE].values()
            if (row["project_id"], row["key"]) == (project_id, key)
        ),
        None,
    )
    current = old["version"] if old else 0
    if expected_version is not None and expected_version != current:
        return {
            "conflict": True,
            "current_version": current,
            "message": f"Version mismatch for {project_id}/{key}",
        }
    row = {
        "project_id": project_id,
        "key": key,
        "value": encoded,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": actor,
        "version": current + 1,
    }
    # Preserve unknown source columns instead of dropping future schema values.
    current_row = {**(old or {}), **row}
    history_row = {"id": history_id, **row}
    records.write(
        before,
        upserts={TABLE: [current_row], HISTORY: [history_row]},
        operation_id="project-" + hashlib.sha256(operation_id.encode()).hexdigest(),
    )
    return {"status": "ok", "version": current + 1}


def project_memory_get(source, *, project_id, key):
    _, _, tables = _view(source, project_id)
    return _result(
        next(
            (
                row
                for row in tables[TABLE].values()
                if (row["project_id"], row["key"]) == (project_id, key)
            ),
            None,
        )
    )


def project_memory_list(source, *, project_id, key_prefix="", limit=100):
    _, _, tables = _view(source, project_id)
    rows = [
        row
        for row in tables[TABLE].values()
        if row["project_id"] == project_id and row["key"].startswith(key_prefix)
    ]
    return [_result(row) for row in sorted(rows, key=lambda row: row["key"])[: max(1, limit)]]

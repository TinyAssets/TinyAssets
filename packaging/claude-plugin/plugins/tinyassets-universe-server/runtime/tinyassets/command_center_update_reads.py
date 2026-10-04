"""Connection-bound metadata readers for a stored presentation policy.

No schema initialization, independent connections, actor context or writes.
The executor attaches trusted stores and reserves writers before constructing this.
"""

from __future__ import annotations

import json


class HeldReads:
    def __init__(self, conn, base):
        self.conn, self.base = conn, base

    def definition(self, definition_id):
        from tinyassets.custom_agents import _definition_from_row, _read_definition_row

        row = _read_definition_row(self.conn, definition_id)
        return _definition_from_row(self.conn, row) if row is not None else None

    def branch(self, source_id):
        row = self.conn.execute(
            "SELECT * FROM branch_definitions WHERE branch_def_id=?", (source_id,)
        ).fetchone()
        return dict(row) if row is not None else {}

    def _version_row(self, version_id):
        if "source_versions" not in {r[1] for r in self.conn.execute("PRAGMA database_list")}:
            raise ValueError("required workflow version store is missing")
        return self.conn.execute(
            "SELECT * FROM source_versions.branch_versions WHERE branch_version_id=?", (version_id,)
        ).fetchone()

    def version_parent(self, version_id):
        row = self._version_row(version_id)
        return str(row["branch_def_id"]) if row else ""

    def version_public(self, version_id):
        row = self._version_row(version_id)
        return bool(row and row["public"])

    def version(self, version_id):
        from tinyassets.branch_versions import _row_to_version

        row = self._version_row(version_id)
        return _row_to_version(row) if row else None

    def pin(self, uid, request_id):
        row = self.conn.execute(
            "SELECT * FROM source_pins.pins WHERE universe_id=? AND request_id=?", (uid, request_id)
        ).fetchone()
        if row is None:
            return None
        return {
            "pin_id": row["pin_id"],
            "kind": row["kind"],
            "agent": row["agent_id"],
            "digest": row["digest"],
            "record": json.loads(row["record_json"]),
            "state": row["state"],
            "progress": json.loads(row["progress_json"]),
        }

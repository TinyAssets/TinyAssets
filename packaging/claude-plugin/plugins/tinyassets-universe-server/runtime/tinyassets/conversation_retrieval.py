"""Read-only, lossless paging of the current founder's retained conversation.

The caller supplies a verified universe directory and principal session. This
module never discovers other sessions and never creates or migrates a store.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from tinyassets.conversation_failure import failure_column_sql, project_failure_row

PAGE_SIZE = 20


def read_conversation_page(universe_dir, session_id, *, field_name="", offset=0,
                           max_chars=8192, query=""):
    if not session_id:
        raise ValueError("conversation_session_required")
    if type(offset) is not int or offset < 0:
        raise ValueError("conversation_offset_invalid")
    if type(max_chars) is not int or not 1 <= max_chars <= 32768:
        raise ValueError("conversation_chunk_size_invalid")
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError("conversation_query_invalid")
    root = Path(universe_dir).resolve()
    path = root / ".conversation_memory.db"
    if path.resolve() != path:
        raise PermissionError("conversation_store_outside_universe")
    if not path.exists():
        return {"available": False, "messages": [], "next_offset": None}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)) as conn:
        conn.row_factory = sqlite3.Row
        # Unicode case folding and literal matching: %, _ and SQL fragments are
        # data. This changes no schema and searches only the bound session.
        conn.create_function("casefold", 1, str.casefold, deterministic=True)
        failure_column = failure_column_sql(conn)
        if field_name:
            if not field_name.isascii() or not field_name.isdecimal() or len(field_name) > 18:
                raise ValueError("conversation_message_id_invalid")
            row = conn.execute(
                f"SELECT id, speaker, ts, content, {failure_column} AS failure_json "
                "FROM conversation_turns "
                "WHERE session_id = ? AND id = ?",
                (session_id, int(field_name)),
            ).fetchone()
            if row is None:
                return {"available": True, "error": "conversation_message_not_found"}
            value = project_failure_row(row)
            content = value.pop("content")
            value["total_chars"] = len(content)
            value["chunk"] = content[offset:offset + max_chars]
            end = offset + len(value["chunk"])
            return dict(
                value, available=True, field_name=str(row["id"]),
                offset=offset, offset_unit="unicode_code_points",
                next_offset=end if end < value["total_chars"] else None,
            )
        # Keyset pagination: new arrivals cannot shift or skip the older page.
        where = "session_id = ?" + (" AND id < ?" if offset else "")
        args = [session_id]
        if offset:
            args.append(offset)
        if query:
            where += " AND instr(casefold(content), ?) > 0"
            args.append(query.casefold())
        args.append(PAGE_SIZE + 1)
        rows = conn.execute(
            "SELECT id, speaker, ts, length(CAST(content AS BLOB)) AS total_bytes, "
            "substr(content, 1, 240) AS preview, "
            f"{failure_column} AS failure_json "
            f"FROM conversation_turns WHERE {where} ORDER BY id DESC LIMIT ?", args,
        ).fetchall()
        kept = rows[:PAGE_SIZE]
        return {
            "available": True,
            "messages": [project_failure_row(row) for row in kept],
            "next_offset": kept[-1]["id"] if len(rows) > PAGE_SIZE else None,
            "offset_unit": "before_message_id",
            "read": (
                "Use field_name=<id> to read a message; "
                "output_offset then counts Unicode characters. Previews are incomplete. "
                "Search retained text with query; keep the query when paging next_offset."
            ),
            "retention": (
                "Only retained messages are available; deleted history cannot be reconstructed."
            ),
        }

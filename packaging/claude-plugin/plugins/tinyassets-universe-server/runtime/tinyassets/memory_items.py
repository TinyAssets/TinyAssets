"""One memory per bullet; legacy IDs are persisted only by an owner write."""
from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

from tinyassets import harness_history
from tinyassets.universe_files import MAX_BRAIN_FILE_BYTES, read_universe_text

_BULLET = re.compile(r"^- (?:\[(m_[0-9a-f]{4,8})\] )?(.*)$")


def _read(universe_dir: Path) -> str:
    try:
        return read_universe_text(universe_dir, "MEMORY.md", max_bytes=MAX_BRAIN_FILE_BYTES)
    except FileNotFoundError:
        return ""


def _parse(text: str) -> tuple[list[str], list[dict]]:
    lines = text.splitlines(keepends=True)
    items = []
    used = {m[1] for line in lines if (m := _BULLET.fullmatch(line.rstrip("\r\n"))) and m[1]}
    seen = set()
    for index, line in enumerate(lines):
        match = _BULLET.fullmatch(line.rstrip("\r\n"))
        if not match:
            continue
        item_id, body = match.groups()
        if item_id in seen:
            raise ValueError(f"duplicate memory id: {item_id}")
        if item_id is None:
            salt = 0
            while True:
                item_id = "m_" + hashlib.sha256(f"{index}:{salt}:{body}".encode()).hexdigest()[:8]
                if item_id not in used:
                    break
                salt += 1
            used.add(item_id)
        seen.add(item_id)
        items.append({"id": item_id, "text": body, "line": index})
    return lines, items


def list_items(universe_dir: Path) -> list[dict]:
    """List stable IDs without modifying the file (legacy IDs are provisional)."""
    return [{"id": item["id"], "text": item["text"]} for item in _parse(_read(universe_dir))[1]]


def _change(universe_dir: Path, item_id: str | None, text: str | None) -> dict | None:
    with harness_history.transaction(universe_dir) as conn:
        lines, items = _parse(_read(universe_dir))
        target = next((item for item in items if item["id"] == item_id), None)
        if item_id is not None and target is None:
            raise ValueError("unknown memory item")
        for item in items:
            if item is target:
                lines[item["line"]] = "" if text is None else f"- [{item_id}] {text}\n"
            else:
                lines[item["line"]] = f"- [{item['id']}] {item['text']}\n"
        if item_id is None:
            used = {item["id"] for item in items}
            while item_id is None or item_id in used:
                item_id = "m_" + secrets.token_hex(4)
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"- [{item_id}] {text}\n")
        content = "".join(lines).encode("utf-8")
        if len(content) > MAX_BRAIN_FILE_BYTES:
            raise ValueError("memory is too large")
        harness_history._write(conn, universe_dir, "MEMORY.md", content, "owner")
    return {"id": item_id, "text": text} if text is not None else None


def set_item(universe_dir: Path, item_id: str | None, text: str) -> dict:
    if not isinstance(text, str) or not text.strip() or any(c in text for c in "\r\n\x00"):
        raise ValueError("memory text must be one nonempty line")
    return _change(universe_dir, item_id, text.strip())


def delete_item(universe_dir: Path, item_id: str) -> None:
    if not isinstance(item_id, str) or not item_id:
        raise ValueError("a memory item id is required")
    _change(universe_dir, item_id, None)

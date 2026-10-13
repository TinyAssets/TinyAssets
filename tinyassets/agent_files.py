"""Owner-owned agent files and the replayable C8 store migration.

The binding id is the agent's durable address.  A directory name is only a
human-readable placement; neither address nor authority is derived from it.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from tinyassets.universe_files import (
    list_universe_dir,
    read_universe_file,
    read_universe_text,
    write_universe_file,
)


def agent_directory(name: str, identity: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48] or "agent"
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"agents/{slug}-{suffix}"


def _instructions(definition: dict[str, Any]) -> str:
    sections = []
    for key, component in sorted(definition.get("components", {}).items()):
        config = component.get("config", {})
        instruction = config.get("instructions") if isinstance(config, dict) else None
        if isinstance(instruction, str) and instruction.strip():
            sections.append(f"## {key} ({component.get('kind', '')})\n\n{instruction.strip()}")
    return "\n\n".join(sections) + ("\n" if sections else "")


def _put_once(universe_dir: Path, path: str, data: bytes) -> None:
    try:
        write_universe_file(universe_dir, path, data, mode="exclusive")
    except FileExistsError:
        if read_universe_file(universe_dir, path) != data:
            raise ValueError(f"agent file already edited or conflicts: {path}") from None


def _write_agent(
    universe_dir: Path, *, owner: str, identity: str,
    definition: dict[str, Any], binding: dict[str, Any] | None,
) -> str:
    name = (binding or {}).get("configuration", {}).get("name") or definition["name"]
    directory = agent_directory(name, identity)
    stored_definition = deepcopy(definition)
    stored_definition.pop("portable_definition", None)
    instruction_files = {}
    for key, component in stored_definition.get("components", {}).items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key):
            raise ValueError(f"invalid stored agent component key: {key!r}")
        config = component.get("config", {})
        instruction = config.pop("instructions", None)
        if isinstance(instruction, str) and instruction.strip():
            relpath = f"{directory}/instructions/{key}.md"
            _put_once(universe_dir, relpath, instruction.encode("utf-8"))
            instruction_files[key] = f"instructions/{key}.md"
    settings = {
        "schema_version": 1,
        "agent_id": identity,
        "owner_user_id": owner,
        "universe_id": universe_dir.name,
        "definition": stored_definition,
        "binding": binding,
        "instruction_files": instruction_files,
    }
    _put_once(universe_dir, f"{directory}/settings.json",
              (json.dumps(settings, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":")) + "\n").encode("utf-8"))
    return directory


def _assert_owner_space(conn: Any, universe_id: str, owner: str) -> None:
    row = conn.execute(
        "SELECT owner_id FROM universe_owner WHERE universe_id = ?", (universe_id,)
    ).fetchone()
    if row is None or row[0] != owner:
        raise PermissionError("agent migration needs the owner's own space")


def migrate_owner(base_path: str | Path, owner_user_id: str) -> list[str]:
    """Copy one owner's current SQL agents into files in one SQL transaction.

    A completed owner is never rewritten, so later file edits survive replay.
    Interrupted file writes are compared byte for byte on retry; disagreement
    fails closed. Run during the C8 maintenance window with agent writes stopped.
    """
    from tinyassets.custom_agents import (
        _agent_connect,
        _binding_from_row,
        _definition_from_row,
        _read_definition_row,
    )
    from tinyassets.principals import named_principal
    from tinyassets.storage.current_home import current_home

    owner = named_principal(owner_user_id)
    if not owner or owner != owner_user_id:
        raise ValueError("a named owner is required")
    base = Path(base_path)
    with _agent_connect(base) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_file_migrations ("
            "owner_user_id TEXT PRIMARY KEY, completed_at TEXT NOT NULL)"
        )
        if conn.execute(
            "SELECT 1 FROM agent_file_migrations WHERE owner_user_id = ?", (owner,)
        ).fetchone():
            return []

        rows = conn.execute(
            "SELECT * FROM agent_bindings WHERE created_by = ? "
            "ORDER BY universe_id, agent_binding_id", (owner,)
        ).fetchall()
        placements = []
        bound_definitions = set()
        for row in rows:
            binding = _binding_from_row(row)
            did = binding["agent_definition_id"]
            definition_row = _read_definition_row(conn, did)
            if definition_row is None:
                raise ValueError(f"binding {binding['agent_binding_id']} lost its definition")
            definition = _definition_from_row(conn, definition_row)
            _assert_owner_space(conn, binding["universe_id"], owner)
            universe = base / binding["universe_id"]
            placements.append(_write_agent(
                universe, owner=owner, identity=binding["agent_binding_id"],
                definition=definition, binding=binding,
            ))
            bound_definitions.add(did)

        unbound = conn.execute(
            "SELECT * FROM agent_definitions WHERE author_id = ? "
            "ORDER BY agent_definition_id", (owner,)
        ).fetchall()
        for row in unbound:
            did = str(row["agent_definition_id"])
            if did in bound_definitions:
                continue
            home = current_home(conn, owner)
            if not home:
                raise ValueError(f"owner {owner!r} has unbound agents but no home")
            _assert_owner_space(conn, home, owner)
            placements.append(_write_agent(
                base / home, owner=owner, identity=did,
                definition=_definition_from_row(conn, row), binding=None,
            ))
        conn.execute(
            "INSERT INTO agent_file_migrations (owner_user_id, completed_at) "
            "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))", (owner,)
        )
    return placements


def migrate_all_owners(base_path: str | Path) -> dict[str, list[str]]:
    """Enumerate owners once; each owner then commits independently."""
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(base_path) as conn:
        home_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'founder_home'"
        ).fetchone()
        unbound_authors = (
            "SELECT author_id FROM agent_definitions "
            "WHERE author_id IN (SELECT founder_sub FROM founder_home)"
            if home_table else "SELECT created_by FROM agent_bindings WHERE 0"
        )
        owners = [str(row[0]) for row in conn.execute(
            "SELECT created_by FROM agent_bindings UNION " + unbound_authors + " ORDER BY 1"
        )]
    return {owner: migrate_owner(base_path, owner) for owner in owners}


def list_agents(base_path: str | Path, *, universe_id: str, owner_user_id: str) -> list[dict]:
    """Read the owner's migrated agents from editable files, never from SQL."""
    universe = Path(base_path) / universe_id
    try:
        directories = list_universe_dir(universe, "agents")
    except FileNotFoundError:
        return []
    found = []
    for name in directories:
        try:
            raw = read_universe_file(universe, f"agents/{name}/settings.json")
        except FileNotFoundError:
            continue  # Existing package agent with no C8 settings.
        settings = json.loads(raw)
        if (settings.get("owner_user_id") != owner_user_id
                or settings.get("universe_id") != universe_id):
            continue
        definition = settings["definition"]
        for key, relpath in settings.get("instruction_files", {}).items():
            if (not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key)
                    or relpath != f"instructions/{key}.md"):
                raise ValueError("invalid agent instruction path")
            definition["components"][key]["config"]["instructions"] = (
                read_universe_text(universe, f"agents/{name}/{relpath}")
            )
        found.append({**settings, "directory": f"agents/{name}",
                      "instructions": _instructions(definition)})
    return found

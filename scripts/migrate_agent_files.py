"""C8 cutover: move every custom-agent binding out of SQL into ``agents/<name>/``.

Run once, in a maintenance window, after a full backup::

    python scripts/migrate_agent_files.py --base <data-root>

For each command center, every binding that was an agent a person could
address (a configured, non-serving, non-installation binding on a definition
with instructions) becomes a directory beside the main agent: ``AGENTS.md``
holds its instructions and ``settings.yaml`` names it (``agent.id`` keeps the
binding id, so its thread, activities and wakes keep answering; ``agent.name``
keeps the owner's name for it; a retired binding lands ``retired: true``). A
binding whose package install already landed ``agents/<slug>/`` is written
into that directory. The SQL rows are deleted in the same transaction, so a
second run finds nothing to do; an interrupted run is replay-safe because
every file is compared byte for byte before the rows go.

The serving binding (the main agent's provider) and a conversation-design
installation are not agents and are left alone, as are catalogue definitions
with no binding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from tinyassets.agent_definition import (  # noqa: E402
    AGENTS_DIR,
    INSTRUCTIONS_FILE,
    SETTINGS_FILE,
    parse_settings,
)
from tinyassets.universe_files import (  # noqa: E402
    list_universe_dir,
    read_universe_file,
    write_universe_file,
)

NOT_AN_AGENT_ROLES = frozenset({"app_experience"})


def _instructions(definition: dict) -> str:
    sections = []
    for key, component in sorted((definition.get("components") or {}).items()):
        config = component.get("config") if isinstance(component, dict) else None
        text = config.get("instructions") if isinstance(config, dict) else None
        if isinstance(text, str) and text.strip():
            sections.append((key, component.get("kind") or "", text.strip()))
    if len(sections) == 1:
        return sections[0][2] + "\n"
    return "\n\n".join(f"## {key} ({kind})\n\n{text}" if kind else f"## {key}\n\n{text}"
                       for key, kind, text in sections) + ("\n" if sections else "")


def _is_agent(binding: dict, definition: dict | None) -> bool:
    from tinyassets.onboarding.serving import (
        PLATFORM_DEFINITION_AUTHOR,
        RETIRED_PLATFORM_DEFINITION_AUTHOR,
    )

    configuration = binding.get("configuration")
    return bool(
        definition is not None
        and binding.get("status") == "configured"
        and isinstance(configuration, dict)
        and configuration.get("role") not in NOT_AN_AGENT_ROLES
        and "turn_consumer" not in configuration
        and "provider_ref" not in configuration
        and definition.get("author_id") not in {
            PLATFORM_DEFINITION_AUTHOR, RETIRED_PLATFORM_DEFINITION_AUTHOR}
    )


def _installed_slug(base: Path, binding: dict) -> str | None:
    """The package directory a template install landed for this binding, if any."""
    from tinyassets.command_center_packages import _db, database_path

    if not database_path(base).exists():
        return None
    uid, owner, wanted = binding["universe_id"], binding["created_by"], binding["agent_binding_id"]
    with _db(base) as conn:
        rows = conn.execute(
            "SELECT pin_id, record_json, progress_json FROM pins "
            "WHERE universe_id = ? AND owner_id = ? AND kind = 'install' "
            "AND state = 'activated'", (uid, owner),
        ).fetchall()
    for row in rows:
        progress = json.loads(row["progress_json"])
        plan = json.loads(row["record_json"]).get("action", {}).get("plan", {})
        for template in plan.get("agent_templates", []):
            encoded = json.dumps([uid, owner, row["pin_id"], template["key"]],
                                 separators=(",", ":")).encode()
            if (progress.get("agents", {}).get(template["key"]) != wanted
                    or "agent_binding_" + hashlib.sha256(encoded).hexdigest() != wanted):
                continue
            slug = plan.get("placement", {}).get("agent_slug")
            if isinstance(slug, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
                return slug
    return None


def _put(universe_dir: Path, relpath: str, data: bytes) -> None:
    """Create ``relpath``; an identical file is a replay, a different one refuses."""
    def text(raw: bytes) -> bytes:
        return raw.replace(b"\r\n", b"\n")  # a CRLF host wrote the same text

    try:
        write_universe_file(universe_dir, relpath, data, mode="exclusive")
    except FileExistsError:
        if text(read_universe_file(universe_dir, relpath)) != text(data):
            raise ValueError(f"agent file already edited or conflicts: {relpath}") from None


def _settings_with_identity(universe_dir: Path, relpath: str, identity: dict) -> bytes:
    """The directory's settings.yaml with the ``agent`` block added or verified."""
    try:
        raw = read_universe_file(universe_dir, relpath)
    except FileNotFoundError:
        doc = {"schema_version": 1}
    else:
        parsed = parse_settings(raw)
        if parsed.agent.id not in (None, identity["id"]):
            raise ValueError(f"{relpath} already names another agent")
        if parsed.legacy_model is not None:
            raise ValueError(f"{relpath} is a legacy model-only file; give it schema_version: 1")
        doc = yaml.safe_load(raw.decode("utf-8")) or {}
    doc["agent"] = {**(doc.get("agent") or {}), **identity}
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    parse_settings(text.encode("utf-8"))  # the file must read back as settings
    return text.encode("utf-8")


def migrate_universe(base_path: str | Path, universe_id: str) -> list[str]:
    """Move one command center's agent bindings into its ``agents/`` directories."""
    from tinyassets.command_center_packages import slug as slugify
    from tinyassets.commons_catalog import (
        _agent_connect,
        _binding_from_row,
        _definition_from_row,
        _read_definition_row,
    )

    base = Path(base_path)
    universe_dir = base / universe_id
    if not universe_dir.is_dir():
        raise ValueError(f"command center directory {universe_id!r} is missing")
    placements: list[str] = []
    with _agent_connect(base) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM agent_bindings WHERE universe_id = ? ORDER BY created_at, "
            "agent_binding_id", (universe_id,),
        ).fetchall()
        try:
            taken = set(list_universe_dir(universe_dir, AGENTS_DIR))
        except FileNotFoundError:
            taken = set()
        migrated = []
        for row in rows:
            binding = _binding_from_row(row)
            definition_row = _read_definition_row(conn, binding["agent_definition_id"])
            definition = (_definition_from_row(conn, definition_row)
                          if definition_row is not None else None)
            if not _is_agent(binding, definition):
                continue
            configuration = dict(binding["configuration"])
            name = str(configuration.pop("name", "") or definition["name"]).strip()
            configuration.pop("schema_version", None)
            slug = _installed_slug(base, binding)
            if slug is None:
                stem = slugify(name)
                slug, n = stem, 1
                while slug in taken:
                    n += 1
                    slug = f"{stem}-{n}"
            taken.add(slug)
            directory = f"{AGENTS_DIR}/{slug}"
            identity = {"id": binding["agent_binding_id"], "name": name}
            if binding.get("retired"):
                identity["retired"] = True
            if configuration:
                identity["configuration"] = configuration
            instructions = _instructions(definition)
            if instructions:
                _put(universe_dir, f"{directory}/{INSTRUCTIONS_FILE}", instructions.encode("utf-8"))
            settings_path = f"{directory}/{SETTINGS_FILE}"
            write_universe_file(universe_dir, settings_path,
                                _settings_with_identity(universe_dir, settings_path, identity))
            migrated.append(binding["agent_binding_id"])
            placements.append(directory)
        for binding_id in migrated:
            conn.execute("DELETE FROM agent_bindings WHERE agent_binding_id = ?", (binding_id,))
    return placements


def migrate_all(base_path: str | Path) -> dict[str, list[str]]:
    """Every command center that holds bindings, each in its own transaction."""
    from tinyassets.commons_catalog import _agent_connect

    with _agent_connect(base_path) as conn:
        universes = [str(row[0]) for row in conn.execute(
            "SELECT DISTINCT universe_id FROM agent_bindings ORDER BY 1")]
    return {uid: migrate_universe(base_path, uid) for uid in universes}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="data root of the backed-up installation")
    parser.add_argument("--universe", help="migrate only this command center")
    args = parser.parse_args()
    result = ({args.universe: migrate_universe(args.base, args.universe)} if args.universe
              else migrate_all(args.base))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

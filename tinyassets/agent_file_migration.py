"""C8 cutover: every agent binding leaves SQL for ``agents/<name>/``.

The daemon runs :func:`migrate` itself, once, before anything serves
(``consumer_runtime.initialize``), so a deploy and the cutover are one event:
no turn, wake or activity can look for an agent between the code that stopped
reading the table and the files that replaced it. ``scripts/migrate_agent_files.py``
runs the same function on a restored backup for a rehearsal.

What moves. Every binding main's addressability rule let an owner talk to
(``addressed_agents.is_conversable`` on main, 2026-10-12: a configured binding
that is neither the conversation-design installation nor a turn consumer),
plus a retired one of those so its thread, activities and wakes keep their id.
Each becomes a directory beside the main agent: ``AGENTS.md`` holds the
instructions the agent spoke with and ``settings.yaml`` names it (``agent.id``
keeps the binding id, ``agent.name`` the owner's name for it, ``agent.owner``
the account that created it, ``retired: true`` for a retired row, any other
configuration verbatim). A binding whose package install already landed
``agents/<slug>/`` is written into that directory; the package's inert copy of
``AGENTS.md`` gives way to the instructions the agent actually spoke with, and
the backup keeps the old bytes.

What stays. The main agent's provider binding is not an agent in either
state: serving, it IS the main agent (main resolved it to ``main``); paused or
not yet consented, it is the row ``onboarding.serving._reconnect_manifest``,
``model_bootstrap_binding.reconnect_owned_binding`` and
``ensure_bootstrap_binding`` resume by ``provider_ref`` or by its untouched
platform definition, and the model-consent request pins its id and revision.
Its definition carries no instructions, so no text an owner read is lost. The
conversation-design installation, turn consumers and catalogue definitions
with no binding stay too.

Replay. One transaction holds the catalogue's write lock for the whole run,
so two daemons or a daemon and the script serialize. Files are written before
the rows go, ``settings.yaml`` (the claim: it carries ``agent.id``) before
``AGENTS.md``, so after a crash at any point the agent exists in at least one
place and the next run finds its directory BY ID rather than allocating a
second slug; every file found is compared byte for byte, a file this run would
not have written refuses. The rows are deleted and the ledger row
(``agent_file_migration``) is inserted in the same commit, after the backup of
every row and every replaced file is on disk. A ledger row ends the matter:
later starts read nothing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tinyassets.agent_definition import (
    AGENT_NAME,
    AGENTS_DIR,
    INSTRUCTIONS_FILE,
    MAX_SETTINGS_BYTES,
    SETTINGS_FILE,
    SettingsError,
    parse_settings,
)
from tinyassets.storage import DB_FILENAME, db_path
from tinyassets.universe_files import (
    list_universe_dir,
    read_universe_file,
    write_universe_file,
)

LEDGER_NAME = "agent-files-c8"
NOT_AN_AGENT_ROLES = frozenset({"app_experience"})
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_log = logging.getLogger(__name__)
_LOCK = threading.Lock()
_DONE: set[str] = set()


@dataclass
class _File:
    path: str
    new: bytes
    old: bytes | None
    #: ``create`` (nothing there), ``replace`` (an inert package copy) or
    #: ``keep`` (already these bytes).
    action: str


@dataclass
class _Placement:
    binding: dict
    definition: dict | None
    directory: str
    files: list[_File] = field(default_factory=list)


def _text(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")  # a CRLF host wrote the same text


def _instructions(definition: dict | None) -> str:
    sections = []
    for key, component in sorted(((definition or {}).get("components") or {}).items()):
        config = component.get("config") if isinstance(component, dict) else None
        text = config.get("instructions") if isinstance(config, dict) else None
        if isinstance(text, str) and text.strip():
            sections.append((key, component.get("kind") or "", text.strip()))
    if len(sections) == 1:
        return sections[0][2] + "\n"
    return "\n\n".join(f"## {key} ({kind})\n\n{text}" if kind else f"## {key}\n\n{text}"
                       for key, kind, text in sections) + ("\n" if sections else "")


def _is_provider_binding(binding: dict, definition: dict | None) -> bool:
    """The main agent's provider binding, in any state (see the module doc)."""
    from tinyassets.onboarding.serving import (
        PLATFORM_DEFINITION_AUTHOR,
        RETIRED_PLATFORM_DEFINITION_AUTHOR,
    )

    return ("provider_ref" in binding["configuration"]
            or (definition or {}).get("author_id") in {
                PLATFORM_DEFINITION_AUTHOR, RETIRED_PLATFORM_DEFINITION_AUTHOR})


def _is_agent(binding: dict, definition: dict | None) -> bool:
    """Main's ``is_conversable`` without its per-caller parts (owner, universe,
    retired), minus the provider binding; a retired agent moves with its id."""
    configuration = binding.get("configuration")
    return bool(
        binding.get("status") == "configured"
        and isinstance(configuration, dict)
        and configuration.get("role") not in NOT_AN_AGENT_ROLES
        and "turn_consumer" not in configuration
        and not _is_provider_binding(binding, definition)
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
            if isinstance(slug, str) and _SLUG.fullmatch(slug):
                return slug
    return None


def _read(universe_dir: Path, relpath: str) -> bytes | None:
    try:
        return read_universe_file(universe_dir, relpath)
    except FileNotFoundError:
        return None


def _index(universe_dir: Path) -> tuple[set[str], dict[str, list[str]]]:
    """Every name under ``agents/`` and, by ``agent.id``, the directories claiming it.

    A directory whose settings cannot be read is occupied, not claimed: the
    roster will refuse it visibly; the migration must not write into it.
    """
    try:
        names = set(list_universe_dir(universe_dir, AGENTS_DIR))
    except FileNotFoundError:
        return set(), {}
    claims: dict[str, list[str]] = {}
    for slug in sorted(names):
        if not AGENT_NAME.fullmatch(slug):
            continue
        try:
            raw = read_universe_file(universe_dir, f"{AGENTS_DIR}/{slug}/{SETTINGS_FILE}",
                                     max_bytes=MAX_SETTINGS_BYTES)
            identity = parse_settings(raw).agent
        except (OSError, SettingsError):
            continue
        if identity.id:
            claims.setdefault(identity.id, []).append(slug)
    return names, claims


def _settings(universe_dir: Path, relpath: str, identity: dict) -> tuple[bytes | None, bytes]:
    """The directory's settings as they are and with the ``agent`` block merged in."""
    raw = _read(universe_dir, relpath)
    if raw is None:
        doc = {"schema_version": 1}
    else:
        parsed = parse_settings(raw)
        if parsed.agent.id not in (None, identity["id"]):
            raise ValueError(f"{relpath} already names another agent")
        if parsed.legacy_model is not None:
            raise ValueError(f"{relpath} is a legacy model-only file; give it schema_version: 1")
        doc = yaml.safe_load(raw.decode("utf-8")) or {}
    doc["agent"] = {**(doc.get("agent") or {}), **identity}
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True).encode("utf-8")
    parse_settings(text)  # the file must read back as settings
    return raw, text


def _file(path: str, old: bytes | None, new: bytes, *, claimed: bool) -> _File | None:
    if old is None:
        return None if not new else _File(path, new, None, "create")
    if _text(old) == _text(new):
        return _File(path, new, old, "keep")
    if claimed:
        raise ValueError(f"agent file already edited or conflicts: {path}")
    return _File(path, new, old, "replace")


def _plan_universe(base: Path, universe_id: str, rows: list, conn) -> list[_Placement]:
    from tinyassets.command_center_packages import slug as slugify
    from tinyassets.commons_catalog import (
        _binding_from_row,
        _definition_from_row,
        _read_definition_row,
    )

    universe_dir = base / universe_id
    if not universe_dir.is_dir():
        raise ValueError(f"command center directory {universe_id!r} is missing; its agent "
                         "bindings cannot be migrated until it is restored")
    taken, claims = _index(universe_dir)
    placements = []
    for row in rows:
        binding = _binding_from_row(row)
        definition_row = _read_definition_row(conn, binding["agent_definition_id"])
        definition = (_definition_from_row(conn, definition_row)
                      if definition_row is not None else None)
        if not _is_agent(binding, definition):
            continue
        binding_id = binding["agent_binding_id"]
        configuration = dict(binding["configuration"])
        name = str(configuration.pop("name", "") or (definition or {}).get("name")
                   or "").strip() or "Unnamed agent"
        configuration.pop("schema_version", None)
        claimed = claims.get(binding_id, [])
        if len(claimed) > 1:
            raise ValueError(f"agent {binding_id!r} is claimed by more than one directory: "
                             + ", ".join(claimed))
        slug = claimed[0] if claimed else _installed_slug(base, binding)
        if slug is None:
            stem = slugify(name)
            slug, n = stem, 1
            while slug in taken:
                n += 1
                slug = f"{stem}-{n}"
        taken.add(slug)
        claims.setdefault(binding_id, [slug])
        directory = f"{AGENTS_DIR}/{slug}"
        identity = {"id": binding_id, "name": name, "owner": binding["created_by"]}
        if binding.get("retired"):
            identity["retired"] = True
        if configuration:
            identity["configuration"] = configuration
        placement = _Placement(binding, definition, directory)
        settings_path = f"{directory}/{SETTINGS_FILE}"
        old, new = _settings(universe_dir, settings_path, identity)
        for file in (
            _file(settings_path, old, new, claimed=bool(claimed)),
            _file(f"{directory}/{INSTRUCTIONS_FILE}",
                  _read(universe_dir, f"{directory}/{INSTRUCTIONS_FILE}"),
                  _instructions(definition).encode("utf-8"), claimed=bool(claimed)),
        ):
            if file is not None:
                placement.files.append(file)
        placements.append(placement)
    return placements


def _write_backup(base: Path, plan: dict[str, list[_Placement]]) -> str:
    """Every row about to be deleted and every file about to be replaced, first."""
    record = {
        "migration": LEDGER_NAME,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bindings": [p.binding for placements in plan.values() for p in placements],
        "definitions": {p.definition["agent_definition_id"]: p.definition
                        for placements in plan.values() for p in placements
                        if p.definition is not None},
        "replaced_files": [
            {"universe_id": uid, "path": file.path,
             "sha256": hashlib.sha256(file.old).hexdigest(),
             "base64": base64.b64encode(file.old).decode("ascii")}
            for uid, placements in plan.items() for p in placements
            for file in p.files if file.action == "replace"
        ],
    }
    data = json.dumps(record, sort_keys=True, indent=1).encode("utf-8")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for counter in range(1000):
        name = f"{DB_FILENAME}.agent-bindings-{stamp}" + (f"-{counter}" if counter else "")
        try:
            write_universe_file(base, name + ".json", data, mode="exclusive")
        except FileExistsError:
            continue
        return name + ".json"
    raise RuntimeError("could not allocate a backup file name")


def _apply(universe_dir: Path, placement: _Placement) -> None:
    for file in placement.files:  # settings (the claim) first, then AGENTS.md
        if file.action == "create":
            write_universe_file(universe_dir, file.path, file.new, mode="exclusive")
        elif file.action == "replace":
            write_universe_file(universe_dir, file.path, file.new)


def _delete(conn, binding_ids: list[str]) -> None:
    for binding_id in binding_ids:
        conn.execute("DELETE FROM agent_bindings WHERE agent_binding_id = ?", (binding_id,))


def _catalogue_exists(base: Path) -> bool:
    """Whether the binding table was ever created here. A fresh installation
    has nothing to migrate and must not grow the catalogue at startup; the
    first binding creates it, and the next start runs the migration then."""
    import sqlite3

    path = db_path(base)
    if not path.is_file():
        return False
    with sqlite3.connect(path, timeout=30.0) as conn:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_bindings'",
        ).fetchone() is not None


def _ledger_done(conn) -> bool:
    return conn.execute("SELECT 1 FROM agent_file_migration WHERE name = ?",
                        (LEDGER_NAME,)).fetchone() is not None


def migrate(base_path: str | Path) -> dict[str, list[str]]:
    """Move every agent binding into its directory; ``{universe_id: [directories]}``.

    Idempotent and exactly-once: a ledger row ends it for every later call in
    any process, and this process asks the ledger once. Raises rather than
    serving with an agent missing: an interrupted or refused run leaves every
    row in place (and in the backup) for the next start to finish.
    """
    from tinyassets.commons_catalog import _agent_connect

    base = Path(base_path)
    key = str(db_path(base))
    if key in _DONE or not _catalogue_exists(base):
        return {}
    with _LOCK, _agent_connect(base) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _ledger_done(conn):
            _DONE.add(key)
            return {}
        rows = conn.execute(
            "SELECT * FROM agent_bindings ORDER BY universe_id, created_at, agent_binding_id",
        ).fetchall()
        by_universe: dict[str, list] = {}
        for row in rows:
            by_universe.setdefault(str(row["universe_id"]), []).append(row)
        plan = {uid: _plan_universe(base, uid, rows, conn) for uid, rows in by_universe.items()}
        plan = {uid: placements for uid, placements in plan.items() if placements}
        backup = _write_backup(base, plan) if plan else ""
        for uid, placements in plan.items():
            for placement in placements:
                _apply(base / uid, placement)
        _delete(conn, [p.binding["agent_binding_id"]
                       for placements in plan.values() for p in placements])
        result = {uid: [p.directory for p in placements] for uid, placements in plan.items()}
        conn.execute(
            "INSERT INTO agent_file_migration (name, applied_at, backup, detail_json) "
            "VALUES (?, ?, ?, ?)",
            (LEDGER_NAME, time.time(), backup, json.dumps(result, sort_keys=True)),
        )
        _DONE.add(key)
    if result:
        _log.warning("agent files: migrated %d binding(s) in %d command center(s); backup %s",
                     sum(len(v) for v in result.values()), len(result), backup)
    return result

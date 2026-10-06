"""app_ui is a projection backend of a pinned extension, never a second revision."""
from __future__ import annotations

import hashlib
import json
import logging

from tinyassets import command_center_packages as packages
from tinyassets import custom_agents

SCHEMA = """
CREATE TABLE IF NOT EXISTS extension_ui_projections (
 owner_id TEXT NOT NULL, universe_id TEXT NOT NULL, agent_id TEXT NOT NULL,
 name TEXT NOT NULL, revision TEXT NOT NULL, generation INTEGER NOT NULL,
 ui_id TEXT NOT NULL, component_json TEXT NOT NULL,
 PRIMARY KEY(owner_id,universe_id,ui_id)
);
"""


def component(state, card, content, identity):
    tag = hashlib.sha256(json.dumps([*identity, state, card["name"]],
                                   sort_keys=True).encode()).hexdigest()[:48]
    raw = content[card["asset"]].decode("utf-8")
    if card["asset"].endswith(".json"):
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("extension UI JSON must be an app_ui component")
        result = {**result, "ui_id": "ta-ext-" + tag}
    else:
        result = {"kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": "ta-ext-" + tag,
                  "name": card["description"], "markup": raw, "style": "", "script": ""}
    custom_agents._refuse_unrenderable(result)
    return result


def project(service, state):
    base = service.backend.root.parent
    owner, universe, agent = service.store.identity
    components = []
    if state["state"] == "active":
        doc, content = service.store.load(state["name"], state["revision"]).content()
        components = [component(state, card, content, service.store.identity)
                      for card in doc.get("cards", [])]
    with packages._db(base) as conn:
        conn.executescript(SCHEMA)
        prior = [r[0] for r in conn.execute(
            "SELECT ui_id FROM extension_ui_projections WHERE owner_id=? AND universe_id=? "
            "AND agent_id=? AND name=?", (owner, universe, agent, state["name"]))]
        for entry in components:
            conn.execute("INSERT OR REPLACE INTO extension_ui_projections VALUES (?,?,?,?,?,?,?,?)",
                         (owner, universe, agent, state["name"], state["revision"],
                          state["generation"], entry["ui_id"], json.dumps(entry)))
    for entry in components:
        custom_agents.change_app_ui_entry(base, owner_user_id=owner, universe_id=universe,
                                          operation="add_ui", payload={"component": entry})
    for ui_id in prior:
        try:
            custom_agents.change_app_ui_entry(base, owner_user_id=owner, universe_id=universe,
                                              operation="remove_ui", payload={"ui_id": ui_id})
        except custom_agents.AgentNotFoundError:
            pass
    return [entry["ui_id"] for entry in components]


def fence(base, owner, universe, document):
    """Even failed cleanup or a direct backend edit cannot serve stale revision code."""
    if not packages.database_path(base).is_file():
        return document
    with packages._db(base) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                              "AND name='extension_ui_projections'").fetchone()
        if not exists:
            return document
        rows = conn.execute(
            "SELECT p.*, a.state AS active_state,a.revision AS active_revision,"
            "a.generation AS active_generation FROM extension_ui_projections p "
            "LEFT JOIN extension_activations a ON p.owner_id=a.owner_id "
            "AND p.universe_id=a.universe_id AND p.agent_id=a.agent_id AND p.name=a.name "
            "WHERE p.owner_id=? AND p.universe_id=?", (owner, universe)).fetchall()
    projections = {row["ui_id"]: row for row in rows}
    library = []
    for entry in document["ui_library"]:
        row = projections.get(entry.get("ui_id")) if isinstance(entry, dict) else None
        if row is None:
            library.append(entry)
            continue
        if (row["active_state"] != "active" or row["revision"] != row["active_revision"]
                or row["generation"] != row["active_generation"]
                or entry != json.loads(row["component_json"])):
            continue
        from types import SimpleNamespace

        from tinyassets.extension_capabilities import ExtensionCapabilities

        ctx = SimpleNamespace(owner=owner, universe=universe, initiating_agent=row["agent_id"])
        service = ExtensionCapabilities(SimpleNamespace(root=base / universe, context=ctx))
        from tinyassets.harness_settings import SettingsError

        try:
            enabled = service._enabled(row["name"])
        except SettingsError:
            logging.getLogger(__name__).warning(
                "Extension UI %s hidden: SettingsError for agent %s",
                row["ui_id"], row["agent_id"], exc_info=True)
            continue
        if enabled:
            library.append(entry)
    selection = document["ui_selection"]
    if isinstance(selection, dict) and selection.get("ui_id") in projections and not any(
            entry.get("ui_id") == selection["ui_id"] for entry in library):
        selection = {"version": 1, "state": "default"}
    return {**document, "ui_library": library, "ui_selection": selection}

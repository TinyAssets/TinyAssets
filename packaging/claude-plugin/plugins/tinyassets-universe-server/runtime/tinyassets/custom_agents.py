"""Composable public agent definitions and private universe bindings.

This module owns the durable custom-agent domain model.  Public definitions
are immutable commons artifacts; universe bindings are private operational
configuration.  Runtime activation intentionally lives elsewhere.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator

from tinyassets.ids import new_ulid
from tinyassets.storage import db_path

AGENT_SCHEMA_VERSION = 1
MAX_AGENT_JSON_BYTES = 256 * 1024
#: Kept, unlike the component count: the stored lineage table carries it as a
#: CHECK constraint, so lifting it is a rebuild of an attribution table, and a
#: remix chain 50 generations deep is not a shape anyone has built.
MAX_LINEAGE_DEPTH = 50

_COMPONENT_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CONTENT_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_SECRET_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "bot_token",
        "channel_secret",
        "client_secret",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secrets",
        "session_token",
        "signing_secret",
        "token",
        "webhook_secret",
    }
)
_PRIVATE_DEFINITION_FIELDS = frozenset(
    {
        "chat_history",
        "conversation",
        "conversation_history",
        "conversations",
        "effect_payload",
        "effect_payloads",
        "execution_history",
        "external_write_results",
        "message_history",
        "messages",
        "run_history",
        "run_state",
        "runtime_state",
    }
)
_SENSITIVE_FIELD_SUFFIXES = (
    "_api_key",
    "_authorization",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)
_CREDENTIAL_VALUE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/-]{3,}|gh[pousr]_[a-z0-9_=-]{12,}|"
    r"xox[baprs]-[a-z0-9-]{12,}|sk-[a-z0-9_-]{16,}|"
    r"eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
)
_FORBIDDEN_BINDING_CONTENT_FIELDS = frozenset(
    {
        "conversation",
        "conversation_history",
        "conversations",
        "effect_payload",
        "effect_payloads",
        "external_write_results",
        "message_history",
        "messages",
        "run_state",
        "runtime_state",
        "transcript",
        "transcripts",
    }
)
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_INITIALIZED: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_definitions (
    agent_definition_id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    tags_json TEXT NOT NULL DEFAULT '[]',
    components_json TEXT NOT NULL,
    external_origins_json TEXT NOT NULL DEFAULT '[]',
    portable_json TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    idempotency_key TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_definition_idempotency
    ON agent_definitions(author_id, idempotency_key)
    WHERE idempotency_key <> '';

CREATE INDEX IF NOT EXISTS idx_agent_definition_created
    ON agent_definitions(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_definition_author
    ON agent_definitions(author_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_component_lineage (
    child_definition_id TEXT NOT NULL,
    child_component_key TEXT NOT NULL,
    parent_definition_id TEXT NOT NULL,
    parent_component_key TEXT NOT NULL,
    credit_share REAL NOT NULL
        CHECK (credit_share >= 0.0 AND credit_share <= 1.0),
    generation_depth INTEGER NOT NULL
        CHECK (generation_depth >= 1 AND generation_depth <= 50),
    created_at REAL NOT NULL,
    PRIMARY KEY (
        child_definition_id,
        child_component_key,
        parent_definition_id,
        parent_component_key
    ),
    FOREIGN KEY(child_definition_id)
        REFERENCES agent_definitions(agent_definition_id) ON DELETE CASCADE,
    FOREIGN KEY(parent_definition_id)
        REFERENCES agent_definitions(agent_definition_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_agent_lineage_parent
    ON agent_component_lineage(parent_definition_id, parent_component_key);

CREATE TABLE IF NOT EXISTS agent_bindings (
    agent_binding_id TEXT PRIMARY KEY,
    universe_id TEXT NOT NULL,
    agent_definition_id TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    status TEXT NOT NULL DEFAULT 'configured'
        CHECK (status IN ('configured', 'serving')),
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(agent_definition_id)
        REFERENCES agent_definitions(agent_definition_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_agent_binding_universe
    ON agent_bindings(universe_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_binding_definition
    ON agent_bindings(agent_definition_id);

-- Which user-authored UIs a person keeps in a universe, and which one they are
-- using. Deliberately NOT an agent binding: a UI choice has no definition, is
-- never served, and must not appear to any binding reader. One row per
-- (person, universe); the primary key is what makes a concurrent first save
-- produce one row rather than two.
CREATE TABLE IF NOT EXISTS universe_app_ui (
    owner_user_id TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    ui_library_json TEXT NOT NULL,
    ui_selection_json TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at REAL NOT NULL,
    PRIMARY KEY (owner_user_id, universe_id)
);

-- The bytes a person's UIs load: textures, audio, fonts, models, JS/CSS files.
-- Content-addressed per owner, so an edit is a new hash and a texture two UIs
-- share is stored once. A UI names a blob by path in its `assets` map, and the
-- media type lives in that reference (from the path's extension), never here:
-- one shared row must not carry a property two references can disagree on.
-- Nothing here knows which UI or universe uses it. Only the owner reads a row.
CREATE TABLE IF NOT EXISTS universe_app_ui_asset (
    owner_user_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    content BLOB NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (owner_user_id, sha256)
);
"""


class AgentValidationError(ValueError):
    """An agent document violates the public persistence contract."""


class AgentConflictError(AgentValidationError):
    """An idempotency or revision precondition conflicts with stored state."""


class AgentNotFoundError(LookupError):
    """A requested definition or universe-scoped binding does not exist."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AgentValidationError(f"payload must be JSON-compatible: {exc}") from exc


def _json_clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _check_size(value: Any) -> None:
    size = len(_canonical_json(value).encode("utf-8"))
    if size > MAX_AGENT_JSON_BYTES:
        raise AgentValidationError(
            f"payload exceeds {MAX_AGENT_JSON_BYTES} bytes of canonical JSON"
        )


def _is_sensitive_field_name(raw_key: Any) -> bool:
    raw_text = str(raw_key)
    key = raw_text.strip().casefold().replace("-", "_")
    return (
        key in _SECRET_FIELD_NAMES
        or key in _PRIVATE_DEFINITION_FIELDS
        or key.endswith(_SENSITIVE_FIELD_SUFFIXES)
        or bool(_CREDENTIAL_VALUE.search(raw_text))
    )


def _looks_sensitive_value(value: Any) -> bool:
    return isinstance(value, str) and bool(_CREDENTIAL_VALUE.search(value))


def _check_secret_fields(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            safe_key = "[credential-shaped-key]" if _looks_sensitive_value(key) else key
            child_path = f"{path}.{safe_key}" if path else safe_key
            if _is_sensitive_field_name(key):
                raise AgentValidationError(
                    f"{child_path} contains forbidden credential/private runtime content"
                )
            _check_secret_fields(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_secret_fields(child, f"{path}[{index}]")
    elif _looks_sensitive_value(value):
        raise AgentValidationError(f"{path} contains a credential-shaped value")


def _check_binding_content_fields(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if key.strip().lower() in _FORBIDDEN_BINDING_CONTENT_FIELDS:
                raise AgentValidationError(
                    f"{child_path} is private operational content, "
                    "not binding configuration"
                )
            _check_binding_content_fields(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_binding_content_fields(child, f"{path}[{index}]")


def _normalize_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AgentValidationError("tags must be a JSON list")
    if len(raw) > 32:
        raise AgentValidationError("tags may contain at most 32 values")
    tags: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            raise AgentValidationError(f"tags[{index}] must be a non-empty string")
        tag = value.strip()
        if len(tag) > 64:
            raise AgentValidationError(f"tags[{index}] exceeds 64 characters")
        if tag not in tags:
            tags.append(tag)
    return tags


def _normalize_components(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        raise AgentValidationError("components must be a non-empty JSON object")
    # No count of components: the definition is bounded by its canonical JSON
    # bytes (MAX_AGENT_JSON_BYTES), not by how it is divided (plan item 6).
    components: dict[str, dict[str, Any]] = {}
    for raw_key, raw_component in raw.items():
        key = str(raw_key)
        path = f"components.{key}"
        if not _COMPONENT_KEY.fullmatch(key):
            raise AgentValidationError(f"{path} is not a valid component key")
        if not isinstance(raw_component, dict):
            raise AgentValidationError(f"{path} must be a JSON object")
        component = _json_clone(raw_component)
        kind = component.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise AgentValidationError(f"{path}.kind must be a non-empty string")
        component["kind"] = kind.strip()
        components[key] = component
    return components


def _normalize_lineage(
    raw: Any,
    components: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AgentValidationError("lineage must be a JSON object")
    lineage: dict[str, list[dict[str, Any]]] = {}
    for raw_child_key, raw_sources in raw.items():
        child_key = str(raw_child_key)
        path = f"lineage.{child_key}"
        if child_key not in components:
            raise AgentValidationError(f"{path} names no child component")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise AgentValidationError(f"{path} must be a non-empty JSON list")
        sources: list[dict[str, Any]] = []
        total = 0.0
        seen: set[tuple[str, str]] = set()
        for index, raw_source in enumerate(raw_sources):
            source_path = f"{path}[{index}]"
            if not isinstance(raw_source, dict):
                raise AgentValidationError(f"{source_path} must be a JSON object")
            definition_id = str(raw_source.get("definition_id") or "").strip()
            component_key = str(raw_source.get("component_key") or "").strip()
            if not definition_id:
                raise AgentValidationError(f"{source_path}.definition_id is required")
            if not _COMPONENT_KEY.fullmatch(component_key):
                raise AgentValidationError(f"{source_path}.component_key is invalid")
            try:
                share = float(raw_source.get("credit_share"))
            except (TypeError, ValueError) as exc:
                raise AgentValidationError(
                    f"{source_path}.credit_share must be a finite number"
                ) from exc
            if not math.isfinite(share) or share < 0.0 or share > 1.0:
                raise AgentValidationError(f"{source_path}.credit_share must be within [0, 1]")
            source_key = (definition_id, component_key)
            if source_key in seen:
                raise AgentValidationError(f"{source_path} duplicates a source")
            seen.add(source_key)
            total += share
            source = {
                "definition_id": definition_id,
                "component_key": component_key,
                "credit_share": share,
            }
            definition_fingerprint = str(
                raw_source.get("definition_fingerprint") or ""
            ).strip()
            component_fingerprint = str(
                raw_source.get("component_fingerprint") or ""
            ).strip()
            if bool(definition_fingerprint) != bool(component_fingerprint):
                raise AgentValidationError(
                    f"{source_path} must supply both lineage fingerprints"
                )
            if definition_fingerprint:
                if not _CONTENT_FINGERPRINT.fullmatch(definition_fingerprint):
                    raise AgentValidationError(
                        f"{source_path}.definition_fingerprint is invalid"
                    )
                if not _CONTENT_FINGERPRINT.fullmatch(component_fingerprint):
                    raise AgentValidationError(
                        f"{source_path}.component_fingerprint is invalid"
                    )
                source["definition_fingerprint"] = definition_fingerprint
                source["component_fingerprint"] = component_fingerprint
            sources.append(source)
        if total > 1.0 + 1e-9:
            raise AgentValidationError(f"{path} credit shares total {total:g}, exceeding 1.0")
        lineage[child_key] = sorted(
            sources,
            key=lambda item: (
                item["definition_id"],
                item["component_key"],
            ),
        )
    return dict(sorted(lineage.items()))


def _normalize_definition_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AgentValidationError("definition payload must be a JSON object")
    cloned = _json_clone(payload)
    schema_version = cloned.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != AGENT_SCHEMA_VERSION:
        raise AgentValidationError(f"schema_version must equal {AGENT_SCHEMA_VERSION}")
    name = cloned.get("name")
    if not isinstance(name, str) or not name.strip():
        raise AgentValidationError("name must be a non-empty string")
    name = name.strip()
    if len(name) > 120:
        raise AgentValidationError("name exceeds 120 characters")
    description = cloned.get("description", "")
    if not isinstance(description, str):
        raise AgentValidationError("description must be a string")
    if len(description) > 4000:
        raise AgentValidationError("description exceeds 4000 characters")

    components = _normalize_components(cloned.get("components"))
    lineage = _normalize_lineage(cloned.get("lineage"), components)
    external_origins = cloned.get("external_origins", [])
    if not isinstance(external_origins, list):
        raise AgentValidationError("external_origins must be a JSON list")
    external_origins = _json_clone(external_origins)

    normalized = {
        "schema_version": AGENT_SCHEMA_VERSION,
        "name": name,
        "description": description,
        "tags": _normalize_tags(cloned.get("tags")),
        "components": components,
        "lineage": lineage,
        "external_origins": external_origins,
    }
    if "bundle_id" in cloned:
        bundle_id = cloned["bundle_id"]
        if not isinstance(bundle_id, str) or not re.fullmatch(r"bundle_[0-9a-f]{32}", bundle_id):
            raise AgentValidationError("bundle_id must be a platform-minted bundle identity")
        normalized["bundle_id"] = bundle_id
    _check_secret_fields(normalized)
    _check_size(normalized)
    return normalized


def _normalize_binding_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AgentValidationError("binding payload must be a JSON object")
    cloned = _json_clone(payload)
    schema_version = cloned.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != AGENT_SCHEMA_VERSION:
        raise AgentValidationError(f"schema_version must equal {AGENT_SCHEMA_VERSION}")
    name = cloned.get("name")
    if not isinstance(name, str) or not name.strip():
        raise AgentValidationError("name must be a non-empty string")
    cloned["name"] = name.strip()
    if len(cloned["name"]) > 120:
        raise AgentValidationError("name exceeds 120 characters")
    reserved = {
        "agent_binding_id",
        "agent_definition_id",
        "created_at",
        "created_by",
        "provider_ref",
        "revision",
        "status",
        "universe_id",
        "updated_at",
        "updated_by",
    }
    collision = sorted(reserved.intersection(cloned))
    if collision:
        raise AgentValidationError(f"binding payload contains reserved field {collision[0]}")
    _check_secret_fields(cloned)
    _check_binding_content_fields(cloned)
    _check_size(cloned)
    return cloned


def _ensure_schema(base_path: str | Path) -> Path:
    path = db_path(base_path)
    key = str(path)
    if key in _SCHEMA_INITIALIZED:
        return path
    with _SCHEMA_LOCK:
        if key in _SCHEMA_INITIALIZED:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            with conn:
                conn.executescript(_SCHEMA)
                _migrate_serving_status(conn)
                if "retired" not in {row[1] for row in conn.execute(
                    "PRAGMA table_info(agent_bindings)"
                )}:
                    conn.execute(
                        "ALTER TABLE agent_bindings ADD COLUMN retired INTEGER NOT NULL "
                        "DEFAULT 0 CHECK (retired IN (0, 1))"
                    )
                if "retirement_revision" not in {row[1] for row in conn.execute(
                    "PRAGMA table_info(agent_bindings)"
                )}:
                    conn.execute(
                        "ALTER TABLE agent_bindings ADD COLUMN retirement_revision "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                from tinyassets.commons_bundles import backfill, ensure_schema

                conn.execute("BEGIN IMMEDIATE")
                ensure_schema(conn)
                backfill(conn, base_path)
        finally:
            conn.close()
        _SCHEMA_INITIALIZED.add(key)
    return path


def _migrate_serving_status(conn: sqlite3.Connection) -> None:
    """Expand the binding-status CHECK on databases created before slice 1."""

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agent_bindings'"
    ).fetchone()
    sql = str(row[0] or "") if row is not None else ""
    if "'serving'" in sql:
        return
    conn.execute("DROP INDEX IF EXISTS idx_agent_binding_universe")
    conn.execute("DROP INDEX IF EXISTS idx_agent_binding_definition")
    conn.execute("ALTER TABLE agent_bindings RENAME TO agent_bindings_pre_serving")
    conn.executescript(
        """
        CREATE TABLE agent_bindings (
            agent_binding_id TEXT PRIMARY KEY,
            universe_id TEXT NOT NULL,
            agent_definition_id TEXT NOT NULL,
            configuration_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            status TEXT NOT NULL DEFAULT 'configured'
                CHECK (status IN ('configured', 'serving')),
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(agent_definition_id)
                REFERENCES agent_definitions(agent_definition_id) ON DELETE RESTRICT
        );
        INSERT INTO agent_bindings (
            agent_binding_id, universe_id, agent_definition_id,
            configuration_json, revision, status, created_by, updated_by,
            created_at, updated_at
        )
        SELECT agent_binding_id, universe_id, agent_definition_id,
               configuration_json, revision, status, created_by, updated_by,
               created_at, updated_at
          FROM agent_bindings_pre_serving;
        DROP TABLE agent_bindings_pre_serving;
        CREATE INDEX idx_agent_binding_universe
            ON agent_bindings(universe_id, updated_at DESC);
        CREATE INDEX idx_agent_binding_definition
            ON agent_bindings(agent_definition_id);
        """
    )


@contextmanager
def _agent_connect(
    base_path: str | Path,
) -> Iterator[sqlite3.Connection]:
    path = _ensure_schema(base_path)
    conn = sqlite3.connect(path, timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn
    finally:
        conn.close()


def _lineage_rows(
    conn: sqlite3.Connection,
    definition_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT child_component_key, parent_definition_id,
               parent_component_key, credit_share, generation_depth
        FROM agent_component_lineage
        WHERE child_definition_id = ?
        ORDER BY child_component_key, parent_definition_id,
                 parent_component_key
        """,
        (definition_id,),
    ).fetchall()
    return [
        {
            "child_component_key": str(row["child_component_key"]),
            "parent_definition_id": str(row["parent_definition_id"]),
            "parent_component_key": str(row["parent_component_key"]),
            "credit_share": float(row["credit_share"]),
            "generation_depth": int(row["generation_depth"]),
        }
        for row in rows
    ]


def _definition_from_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, Any]:
    portable = json.loads(str(row["portable_json"]))
    portable["content_fingerprint"] = str(row["content_fingerprint"])
    return {
        "agent_definition_id": str(row["agent_definition_id"]),
        "author_id": str(row["author_id"]),
        "schema_version": int(row["schema_version"]),
        "name": str(row["name"]),
        "description": str(row["description"]),
        "tags": json.loads(str(row["tags_json"])),
        "components": json.loads(str(row["components_json"])),
        "external_origins": json.loads(str(row["external_origins_json"])),
        "content_fingerprint": str(row["content_fingerprint"]),
        "created_at": float(row["created_at"]),
        "lineage": _lineage_rows(conn, str(row["agent_definition_id"])),
        "portable_definition": portable,
    }


def _read_definition_row(
    conn: sqlite3.Connection,
    definition_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM agent_definitions
        WHERE agent_definition_id = ?
        """,
        (definition_id,),
    ).fetchone()


def _lineage_edge(
    conn: sqlite3.Connection,
    *,
    child_key: str,
    source: dict[str, Any],
    parent_id: str,
) -> dict[str, Any]:
    parent_key = source["component_key"]
    depth_row = conn.execute(
        """
        SELECT MAX(generation_depth) AS depth
        FROM agent_component_lineage
        WHERE child_definition_id = ?
          AND child_component_key = ?
        """,
        (parent_id, parent_key),
    ).fetchone()
    generation_depth = int(depth_row["depth"] or 0) + 1
    if generation_depth > MAX_LINEAGE_DEPTH:
        raise AgentValidationError(
            f"lineage may not exceed {MAX_LINEAGE_DEPTH} generations"
        )
    return {
        "child_component_key": child_key,
        "parent_definition_id": parent_id,
        "parent_component_key": parent_key,
        "credit_share": float(source["credit_share"]),
        "generation_depth": generation_depth,
    }


def _parent_component(
    parent: sqlite3.Row,
    component_key: str,
) -> dict[str, Any] | None:
    components = json.loads(str(parent["components_json"]))
    component = components.get(component_key)
    return component if isinstance(component, dict) else None


def _enrich_local_lineage(
    conn: sqlite3.Connection,
    normalized: dict[str, Any],
) -> dict[str, Any]:
    enriched = copy.deepcopy(normalized)
    for sources in enriched["lineage"].values():
        for source in sources:
            parent_id = source["definition_id"]
            parent_key = source["component_key"]
            parent = _read_definition_row(conn, parent_id)
            component = (
                _parent_component(parent, parent_key) if parent is not None else None
            )
            if parent is None or component is None:
                raise AgentValidationError(
                    f"parent component {parent_id}.{parent_key} does not exist"
                )
            definition_fingerprint = str(parent["content_fingerprint"])
            component_fingerprint = _fingerprint(component)
            supplied_definition = source.get("definition_fingerprint")
            supplied_component = source.get("component_fingerprint")
            if supplied_definition and supplied_definition != definition_fingerprint:
                raise AgentValidationError(
                    f"parent definition fingerprint does not match {parent_id}"
                )
            if supplied_component and supplied_component != component_fingerprint:
                raise AgentValidationError(
                    f"parent component fingerprint does not match {parent_id}.{parent_key}"
                )
            source["definition_fingerprint"] = definition_fingerprint
            source["component_fingerprint"] = component_fingerprint
    _check_size(enriched)
    return enriched


def _verified_local_lineage(
    conn: sqlite3.Connection,
    lineage: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for child_key, sources in lineage.items():
        for source in sources:
            parent_id = source["definition_id"]
            parent_key = source["component_key"]
            parent = _read_definition_row(conn, parent_id)
            component = (
                _parent_component(parent, parent_key) if parent is not None else None
            )
            if parent is None or component is None:
                raise AgentValidationError(
                    f"parent component {parent_id}.{parent_key} does not exist"
                )
            verified.append(
                _lineage_edge(
                    conn,
                    child_key=child_key,
                    source=source,
                    parent_id=parent_id,
                )
            )
    return verified


def _verified_import_lineage(
    conn: sqlite3.Connection,
    lineage: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for child_key, sources in lineage.items():
        for source in sources:
            parent_key = source["component_key"]
            definition_fingerprint = source.get("definition_fingerprint")
            component_fingerprint = source.get("component_fingerprint")
            if not definition_fingerprint or not component_fingerprint:
                continue
            rows = conn.execute(
                """
                SELECT *
                FROM agent_definitions
                WHERE content_fingerprint = ?
                ORDER BY agent_definition_id
                """,
                (definition_fingerprint,),
            ).fetchall()
            matches = [
                row
                for row in rows
                if (
                    (component := _parent_component(row, parent_key)) is not None
                    and _fingerprint(component) == component_fingerprint
                )
            ]
            if len(matches) == 1:
                verified.append(
                    _lineage_edge(
                        conn,
                        child_key=child_key,
                        source=source,
                        parent_id=str(matches[0]["agent_definition_id"]),
                    )
                )
    return verified


def _author_and_key(author_id: str, idempotency_key: str) -> tuple[str, str]:
    from tinyassets.principals import named_principal

    actor = named_principal(author_id)
    if not actor:
        raise AgentValidationError("an authenticated author_id is required")
    key = (idempotency_key or "").strip()
    if len(key) > 128:
        raise AgentValidationError("idempotency_key exceeds 128 characters")
    return actor, key


def _publish_normalized(
    conn: sqlite3.Connection,
    *,
    actor: str,
    normalized: dict[str, Any],
    key: str,
    imported: bool,
) -> dict[str, Any]:
    from tinyassets.commons_bundles import _source_bundle, append, authorize

    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    stored = copy.deepcopy(normalized) if imported else _enrich_local_lineage(conn, normalized)
    bundle_id = stored.get("bundle_id", "")
    if bundle_id and imported:
        bundle_id = _source_bundle(conn, actor, "import", {"name": bundle_id + ":" + key})
        stored["bundle_id"] = bundle_id
    if bundle_id:
        authorize(conn, author=actor, bundle_id=bundle_id)
    content_fingerprint = _fingerprint(stored)

    if key:
        existing = conn.execute(
            """
            SELECT *
            FROM agent_definitions
            WHERE author_id = ? AND idempotency_key = ?
            """,
            (actor, key),
        ).fetchone()
        if existing is not None:
            if existing["content_fingerprint"] != content_fingerprint:
                raise AgentConflictError(
                    "idempotency_key was already used for different content"
                )
            return _definition_from_row(conn, existing)

    verified_lineage = (
        _verified_import_lineage(conn, stored["lineage"])
        if imported
        else _verified_local_lineage(conn, stored["lineage"])
    )
    package = stored["components"].get("package", {})
    if bundle_id and not imported and package.get("bundle_id") == bundle_id:
        expected = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM commons_bundle_versions WHERE bundle_id=?",
            (bundle_id,),
        ).fetchone()[0]
        if package.get("version") != expected:
            raise AgentConflictError("another bundle version was published meanwhile; ask again")
    definition_id = f"agent_{new_ulid()}"
    created_at = time.time()
    insert_sql = """
        INSERT INTO agent_definitions (
            agent_definition_id, author_id, name, description,
            schema_version, tags_json, components_json,
            external_origins_json, portable_json, content_fingerprint,
            idempotency_key, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    if key:
        insert_sql += " ON CONFLICT DO NOTHING"
    cursor = conn.execute(
        insert_sql,
        (
            definition_id,
            actor,
            stored["name"],
            stored["description"],
            AGENT_SCHEMA_VERSION,
            _canonical_json(stored["tags"]),
            _canonical_json(stored["components"]),
            _canonical_json(stored["external_origins"]),
            _canonical_json(stored),
            content_fingerprint,
            key,
            created_at,
        ),
    )
    if cursor.rowcount == 0:
        existing = conn.execute(
            """
            SELECT *
            FROM agent_definitions
            WHERE author_id = ? AND idempotency_key = ?
            """,
            (actor, key),
        ).fetchone()
        if existing is None:
            raise AgentConflictError("definition write conflicted")
        if existing["content_fingerprint"] != content_fingerprint:
            raise AgentConflictError(
                "idempotency_key was already used for different content"
            )
        return _definition_from_row(conn, existing)
    for edge in verified_lineage:
        conn.execute(
            """
            INSERT INTO agent_component_lineage (
                child_definition_id, child_component_key,
                parent_definition_id, parent_component_key,
                credit_share, generation_depth, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                definition_id,
                edge["child_component_key"],
                edge["parent_definition_id"],
                edge["parent_component_key"],
                edge["credit_share"],
                edge["generation_depth"],
                created_at,
            ),
        )
    row = _read_definition_row(conn, definition_id)
    assert row is not None
    if bundle_id:
        append(conn, author=actor, bundle_id=bundle_id, definition_id=definition_id)
    return _definition_from_row(conn, row)


def publish_definition(
    base_path: str | Path,
    *,
    author_id: str,
    payload: dict[str, Any],
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Publish one immutable public definition and verified lineage."""

    actor, key = _author_and_key(author_id, idempotency_key)
    normalized = _normalize_definition_payload(payload)
    with _agent_connect(base_path) as conn:
        return _publish_normalized(
            conn,
            actor=actor,
            normalized=normalized,
            key=key,
            imported=False,
        )


def get_definition(
    base_path: str | Path,
    definition_id: str,
    *,
    include_catalogue: bool = False,
) -> dict[str, Any] | None:
    with _agent_connect(base_path) as conn:
        row = _read_definition_row(conn, (definition_id or "").strip())
        if row is None:
            return None
        definition = _definition_from_row(conn, row)
        if include_catalogue:
            from tinyassets.commons_bundles import metadata

            definition.update(metadata(conn, row["agent_definition_id"]))
        return definition


def list_definitions(
    base_path: str | Path,
    *,
    query: str = "",
    tags: list[str] | tuple[str, ...] = (),
    exclude_tags: list[str] | tuple[str, ...] = (),
    author_id: str = "",
    limit: int = 30,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if type(offset) is not int or offset < 0:
        raise AgentValidationError("offset must be a non-negative integer")
    matched = 0
    bounded_limit = max(1, min(int(limit), 100))
    wanted_query = (query or "").strip().casefold()
    wanted_tags = {str(tag).strip() for tag in tags if str(tag).strip()}
    excluded_tags = {str(tag).strip() for tag in exclude_tags if str(tag).strip()}
    wanted_author = (author_id or "").strip()

    with _agent_connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM agent_definitions AS d
            WHERE NOT EXISTS (
                SELECT 1 FROM commons_bundle_versions AS v
                JOIN commons_bundle_versions AS newer
                  ON newer.bundle_id = v.bundle_id AND newer.version > v.version
                WHERE v.definition_id = d.agent_definition_id
            )
            ORDER BY created_at DESC, agent_definition_id DESC
            """
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            if wanted_author and row["author_id"] != wanted_author:
                continue
            row_tags = set(json.loads(str(row["tags_json"])))
            if wanted_tags and not wanted_tags.issubset(row_tags):
                continue
            if excluded_tags.intersection(row_tags):
                continue
            if wanted_query:
                haystack = f"{row['name']} {row['description']}".casefold()
                if wanted_query not in haystack:
                    continue
            matched += 1
            if matched <= offset:
                continue
            from tinyassets.commons_bundles import metadata

            results.append({**_definition_from_row(conn, row),
                            **metadata(conn, row["agent_definition_id"])})
            if len(results) >= bounded_limit:
                break
        return results


def import_definition(
    base_path: str | Path,
    *,
    author_id: str,
    portable_definition: dict[str, Any],
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Validate a portable export and republish it into the local commons."""

    if not isinstance(portable_definition, dict):
        raise AgentValidationError("portable_definition must be a JSON object")
    supplied = _json_clone(portable_definition)
    supplied_fingerprint = str(supplied.pop("content_fingerprint", "")).strip()
    normalized = _normalize_definition_payload(supplied)
    if supplied_fingerprint and supplied_fingerprint != _fingerprint(normalized):
        raise AgentValidationError("portable definition fingerprint does not match")
    actor, key = _author_and_key(author_id, idempotency_key)
    with _agent_connect(base_path) as conn:
        return _publish_normalized(
            conn,
            actor=actor,
            normalized=normalized,
            key=key,
            imported=True,
        )


def _binding_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "agent_binding_id": str(row["agent_binding_id"]),
        "universe_id": str(row["universe_id"]),
        "agent_definition_id": str(row["agent_definition_id"]),
        "configuration": json.loads(str(row["configuration_json"])),
        "revision": int(row["revision"]),
        "status": str(row["status"]),
        "retired": bool(row["retired"]),
        "retirement_revision": int(row["retirement_revision"]),
        "created_by": str(row["created_by"]),
        "updated_by": str(row["updated_by"]),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
    }


def _read_binding_row(
    conn: sqlite3.Connection,
    *,
    universe_id: str,
    binding_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM agent_bindings
        WHERE universe_id = ? AND agent_binding_id = ?
        """,
        (universe_id, binding_id),
    ).fetchone()


def _require_definition(
    conn: sqlite3.Connection,
    definition_id: str,
) -> None:
    if _read_definition_row(conn, definition_id) is None:
        raise AgentNotFoundError(f"agent definition {definition_id!r} was not found")


#: Roles of which an actor holds at most one binding per universe. The
#: conversation-design installation is read at turn admission, where two active
#: ones make the owner's conversation ambiguous (consumer_selection.py).
SINGLETON_BINDING_ROLES = frozenset({"app_experience"})


def create_binding(
    base_path: str | Path,
    *,
    universe_id: str,
    definition_id: str,
    created_by: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create private universe configuration for a public definition.

    A binding whose ``configuration.role`` is a singleton role (see
    ``SINGLETON_BINDING_ROLES``) is created only if this actor has none with
    that role in this universe yet. That is the create's compare-and-set: two
    clients that both read "none yet" cannot both create the conversation-design
    installation, which would leave turn admission ambiguous. The check and the
    insert are ONE statement, so it holds under concurrency.
    """

    uid = (universe_id or "").strip()
    did = (definition_id or "").strip()
    from tinyassets.principals import named_principal

    actor = named_principal(created_by)
    if not uid:
        raise AgentValidationError("universe_id is required")
    if not did:
        raise AgentValidationError("definition_id is required")
    if not actor:
        raise AgentValidationError("an authenticated created_by actor is required")
    configuration = _normalize_binding_payload(payload)
    role = configuration.get("role")
    if not isinstance(role, str) or role not in SINGLETON_BINDING_ROLES:
        role = None
    binding_id = f"agent_binding_{new_ulid()}"
    created_at = time.time()

    with _agent_connect(base_path) as conn:
        _require_definition(conn, did)
        cursor = conn.execute(
            """
            INSERT INTO agent_bindings (
                agent_binding_id, universe_id, agent_definition_id,
                configuration_json, revision, status, created_by, updated_by,
                created_at, updated_at
            )
            SELECT ?, ?, ?, ?, 1, 'configured', ?, ?, ?, ?
            WHERE ? IS NULL OR NOT EXISTS (
                SELECT 1 FROM agent_bindings
                WHERE universe_id = ? AND created_by = ?
                  AND json_extract(configuration_json, '$.role') = ?
            )
            """,
            (
                binding_id,
                uid,
                did,
                _canonical_json(configuration),
                actor,
                actor,
                created_at,
                created_at,
                role,
                uid,
                actor,
                role,
            ),
        )
        if cursor.rowcount != 1:
            raise AgentConflictError(
                f"a binding with role {role!r} already exists in this command center; "
                "read it and update it instead"
            )
        row = _read_binding_row(
            conn,
            universe_id=uid,
            binding_id=binding_id,
        )
        assert row is not None
        return _binding_from_row(row)


def get_binding(
    base_path: str | Path,
    *,
    universe_id: str,
    binding_id: str,
) -> dict[str, Any] | None:
    with _agent_connect(base_path) as conn:
        row = _read_binding_row(
            conn,
            universe_id=(universe_id or "").strip(),
            binding_id=(binding_id or "").strip(),
        )
        return _binding_from_row(row) if row is not None else None


def serving_binding_candidates(
    base_path: str | Path,
    *,
    universe_id: str,
    owner_user_id: str,
) -> list[dict[str, Any]]:
    """Read enough exact owner-serving matches to detect absent/unique/ambiguous.

    Filtering precedes the bound: inactive or other-owner bindings cannot hide
    an active binding. Two rows prove ambiguity without enumerating all agents.
    """
    with _agent_connect(base_path) as conn:
        rows = conn.execute(
            "SELECT * FROM agent_bindings "
            "WHERE universe_id = ? AND created_by = ? AND status = 'serving' "
            "ORDER BY agent_binding_id LIMIT 2",
            (universe_id, owner_user_id),
        ).fetchall()
        return [_binding_from_row(row) for row in rows]


def reconnect_binding_candidates_in_transaction(
    conn: sqlite3.Connection, *, universe_id: str, owner: str, provider_ref: str,
) -> list[dict[str, Any]]:
    """Select serving first, or unique configured recovery, in one read snapshot."""
    rows = conn.execute(
        "SELECT * FROM agent_bindings WHERE universe_id = ? AND created_by = ? "
        "AND status = 'serving' ORDER BY agent_binding_id LIMIT 2",
        (universe_id, owner),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT * FROM agent_bindings WHERE universe_id = ? AND created_by = ? "
            "AND status = 'configured' "
            "AND json_extract(configuration_json, '$.provider_ref') = ? "
            "ORDER BY agent_binding_id LIMIT 2",
            (universe_id, owner, provider_ref),
        ).fetchall()
    return [_binding_from_row(row) for row in rows]


def list_bindings(
    base_path: str | Path,
    *,
    universe_id: str,
    limit: int | None = 30,
    include_retired: bool = True,
) -> list[dict[str, Any]]:
    """The owner's bindings, newest first, at most ``limit`` of them.

    ``limit=None`` is every binding: a caller that must FIND one (the deposit's
    binding hint) reads them all rather than a page it then filters.

    ``limit`` is honoured as asked. It used to be silently clamped to 100, so a
    caller that asked for more got exactly 100 back and could not tell whether
    that was the whole list or a page -- which is how the app ended up disabling
    its own Apply button at a hundred rows. A page size the caller chooses is a
    read preference; a ceiling the platform imposes on it is a limit, and an
    account has exactly two (founder, 2026-09-30).
    """
    uid = (universe_id or "").strip()
    page = -1 if limit is None else max(1, int(limit))  # SQLite: LIMIT -1 is none
    with _agent_connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM agent_bindings
            WHERE universe_id = ? AND (? OR retired = 0)
            ORDER BY updated_at DESC, agent_binding_id DESC
            LIMIT ?
            """,
            (uid, include_retired, page),
        ).fetchall()
        return [_binding_from_row(row) for row in rows]


def set_binding_retired(
    base_path: str | Path, *, universe_id: str, binding_id: str,
    expected_revision: int, updated_by: str, retired: bool,
) -> dict[str, Any]:
    """Reversibly retire an owner's agent; keep its identity and all content."""
    from tinyassets.addressed_agents import is_conversable
    from tinyassets.principals import named_principal
    from tinyassets.provider_assignment import provider_assignment_admission

    actor = named_principal(updated_by)
    uid, bid = universe_id.strip(), binding_id.strip()
    if not actor or not uid or not bid:
        raise AgentValidationError("owner, command center and binding are required")
    if bid == "main":
        raise AgentValidationError("the main agent cannot be retired")
    if (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
            or expected_revision < 1):
        raise AgentValidationError("expected_revision must be a positive integer")
    with provider_assignment_admission().exclusive(Path(base_path) / uid):
        with _agent_connect(base_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _read_binding_row(conn, universe_id=uid, binding_id=bid)
            if row is None or row["created_by"] != actor:
                raise AgentNotFoundError("agent binding was not found")
            binding = _binding_from_row(row)
            definition = _read_definition_row(conn, binding["agent_definition_id"])
            from tinyassets.onboarding.serving import (
                PLATFORM_DEFINITION_AUTHOR,
                RETIRED_PLATFORM_DEFINITION_AUTHOR,
            )

            if (definition["author_id"] in {
                PLATFORM_DEFINITION_AUTHOR, RETIRED_PLATFORM_DEFINITION_AUTHOR,
            } or "provider_ref" in binding["configuration"]):
                raise AgentValidationError("the main agent cannot be retired")
            if not is_conversable({**binding, "retired": False}, owner=actor, universe_id=uid):
                raise AgentValidationError(
                    "the main agent or a non-agent binding cannot be retired"
                )
            cursor = conn.execute(
                "UPDATE agent_bindings SET retired=?, "
                "retirement_revision=CASE WHEN ? THEN revision+1 ELSE retirement_revision END, "
                "revision=revision+1, updated_by=?, "
                "updated_at=? WHERE universe_id=? AND agent_binding_id=? "
                "AND created_by=? AND revision=?",
                (int(retired), int(retired), actor, time.time(), uid, bid, actor,
                 expected_revision),
            )
            if cursor.rowcount != 1:
                raise AgentConflictError("binding revision conflict; read the binding again")
            result = _binding_from_row(_read_binding_row(conn, universe_id=uid, binding_id=bid))
        if retired:
            from tinyassets.turn_interrupt import request_interrupt

            request_interrupt(actor, uid, agent_id=bid, reason="agent retired")
            from tinyassets.activity_runner import stop
            from tinyassets.agent_activities import fence_agent

            for run_id in fence_agent(Path(base_path) / uid, owner=actor, agent_id=bid):
                stop(Path(base_path), run_id)
    return result


def update_binding(
    base_path: str | Path,
    *,
    universe_id: str,
    binding_id: str,
    expected_revision: int,
    updated_by: str,
    payload: dict[str, Any],
    definition_id: str = "",
) -> dict[str, Any]:
    """Replace binding configuration using an atomic revision precondition."""

    uid = (universe_id or "").strip()
    bid = (binding_id or "").strip()
    from tinyassets.principals import named_principal

    actor = named_principal(updated_by)
    if not uid or not bid:
        raise AgentValidationError("universe_id and binding_id are required")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise AgentValidationError("expected_revision must be a positive integer")
    if not actor:
        raise AgentValidationError("an authenticated updated_by actor is required")
    configuration = _normalize_binding_payload(payload)
    requested_definition = (definition_id or "").strip()
    updated_at = time.time()

    from tinyassets.provider_assignment import provider_assignment_admission

    universe_dir = Path(base_path) / uid
    with provider_assignment_admission().exclusive(universe_dir):
        with _agent_connect(base_path) as conn:
            current = _read_binding_row(
                conn,
                universe_id=uid,
                binding_id=bid,
            )
            if current is None:
                raise AgentNotFoundError(f"agent binding {bid!r} was not found")
            if current["retired"]:
                raise AgentValidationError(
                    "agent retired; restore it before updating configuration"
                )
            selected_definition = requested_definition or str(
                current["agent_definition_id"]
            )
            _require_definition(conn, selected_definition)
            cursor = conn.execute(
                """
                UPDATE agent_bindings
                SET agent_definition_id = ?,
                    configuration_json = ?,
                    revision = revision + 1,
                    updated_by = ?,
                    updated_at = ?
                WHERE universe_id = ?
                  AND agent_binding_id = ?
                  AND revision = ?
                """,
                (
                    selected_definition,
                    _canonical_json(configuration),
                    actor,
                    updated_at,
                    uid,
                    bid,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                actual = _read_binding_row(
                    conn,
                    universe_id=uid,
                    binding_id=bid,
                )
                actual_revision = (
                    int(actual["revision"]) if actual is not None else None
                )
                raise AgentConflictError(
                    f"binding revision conflict: expected {expected_revision}, "
                    f"found {actual_revision}"
                )
            updated = _read_binding_row(
                conn,
                universe_id=uid,
                binding_id=bid,
            )
            assert updated is not None
            return _binding_from_row(updated)


def set_binding_provider_ref_in_transaction(
    conn: sqlite3.Connection,
    *,
    universe_id: str,
    binding_id: str,
    expected_revision: int,
    owner_user_id: str,
    provider_ref: str,
) -> dict[str, Any]:
    """Server-only CAS for the authority-validated provider selection."""

    if not conn.in_transaction:
        raise ValueError("provider binding update requires an active transaction")
    current = _read_binding_row(
        conn,
        universe_id=universe_id.strip(),
        binding_id=binding_id.strip(),
    )
    if current is None:
        raise AgentNotFoundError(f"agent binding {binding_id!r} was not found")
    if str(current["created_by"]) != owner_user_id.strip():
        raise PermissionError("only the binding creator may assign its provider")
    if current["retired"]:
        raise PermissionError("agent retired; restore it before assigning a provider")
    if int(current["revision"]) != expected_revision:
        raise AgentConflictError(
            f"binding revision conflict: expected {expected_revision}, "
            f"found {int(current['revision'])}"
        )
    configuration = json.loads(str(current["configuration_json"]))
    configuration["provider_ref"] = provider_ref.strip()
    updated_at = time.time()
    cursor = conn.execute(
        """
        UPDATE agent_bindings
           SET configuration_json = ?, revision = revision + 1,
               status = 'configured', updated_by = ?, updated_at = ?
         WHERE universe_id = ? AND agent_binding_id = ? AND revision = ?
        """,
        (
            _canonical_json(configuration),
            owner_user_id.strip(),
            updated_at,
            universe_id.strip(),
            binding_id.strip(),
            expected_revision,
        ),
    )
    if cursor.rowcount != 1:
        raise AgentConflictError("binding changed during provider assignment")
    updated = _read_binding_row(
        conn,
        universe_id=universe_id.strip(),
        binding_id=binding_id.strip(),
    )
    assert updated is not None
    return _binding_from_row(updated)


def set_binding_serving_in_transaction(
    conn: sqlite3.Connection,
    *,
    universe_id: str,
    binding_id: str,
    expected_revision: int,
    owner_user_id: str,
    enabled: bool,
) -> dict[str, Any]:
    """Server-only CAS for configured/serving intent."""

    if not conn.in_transaction:
        raise ValueError("serving update requires an active transaction")
    current = _read_binding_row(
        conn,
        universe_id=universe_id.strip(),
        binding_id=binding_id.strip(),
    )
    if current is None:
        raise AgentNotFoundError(f"agent binding {binding_id!r} was not found")
    if str(current["created_by"]) != owner_user_id.strip():
        raise PermissionError("only the binding creator may change serving state")
    if current["retired"]:
        raise PermissionError("agent retired; restore it before changing serving state")
    if int(current["revision"]) != expected_revision:
        raise AgentConflictError(
            f"binding revision conflict: expected {expected_revision}, "
            f"found {int(current['revision'])}"
        )
    if not isinstance(enabled, bool):
        raise AgentValidationError("enabled must be a boolean")
    status = "serving" if enabled else "configured"
    cursor = conn.execute(
        """
        UPDATE agent_bindings
           SET status = ?, updated_by = ?, updated_at = ?
         WHERE universe_id = ? AND agent_binding_id = ? AND revision = ?
        """,
        (
            status,
            owner_user_id.strip(),
            time.time(),
            universe_id.strip(),
            binding_id.strip(),
            expected_revision,
        ),
    )
    if cursor.rowcount != 1:
        raise AgentConflictError("binding changed during serving transition")
    updated = _read_binding_row(
        conn,
        universe_id=universe_id.strip(),
        binding_id=binding_id.strip(),
    )
    assert updated is not None
    return _binding_from_row(updated)


#: There is NO bound on a person's UI library, by count OR by bytes. The 4 MiB
#: ``MAX_APP_UI_LIBRARY_BYTES`` that used to live here was a separate storage
#: number, refusing an install with "remove one first"; its own comment said it
#: should be charged against a per-universe storage quota once one existed. One
#: does, so it is: these bytes count toward tier storage, which is one of the two
#: limits an account has (founder, 2026-09-30 -- the other is concurrent agent
#: seats). Per-UI bundle validation is unchanged: that is payload validation of
#: one document, not an account limit.
_APP_UI_FIELDS = frozenset({"ui_library", "ui_selection"})
_MAX_APP_UI_SELECTION_BYTES = 1024

#: Payload bounds on ONE UI, not account limits: the account's limit is its
#: storage, which every byte below is charged to. They exist because a UI is
#: held whole in the viewer's tab and its text is read by a bounded model. The
#: 49,152-byte component bound these replace made a game impossible (founder's
#: village, 2026-10-02); code past 1 MiB of text belongs in a JS asset.
APP_UI_MAX_COMPONENT_TEXT_BYTES = 1024 * 1024
APP_UI_MAX_ASSET_BYTES = 16 * 1024 * 1024
APP_UI_MAX_UI_ASSET_BYTES = 128 * 1024 * 1024
APP_UI_MAX_ASSET_FILES = 500
APP_UI_SCRIPT_TYPES = ("classic", "module")
_ASSET_PATH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*\Z")
_ASSET_PATH_MAX = 200
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
#: The closed table of what a UI may load. The type is what the frame's Blob is
#: given; the HTTP response that carries the bytes is always octet-stream.
APP_UI_ASSET_MEDIA_TYPES = MappingProxyType({
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
    ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".wav": "audio/wav", ".m4a": "audio/mp4", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".gltf": "model/gltf+json", ".glb": "model/gltf-binary",
    ".bin": "application/octet-stream", ".ktx2": "image/ktx2",
    ".hdr": "image/vnd.radiance",
    ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
    ".json": "application/json", ".txt": "text/plain", ".csv": "text/csv",
    ".xml": "application/xml", ".atlas": "text/plain", ".fnt": "text/plain",
})
#: A blob no UI references is kept this long before an asset write sweeps it,
#: so a `put_asset` never races its own reference away.
_ASSET_GC_GRACE_SECONDS = 3600.0


def app_ui_asset_media_type(path: str) -> str:
    """The media type ``path``'s extension maps to, or AgentValidationError."""
    suffix = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    media_type = APP_UI_ASSET_MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise AgentValidationError(
            f"asset {path!r}: extension {suffix or '(none)'} is not one a UI can load; "
            f"use one of {sorted(APP_UI_ASSET_MEDIA_TYPES)}"
        )
    return media_type


def _check_asset_path(path: Any) -> str:
    if (not isinstance(path, str) or len(path) > _ASSET_PATH_MAX
            or not _ASSET_PATH_RE.match(path) or "/../" in f"/{path}/"):
        raise AgentValidationError(
            f"asset path {path!r} must be up to {_ASSET_PATH_MAX} characters of "
            "slash-separated segments of letters, digits, dot, dash or underscore"
        )
    return path


def app_ui_workflow_refs(entry: dict[str, Any]) -> dict[str, str]:
    """Validate the explicit portable bindings; never interpret script text."""
    refs = entry.get("workflow_refs", {})
    if not isinstance(refs, dict) or len(refs) > 100:
        raise AgentValidationError("workflow_refs must be an object of at most 100 references")
    for alias, workflow in refs.items():
        if (not isinstance(alias, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", alias)
                or not isinstance(workflow, str) or not workflow
                or workflow != workflow.strip() or _utf16_units(workflow) > 200):
            raise AgentValidationError("workflow_refs contains an invalid alias or workflow id")
    return dict(refs)


def app_ui_agent_refs(entry: dict[str, Any]) -> dict[str, str]:
    """Declared agent aliases have the same shape as workflow aliases."""
    try:
        return app_ui_workflow_refs({"workflow_refs": entry.get("agent_refs", {})})
    except AgentValidationError as exc:
        raise AgentValidationError(str(exc).replace("workflow", "agent")) from None


def _check_component(entry: dict[str, Any]) -> None:
    """The bounds and shape of the fields the server stores for one UI.

    The seven rendering fields are the app's to read (it names what it cannot
    render); what the server must own is anything its own stores depend on --
    the asset manifest, the library names, and the text a model reads.
    """
    ui_id = entry["ui_id"]
    text_bytes = len(_canonical_json(entry).encode("utf-8"))
    if text_bytes > APP_UI_MAX_COMPONENT_TEXT_BYTES:
        raise AgentValidationError(
            f"UI {ui_id!r} is {text_bytes} bytes of text; the bound is "
            f"{APP_UI_MAX_COMPONENT_TEXT_BYTES}. Move code into a JS asset (put_asset)"
        )
    if "script_type" in entry and entry["script_type"] not in APP_UI_SCRIPT_TYPES:
        raise AgentValidationError(
            f"UI {ui_id!r}: script_type must be one of {list(APP_UI_SCRIPT_TYPES)}"
        )
    if "libraries" in entry:
        from tinyassets.onboarding import ui_library_set

        try:
            ui_library_set.check_names(entry["libraries"])
        except ValueError as exc:
            raise AgentValidationError(f"UI {ui_id!r}: {exc}") from None
    app_ui_workflow_refs(entry)
    app_ui_agent_refs(entry)
    if "assets" not in entry:
        return
    assets = entry["assets"]
    if not isinstance(assets, dict):
        raise AgentValidationError(f"UI {ui_id!r}: assets must be an object of path -> blob")
    if len(assets) > APP_UI_MAX_ASSET_FILES:
        raise AgentValidationError(
            f"UI {ui_id!r} has {len(assets)} assets; the bound is {APP_UI_MAX_ASSET_FILES}"
        )
    total = 0
    for path, ref in assets.items():
        _check_asset_path(path)
        if (not isinstance(ref, dict) or set(ref) != {"sha256", "size", "media_type"}
                or not isinstance(ref["sha256"], str) or not _SHA256_RE.match(ref["sha256"])
                or type(ref["size"]) is not int
                or not 0 <= ref["size"] <= APP_UI_MAX_ASSET_BYTES
                or ref["media_type"] != app_ui_asset_media_type(path)):
            raise AgentValidationError(
                f"UI {ui_id!r}: asset {path!r} must be exactly "
                '{"sha256": <hex>, "size": <bytes>, "media_type": <its extension\'s type>}; '
                "put_asset writes it for you"
            )
        total += ref["size"]
    if total > APP_UI_MAX_UI_ASSET_BYTES:
        raise AgentValidationError(
            f"UI {ui_id!r} assets total {total} bytes; the bound is {APP_UI_MAX_UI_ASSET_BYTES}"
        )


def _begin_write(conn: sqlite3.Connection) -> None:
    """Take the database write lock NOW, before anything is read.

    A deferred transaction reads without a lock, so an asset sweep could commit
    between a writer's check that a blob is held and its row write (Codex,
    2026-10-02: a save returned success naming a blob already deleted). With the
    lock taken first, the check and the write are one serial step: a sweep that
    ran first leaves the check to refuse; one that runs after sees the reference.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _referenced_assets(library: list[Any]) -> list[tuple[str, str, dict[str, Any]]]:
    refs = []
    for entry in library:
        if isinstance(entry, dict) and isinstance(entry.get("assets"), dict):
            for path, ref in entry["assets"].items():
                refs.append((str(entry.get("ui_id")), path, ref))
    return refs


def _check_assets_held(conn: sqlite3.Connection, owner: str, library: list[Any]) -> None:
    """Every blob ``library`` names is stored for ``owner``, as named.

    A row can never point at bytes its owner does not hold -- not another
    person's blob by hash, not one swept away -- whichever path wrote it. The
    caller holds the write lock (:func:`_begin_write`), so no sweep can delete a
    blob between this check and the row write that references it.
    """
    refs = _referenced_assets(library)
    if not refs:
        return
    hashes = sorted({ref["sha256"] for _, _, ref in refs})
    held: dict[str, int] = {}
    for start in range(0, len(hashes), 500):
        chunk = hashes[start:start + 500]
        for row in conn.execute(
            "SELECT sha256, size_bytes FROM universe_app_ui_asset "
            f"WHERE owner_user_id = ? AND sha256 IN ({','.join('?' * len(chunk))})",
            (owner, *chunk),
        ):
            held[str(row["sha256"])] = int(row["size_bytes"])
    for ui_id, path, ref in refs:
        if held.get(ref["sha256"]) != ref["size"]:
            raise AgentValidationError(
                f"UI {ui_id!r}: asset {path!r} names bytes that are not in your UI storage; "
                "write it with put_asset"
            )


def _app_ui_document(row: sqlite3.Row | None, universe_id: str) -> dict[str, Any]:
    if row is None:
        return {"universe_id": universe_id, "ui_library": [], "ui_selection": None,
                "revision": 0, "updated_at": None}
    selection = row["ui_selection_json"]
    return {
        "universe_id": universe_id,
        "ui_library": json.loads(str(row["ui_library_json"])),
        "ui_selection": None if selection is None else json.loads(str(selection)),
        "revision": int(row["revision"]),
        "updated_at": float(row["updated_at"]),
    }


def _app_ui_scope(owner_user_id: str, universe_id: str) -> tuple[str, str]:
    from tinyassets.principals import named_principal

    owner = named_principal(owner_user_id)
    uid = (universe_id or "").strip()
    if not owner:
        raise AgentValidationError("an authenticated owner is required")
    if not uid:
        raise AgentValidationError("universe_id is required")
    return owner, uid


def _check_app_ui_fields(changes: dict[str, Any]) -> None:
    unknown = sorted(set(changes) - _APP_UI_FIELDS)
    if unknown:
        raise AgentValidationError(
            f"app UI payload field {unknown[0]!r} is not one of {sorted(_APP_UI_FIELDS)}"
        )
    if not changes:
        raise AgentValidationError("app UI payload must set ui_library or ui_selection")
    if "ui_library" in changes:
        library = changes["ui_library"]
        if not isinstance(library, list):
            raise AgentValidationError("ui_library must be a list")
        seen: set[str] = set()
        for entry in library:
            ui_id = entry.get("ui_id") if isinstance(entry, dict) else None
            if not isinstance(ui_id, str) or not ui_id:
                raise AgentValidationError("every ui_library entry must be an object with a ui_id")
            if ui_id in seen:
                raise AgentValidationError(f"ui_id {ui_id!r} is listed twice")
            seen.add(ui_id)
            _check_component(entry)
    if "ui_selection" in changes and not isinstance(changes["ui_selection"], dict):
        raise AgentValidationError("ui_selection must be an object")


def get_app_ui(
    base_path: str | Path, *, owner_user_id: str, universe_id: str,
) -> dict[str, Any]:
    """The caller's own UI library and choice in ``universe_id``.

    An absent row reads as an empty library at revision 0 -- the state a fresh
    account is in, and the revision a first save names.
    """

    owner, uid = _app_ui_scope(owner_user_id, universe_id)
    with _agent_connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM universe_app_ui WHERE owner_user_id = ? AND universe_id = ?",
            (owner, uid),
        ).fetchone()
    from tinyassets.extension_ui import fence

    return fence(Path(base_path), owner, uid, _app_ui_document(row, uid))


def save_app_ui(
    base_path: str | Path,
    *,
    owner_user_id: str,
    universe_id: str,
    expected_revision: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Compare-and-set the caller's UI library and/or choice.

    Only the fields in ``changes`` are written; an omitted field keeps its stored
    value, so saving a choice can never erase the library. ``expected_revision``
    is the revision last read -- 0 when no row exists yet. Each branch is ONE
    statement, so there is no read-then-write window: two first saves racing
    both name revision 0, the primary key admits one row, and the other gets
    :class:`AgentConflictError`.
    """

    owner, uid = _app_ui_scope(owner_user_id, universe_id)
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise AgentValidationError("expected_revision must be a non-negative integer")
    if not isinstance(changes, dict):
        raise AgentValidationError("app UI payload must be a JSON object")
    _check_app_ui_fields(changes)
    # The library has no size bound -- its bytes are the universe's tier storage.
    # The SELECTION still does: it is one small pointer document, and a payload
    # bound on a single document is not an account limit.
    selection_bytes = len(_canonical_json(changes.get("ui_selection")).encode("utf-8"))
    if selection_bytes > _MAX_APP_UI_SELECTION_BYTES:
        raise AgentValidationError(
            f"ui_selection exceeds {_MAX_APP_UI_SELECTION_BYTES} bytes of canonical JSON"
        )
    library = (_canonical_json(changes["ui_library"])
               if "ui_library" in changes else None)
    selection = (_canonical_json(changes["ui_selection"])
                 if "ui_selection" in changes else None)
    from tinyassets import storage_accounting

    # The library's bytes are the saver's account storage (account-storage-quota
    # D7): at the quota this raises `StorageRefused` before anything is written.
    account = owner if storage_accounting.is_account(base_path, owner) else None
    reservation = storage_accounting.reserve(
        base_path, account_id=account, scope_id=account or "", store="ui_library",
        nbytes=sum(len(s.encode("utf-8")) for s in (library or "", selection or "")),
    )
    try:
        saved = _save_app_ui_row(
            base_path, owner=owner, uid=uid, expected_revision=expected_revision,
            library=library, selection=selection,
            library_doc=changes.get("ui_library"),
        )
    except BaseException:
        storage_accounting.release(reservation)
        raise
    storage_accounting.commit(reservation)
    return saved


def _save_app_ui_row(
    base_path: str | Path,
    *,
    owner: str,
    uid: str,
    expected_revision: int,
    library: str | None,
    selection: str | None,
    library_doc: list[Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    with _agent_connect(base_path) as conn:
        _begin_write(conn)
        if library_doc is not None:
            _check_assets_held(conn, owner, library_doc)
        if expected_revision == 0:
            written = conn.execute(
                """
                INSERT INTO universe_app_ui (
                    owner_user_id, universe_id, ui_library_json, ui_selection_json,
                    revision, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(owner_user_id, universe_id) DO NOTHING
                """,
                (owner, uid, library if library is not None else "[]", selection, now),
            ).rowcount
        else:
            written = conn.execute(
                """
                UPDATE universe_app_ui
                   SET ui_library_json = COALESCE(?, ui_library_json),
                       ui_selection_json = COALESCE(?, ui_selection_json),
                       revision = revision + 1,
                       updated_at = ?
                 WHERE owner_user_id = ? AND universe_id = ? AND revision = ?
                """,
                (library, selection, now, owner, uid, expected_revision),
            ).rowcount
        row = conn.execute(
            "SELECT * FROM universe_app_ui WHERE owner_user_id = ? AND universe_id = ?",
            (owner, uid),
        ).fetchone()
    if written != 1:
        current = 0 if row is None else int(row["revision"])
        raise AgentConflictError(
            f"app UI changed: expected revision {expected_revision}, current is {current}; "
            "read it again before saving"
        )
    return _app_ui_document(row, uid)


# ---- One UI at a time --------------------------------------------------------
# A universe edits a person's UIs by TALKING, which means through a model whose
# tool results are bounded (engine_result_bounds). A whole-row compare-and-set
# needs the whole library read first, and a library larger than that bound is
# cut before its revision: live 2026-09-30, the founder asked to try "GTM Village"
# and their universe could not switch the screen. These operations name ONE UI
# (or only the choice) and never need the library or its revision. Each one
# re-reads the row and writes it back under ``revision = <the one it read>``, and
# re-runs on a lost race, so nothing another writer saved in between -- the
# app's whole-library save included -- is overwritten. The row revision still
# advances, so the app's own compare-and-set sees the change.

#: Fields ``edit_ui`` may change. ``kind``, ``version`` and ``ui_id`` are what
#: the component IS; changing those is a ``replace_ui``. ``assets`` changes one
#: path at a time through ``put_asset`` / ``remove_asset``.
APP_UI_EDITABLE_FIELDS = ("name", "markup", "style", "script", "libraries", "script_type",
                          "workflow_refs", "agent_refs")
_APP_UI_TEXT_FIELDS = ("name", "markup", "style", "script")
APP_UI_ENTRY_OPERATIONS = (
    "activate", "use_default", "add_ui", "replace_ui", "edit_ui", "remove_ui",
    "put_asset", "remove_asset",
)
#: The second half of ``put_asset``: the blob is stored, now point the path at
#: it. Internal -- a caller cannot name a hash it has not just written.
_SET_ASSET = "_set_asset"
_APP_UI_ENTRY_ATTEMPTS = 8


def app_ui_etag(component: Any) -> str:
    """A short digest of one UI, so an edit can require the version it read."""
    return hashlib.sha256(_canonical_json(component).encode("utf-8")).hexdigest()[:16]


#: The app's rendering contract, mirrored here so a READ can say which stored
#: UI the app will refuse and why. The app stays the authority on rendering
#: (see ``_check_component``: the server owns only what its own stores depend
#: on); this adds no refusal, it only reports.
#: ``tests/test_app_ui_renderability.py`` holds these equal to app_ui.js.
APP_UI_KIND = "tinyassets.app-ui.v1"
#: The FORMAT version of the component, not a revision and not a cache-buster.
#: It is always 1. An agent that set it to a timestamp hid every UI the person
#: had built (founder, P1, 2026-10-03).
APP_UI_FORMAT_VERSION = 1
APP_UI_COMPONENT_FIELDS = ("kind", "markup", "name", "script", "style", "ui_id", "version")
APP_UI_OPTIONAL_COMPONENT_FIELDS = ("assets", "libraries", "script_type", "workflow_refs",
                                    "agent_refs")
#: Matched with ``fullmatch``, never ``match``: Python's ``$`` also matches
#: BEFORE a trailing newline, so ``"x" * 64 + "\n"`` passed here while the app
#: refused it -- a mirror saying "renderable" about a UI the app will not show
#: is worse than no report at all (Codex, 2026-10-03).
_APP_UI_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
APP_UI_MAX_NAME = 120


def _utf16_units(text: str) -> int:
    """The length JavaScript measures: UTF-16 code units, not code points.

    ``name.length <= MAX_NAME`` in the app counts surrogate pairs twice, so 61
    emoji are 122 units there and 61 characters here. Python said renderable
    about a name the app refuses (Codex, 2026-10-03).
    """
    return len(text.encode("utf-16-le")) // 2


def app_ui_renderability(entry: Any) -> dict[str, str]:
    """``{}`` when the app can render ``entry``, else its reason and a fix.

    Only the checks the WRITE path does not already make, which is exactly the
    set a stored entry can still fail: the field list, ``kind``, the format
    ``version``, and the shape of ``ui_id``, ``name`` and the three text
    fields. Bounds, assets, libraries and ``script_type`` are enforced by
    ``_check_component`` on the way in, so a stored entry has passed them.
    """
    if not isinstance(entry, dict):
        return {"reason": "UI component is not an object",
                "hint": "replace_ui with a JSON object component"}
    keys = set(entry)
    extra = sorted(keys - set(APP_UI_COMPONENT_FIELDS) - set(APP_UI_OPTIONAL_COMPONENT_FIELDS))
    if extra:
        return {"reason": "UI component carries fields this app does not render: "
                          + ", ".join(extra),
                "hint": f"remove {', '.join(extra)}; the app refuses any field outside "
                        f"{list(APP_UI_COMPONENT_FIELDS)} plus "
                        f"{list(APP_UI_OPTIONAL_COMPONENT_FIELDS)}"}
    missing = [f for f in APP_UI_COMPONENT_FIELDS if f not in keys]
    if missing:
        return {"reason": "UI component is missing " + ", ".join(missing),
                "hint": f"replace_ui with all of {list(APP_UI_COMPONENT_FIELDS)} present"}
    if entry["kind"] != APP_UI_KIND:
        return {"reason": f"not a {APP_UI_KIND} component",
                "hint": f'set "kind" to "{APP_UI_KIND}"'}
    if entry["version"] != APP_UI_FORMAT_VERSION or isinstance(entry["version"], bool):
        return {"reason": f"UI version {entry['version']!r} is not supported; this app renders "
                          f"version {APP_UI_FORMAT_VERSION}",
                "hint": f'"version" is the component FORMAT version and is always '
                        f'{APP_UI_FORMAT_VERSION}; it is not a revision or a cache-buster. '
                        f'Set it back to {APP_UI_FORMAT_VERSION} with replace_ui. Nothing needs '
                        "busting: the app re-reads this row whenever its revision moves, and an "
                        "asset is addressed by its own sha256"}
    if (not isinstance(entry["ui_id"], str) or _utf16_units(entry["ui_id"]) > 64
            or not _APP_UI_ID_RE.fullmatch(entry["ui_id"])):
        return {"reason": "ui_id must be lowercase letters, digits or dashes",
                "hint": "use up to 64 characters of lowercase letters, digits or dashes"}
    if (not isinstance(entry["name"], str) or not entry["name"].strip()
            or _utf16_units(entry["name"]) > APP_UI_MAX_NAME):
        return {"reason": "name must be a non-empty string of at most "
                          f"{APP_UI_MAX_NAME} characters",
                "hint": f"set a name of 1 to {APP_UI_MAX_NAME} characters"}
    for field in ("markup", "style", "script"):
        if not isinstance(entry[field], str):
            return {"reason": f"{field} must be a string",
                    "hint": f'set "{field}" to a string (empty is fine)'}
    # The rest of the contract -- script_type, library names, the asset manifest
    # and the text bound -- is already defined once, by the check the write path
    # makes. Delegating keeps this a COMPLETE mirror of what the app renders
    # rather than a partial one: a differential test compares every verdict
    # against app_ui.js, and a partial mirror that says "renderable" about a UI
    # the app refuses is worse than no report (Codex, 2026-10-03, which found
    # script_type and libraries diverging exactly here).
    try:
        _check_component(entry)
    except AgentValidationError as exc:
        return {"reason": str(exc), "hint": "correct it with replace_ui"}
    return {}


def app_ui_index(document: dict[str, Any]) -> dict[str, Any]:
    """The row without any UI body: what a model reads to pick a target."""
    entries = []
    for entry in document.get("ui_library") or []:
        if not isinstance(entry, dict):
            continue
        summary = {
            "ui_id": entry.get("ui_id"),
            "name": entry.get("name"),
            "etag": app_ui_etag(entry),
            "chars": {field: len(entry[field]) for field in ("markup", "style", "script")
                      if isinstance(entry.get(field), str)},
        }
        # Per UI, so one bad component is diagnosable instead of making the
        # whole library read as broken (founder, P1, 2026-10-03).
        refusal = app_ui_renderability(entry)
        summary["renderable"] = not refusal
        if refusal:
            summary["reason"] = refusal["reason"]
            summary["fix"] = refusal["hint"]
        if isinstance(entry.get("assets"), dict):
            # Paths and sizes, not bodies: what a model needs to reference one.
            summary["assets"] = {path: ref.get("size") for path, ref in entry["assets"].items()
                                 if isinstance(ref, dict)}
        for field in ("libraries", "script_type"):
            if field in entry:
                summary[field] = entry[field]
        entries.append(summary)
    return {
        "universe_id": document.get("universe_id"),
        "revision": document.get("revision"),
        "ui_selection": document.get("ui_selection"),
        "uis": entries,
    }


def _entry_position(library: list[Any], ui_id: str) -> int:
    for position, entry in enumerate(library):
        if isinstance(entry, dict) and entry.get("ui_id") == ui_id:
            return position
    return -1


def _named_ui_id(payload: dict[str, Any]) -> str:
    ui_id = payload.get("ui_id")
    if not isinstance(ui_id, str) or not ui_id.strip():
        raise AgentValidationError("ui_id is required")
    return ui_id.strip()


def _missing_ui(ui_id: str, library: list[Any]) -> AgentNotFoundError:
    installed = [e.get("ui_id") for e in library if isinstance(e, dict)]
    return AgentNotFoundError(
        f"no UI with ui_id {ui_id!r} in this library; installed: {installed}"
    )


def _check_etag(payload: dict[str, Any], entry: dict[str, Any]) -> None:
    expected = payload.get("expected_etag")
    if expected in (None, ""):
        return
    if expected != app_ui_etag(entry):
        raise AgentConflictError(
            f"UI {entry.get('ui_id')!r} changed since it was read "
            f"(etag is now {app_ui_etag(entry)}); read it again before editing"
        )


def _refuse_unrenderable(component: Any) -> None:
    """Refuse a component no app can ever render, naming the reason and the fix.

    The write and the read now share ONE definition of a valid component. They
    did not: the write checked only what the server's own stores depend on, so
    ``replace_ui`` ACCEPTED ``"version": 1791005187`` and answered with a
    success receipt (revision 50 -> 51), while the app refused to render it.
    The agent, told it had saved, assured the founder the UI was intact
    (founder, P1, 2026-10-03).

    Only ``add_ui`` and ``replace_ui`` go through here -- a whole component
    supplied by the caller. ``save`` deliberately does NOT: it writes the whole
    library, and the app carries entries it cannot render through that write so
    they are not destroyed. Refusing there would make a stored bad entry
    impossible to write back, which is the data loss this guards against.
    """
    refusal = app_ui_renderability(component)
    if refusal:
        raise AgentValidationError(f"{refusal['reason']}. {refusal['hint']}")


def _component(payload: dict[str, Any]) -> dict[str, Any]:
    component = payload.get("component")
    if not isinstance(component, dict):
        raise AgentValidationError("component must be an object")
    _check_app_ui_fields({"ui_library": [component]})
    _refuse_unrenderable(component)
    return component


def _edited_entry(entry: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    changes = payload.get("set") or {}
    edits = payload.get("edits") or []
    if not isinstance(changes, dict) or not isinstance(edits, list):
        raise AgentValidationError("set must be an object and edits a list")
    if not changes and not edits:
        raise AgentValidationError("edit_ui needs set and/or edits")
    edited = dict(entry)
    for field, value in changes.items():
        if field not in APP_UI_EDITABLE_FIELDS:
            raise AgentValidationError(
                f"set field {field!r} is not one of {list(APP_UI_EDITABLE_FIELDS)}"
            )
        if field == "libraries" and not isinstance(value, list):
            raise AgentValidationError("set.libraries must be a list of library names")
        if field == "workflow_refs":
            app_ui_workflow_refs({"workflow_refs": value})
        if field == "agent_refs":
            app_ui_agent_refs({"agent_refs": value})
        if field not in {"libraries", "workflow_refs", "agent_refs"} and not isinstance(value, str):
            raise AgentValidationError(f"set.{field} must be a string")
        edited[field] = value
    for number, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise AgentValidationError(f"edits[{number}] must be an object")
        field, old, new = edit.get("field"), edit.get("old"), edit.get("new")
        if field not in _APP_UI_TEXT_FIELDS:
            raise AgentValidationError(
                f"edits[{number}].field must be one of {list(_APP_UI_TEXT_FIELDS)}"
            )
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise AgentValidationError(
                f"edits[{number}] needs a non-empty old string and a new string"
            )
        current = edited.get(field)
        count = current.count(old) if isinstance(current, str) else 0
        if count != 1:
            raise AgentValidationError(
                f"edits[{number}]: the old text occurs {count} times in {field}; "
                "it must occur exactly once (quote more of it)"
            )
        edited[field] = current.replace(old, new, 1)
    _check_component(edited)
    return edited


def _apply_app_ui_entry_operation(
    library: list[Any], selection: Any, operation: str, payload: dict[str, Any],
) -> tuple[list[Any] | None, Any, dict[str, Any]]:
    """Pure: the new (library or None if unchanged, selection, outcome)."""

    if operation == "use_default":
        return None, {"version": 1, "state": "default"}, {"ui_selection": "default"}
    if operation == "put_asset":
        # Bytes are stored by `put_app_ui_asset`, which then applies _SET_ASSET.
        raise AgentValidationError("put_asset is applied through put_app_ui_asset")
    if operation == "add_ui":
        component = _component(payload)
        if _entry_position(library, component["ui_id"]) >= 0:
            raise AgentConflictError(
                f"a UI with ui_id {component['ui_id']!r} is already installed; "
                "use replace_ui or edit_ui"
            )
        return [*library, component], selection, {
            "ui_id": component["ui_id"], "etag": app_ui_etag(component)}
    ui_id = _named_ui_id(payload) if operation != "replace_ui" else _component(payload)["ui_id"]
    position = _entry_position(library, ui_id)
    if position < 0:
        raise _missing_ui(ui_id, library)
    entry = library[position]
    if operation == "activate":
        return None, {"version": 1, "state": "active", "ui_id": ui_id}, {
            "ui_selection": "active", "ui_id": ui_id}
    _check_etag(payload, entry)
    if operation == "remove_ui":
        remaining = library[:position] + library[position + 1:]
        if isinstance(selection, dict) and selection.get("ui_id") == ui_id:
            selection = {"version": 1, "state": "default"}
        return remaining, selection, {"ui_id": ui_id, "removed": True}
    if operation in (_SET_ASSET, "remove_asset"):
        path = _check_asset_path(payload.get("path"))
        assets = dict(entry.get("assets") or {}) if isinstance(entry.get("assets"), dict) else {}
        if operation == _SET_ASSET:
            assets[path] = payload["ref"]
        elif path not in assets:
            raise AgentNotFoundError(
                f"UI {ui_id!r} has no asset {path!r}; it has {sorted(assets)}"
            )
        else:
            del assets[path]
        replacement = {k: v for k, v in entry.items() if k != "assets"}
        if assets:
            replacement["assets"] = assets
        _check_component(replacement)
        updated = list(library)
        updated[position] = replacement
        outcome = {"ui_id": ui_id, "path": path, "etag": app_ui_etag(replacement)}
        if operation == _SET_ASSET:
            outcome.update(payload["ref"])
        else:
            outcome["removed"] = True
        return updated, selection, outcome
    replacement = (_component(payload) if operation == "replace_ui"
                   else _edited_entry(entry, payload))
    # An edit may not BREAK a UI that rendered. It is not refused for a fault
    # the stored entry already had, because then the agent could not edit its
    # way out of one -- `replace_ui` is the way back, and it is checked above.
    if operation == "edit_ui" and not app_ui_renderability(entry):
        _refuse_unrenderable(replacement)
    updated = list(library)
    updated[position] = replacement
    return updated, selection, {"ui_id": ui_id, "etag": app_ui_etag(replacement)}


def change_app_ui_entry(
    base_path: str | Path,
    *,
    owner_user_id: str,
    universe_id: str,
    operation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply ONE targeted change to the caller's UI row, atomically.

    ``operation`` is one of :data:`APP_UI_ENTRY_OPERATIONS`. No revision is
    named: the change is computed against whatever is stored now and written
    under that row's revision, so a concurrent writer is never overwritten --
    a lost race re-reads and re-applies. A change that no longer applies after
    the re-read (the UI was removed, an ``expected_etag`` no longer matches)
    fails loudly rather than landing on content it was not made for.
    """

    owner, uid = _app_ui_scope(owner_user_id, universe_id)
    if operation not in APP_UI_ENTRY_OPERATIONS and operation != _SET_ASSET:
        raise AgentValidationError(
            f"app UI operation {operation!r} is not one of {list(APP_UI_ENTRY_OPERATIONS)}"
        )
    if not isinstance(payload, dict):
        raise AgentValidationError("app UI payload must be a JSON object")
    # The library's bytes are the saver's account storage, on this path as on
    # `save_app_ui`: the change can add at most its payload, so that is charged
    # before anything is written (`StorageRefused` at the quota). An edit that
    # replaces bytes is an over-count the next measurement clears.
    from tinyassets import storage_accounting

    storage_accounting.charge_now(
        base_path,
        account_id=owner if storage_accounting.is_account(base_path, owner) else None,
        store="ui_library",
        nbytes=len(_canonical_json(payload).encode("utf-8")),
    )
    with _agent_connect(base_path) as conn:
        for _attempt in range(_APP_UI_ENTRY_ATTEMPTS):
            row = conn.execute(
                "SELECT * FROM universe_app_ui WHERE owner_user_id = ? AND universe_id = ?",
                (owner, uid),
            ).fetchone()
            current = _app_ui_document(row, uid)
            library, selection, outcome = _apply_app_ui_entry_operation(
                current["ui_library"], current["ui_selection"], operation, payload,
            )
            # The read above is lock-free and the write below is compare-and-set
            # on the revision it read; the held-check sits between them under the
            # write lock, so no sweep can land between the check and the write.
            _begin_write(conn)
            if library is not None:
                _check_assets_held(conn, owner, library)
            library_json = None if library is None else _canonical_json(library)
            selection_json = _canonical_json(selection) if selection is not None else None
            now = time.time()
            if row is None:
                written = conn.execute(
                    """
                    INSERT INTO universe_app_ui (
                        owner_user_id, universe_id, ui_library_json, ui_selection_json,
                        revision, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(owner_user_id, universe_id) DO NOTHING
                    """,
                    (owner, uid, library_json or "[]", selection_json, now),
                ).rowcount
            else:
                written = conn.execute(
                    """
                    UPDATE universe_app_ui
                       SET ui_library_json = COALESCE(?, ui_library_json),
                           ui_selection_json = ?,
                           revision = revision + 1,
                           updated_at = ?
                     WHERE owner_user_id = ? AND universe_id = ? AND revision = ?
                    """,
                    (library_json, selection_json, now, owner, uid, int(row["revision"])),
                ).rowcount
            if written == 1:
                conn.commit()
                return {**outcome, "revision": current["revision"] + 1}
            conn.rollback()
    raise AgentConflictError(
        "app UI kept changing while this change was applied; try it again"
    )


def read_app_ui_asset(
    base_path: str | Path, *, owner_user_id: str, sha256: str,
) -> bytes | None:
    """The bytes of one of the owner's own UI blobs, else None.

    Keyed by the owner: a hash names bytes only within one person's storage, so
    knowing another person's hash reaches nothing.
    """
    from tinyassets.principals import named_principal

    owner = named_principal(owner_user_id)
    if not owner or not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        return None
    with _agent_connect(base_path) as conn:
        row = conn.execute(
            "SELECT content FROM universe_app_ui_asset "
            "WHERE owner_user_id = ? AND sha256 = ?",
            (owner, sha256),
        ).fetchone()
    return None if row is None else bytes(row["content"])


def store_app_ui_asset(
    base_path: str | Path, *, owner_user_id: str, data: bytes, media_type: str,
) -> dict[str, Any]:
    """Store ``data`` in the owner's UI blob storage; ``{sha256, size, media_type}``.

    ``media_type`` is the reference's, returned for the caller's manifest; the
    stored row is bytes only, so storing the same bytes under another type
    changes nothing another reference depends on. Charged to the owner's storage
    before anything is written (``StorageRefused`` at the quota); bytes already
    held are free. Unreferenced blobs past their grace period are swept in the
    same transaction.
    """
    from tinyassets import storage_accounting
    from tinyassets.principals import named_principal

    owner = named_principal(owner_user_id)
    if not owner:
        raise AgentValidationError("an authenticated owner is required")
    if not isinstance(data, (bytes, bytearray)):
        raise AgentValidationError("asset content must be bytes")
    if len(data) > APP_UI_MAX_ASSET_BYTES:
        raise AgentValidationError(
            f"asset is {len(data)} bytes; the bound per file is {APP_UI_MAX_ASSET_BYTES}"
        )
    if media_type not in APP_UI_ASSET_MEDIA_TYPES.values():
        raise AgentValidationError(f"media type {media_type!r} is not one a UI can load")
    data = bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    ref = {"sha256": sha, "size": len(data), "media_type": media_type}
    with _agent_connect(base_path) as conn:
        # Already held: free, and the grace period restarts so the reference
        # that follows cannot lose a race with a sweep.
        if conn.execute(
            "UPDATE universe_app_ui_asset SET created_at = ? "
            "WHERE owner_user_id = ? AND sha256 = ?",
            (time.time(), owner, sha),
        ).rowcount == 1:
            return ref
    account = owner if storage_accounting.is_account(base_path, owner) else None
    reservation = storage_accounting.reserve(
        base_path, account_id=account, scope_id=account or "", store="ui_library",
        nbytes=len(data),
    )
    try:
        with _agent_connect(base_path) as conn:
            conn.execute(
                """
                INSERT INTO universe_app_ui_asset (
                    owner_user_id, sha256, size_bytes, content, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, sha256) DO UPDATE SET
                    created_at = excluded.created_at
                """,
                (owner, sha, len(data), sqlite3.Binary(data), time.time()),
            )
            _sweep_app_ui_assets(conn, owner)
    except BaseException:
        storage_accounting.release(reservation)
        raise
    storage_accounting.commit(reservation)
    return ref


def _sweep_app_ui_assets(conn: sqlite3.Connection, owner: str) -> int:
    """Delete the owner's blobs no row of theirs references, past the grace."""
    referenced: set[str] = set()
    for row in conn.execute(
        "SELECT ui_library_json FROM universe_app_ui WHERE owner_user_id = ?", (owner,),
    ):
        try:
            library = json.loads(str(row["ui_library_json"]))
        except ValueError:
            # An unreadable row may still reference anything: sweep nothing.
            return 0
        if not isinstance(library, list):
            return 0
        referenced.update(str(ref.get("sha256")) for _, _, ref in _referenced_assets(library)
                          if isinstance(ref, dict))
    cutoff = time.time() - _ASSET_GC_GRACE_SECONDS
    stale = [str(row["sha256"]) for row in conn.execute(
        "SELECT sha256 FROM universe_app_ui_asset WHERE owner_user_id = ? AND created_at < ?",
        (owner, cutoff),
    ).fetchall() if str(row["sha256"]) not in referenced]
    for sha in stale:
        conn.execute(
            "DELETE FROM universe_app_ui_asset WHERE owner_user_id = ? AND sha256 = ?",
            (owner, sha),
        )
    return len(stale)


def put_app_ui_asset(
    base_path: str | Path,
    *,
    owner_user_id: str,
    universe_id: str,
    ui_id: str,
    path: str,
    data: bytes,
    expected_etag: str = "",
) -> dict[str, Any]:
    """Store ``data`` and point ``path`` in UI ``ui_id`` at it, as one change.

    The media type comes from the path's extension. The pointer is applied like
    every targeted change: to the row as stored, re-applied on a lost race.
    """
    _check_asset_path(path)
    ref = store_app_ui_asset(
        base_path, owner_user_id=owner_user_id, data=data,
        media_type=app_ui_asset_media_type(path),
    )
    payload: dict[str, Any] = {"ui_id": ui_id, "path": path, "ref": ref}
    if expected_etag:
        payload["expected_etag"] = expected_etag
    return change_app_ui_entry(
        base_path, owner_user_id=owner_user_id, universe_id=universe_id,
        operation=_SET_ASSET, payload=payload,
    )


__all__ = [
    "AGENT_SCHEMA_VERSION",
    "APP_UI_ASSET_MEDIA_TYPES",
    "APP_UI_MAX_ASSET_BYTES",
    "APP_UI_MAX_ASSET_FILES",
    "APP_UI_MAX_COMPONENT_TEXT_BYTES",
    "APP_UI_MAX_UI_ASSET_BYTES",
    "app_ui_asset_media_type",
    "put_app_ui_asset",
    "read_app_ui_asset",
    "store_app_ui_asset",
    "APP_UI_EDITABLE_FIELDS",
    "APP_UI_ENTRY_OPERATIONS",
    "AgentConflictError",
    "AgentNotFoundError",
    "AgentValidationError",
    "MAX_AGENT_JSON_BYTES",
    "MAX_LINEAGE_DEPTH",
    "app_ui_etag",
    "app_ui_index",
    "change_app_ui_entry",
    "create_binding",
    "get_app_ui",
    "get_binding",
    "get_definition",
    "import_definition",
    "list_bindings",
    "list_definitions",
    "publish_definition",
    "save_app_ui",
    "set_binding_provider_ref_in_transaction",
    "set_binding_serving_in_transaction",
    "update_binding",
]

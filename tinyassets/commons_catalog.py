"""The commons catalogue and the binding rows that remain beside agent files.

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
            journal = "DELETE" if Path(base_path).name == ".published" else "WAL"
            conn.execute(f"PRAGMA journal_mode = {journal}")
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
) -> dict[str, Any]:
    from tinyassets.commons_bundles import append, authorize

    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    stored = _enrich_local_lineage(conn, normalized)
    bundle_id = stored.get("bundle_id", "")
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

    verified_lineage = _verified_local_lineage(conn, stored["lineage"])
    package = stored["components"].get("package", {})
    if bundle_id and package.get("bundle_id") == bundle_id:
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

    from tinyassets.owner_runtime import current

    if current() is not None:
        from tinyassets.owner_channel import request

        return request("", {"op": "BOX_PUBLISH", "payload": payload,
                            "idempotency_key": idempotency_key})
    actor, key = _author_and_key(author_id, idempotency_key)
    normalized = _normalize_definition_payload(payload)
    with _agent_connect(base_path) as conn:
        return _publish_normalized(
            conn,
            actor=actor,
            normalized=normalized,
            key=key,
        )


def get_definition(
    base_path: str | Path,
    definition_id: str,
    *,
    include_catalogue: bool = False,
) -> dict[str, Any] | None:
    from tinyassets.owner_runtime import current

    if current() is not None:
        from tinyassets.broker.owner_reads import remote

        return remote("definition", definition_id=definition_id,
                      include_catalogue=include_catalogue)
    return _get_definition(base_path, definition_id, include_catalogue=include_catalogue)


@contextmanager
def _definition_connect(base_path):
    """Published reads never initialize schemas or inspect private store paths."""
    if Path(base_path).name != ".published":
        with _agent_connect(base_path) as conn:
            yield conn
        return
    from tinyassets.storage import DB_FILENAME

    path = Path(base_path) / DB_FILENAME
    if not path.exists():
        yield None
        return
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def _get_definition(base_path, definition_id, *, include_catalogue=False):
    with _definition_connect(base_path) as conn:
        if conn is None:
            return None
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
    from tinyassets.owner_runtime import current

    args = dict(query=query, tags=tags, exclude_tags=exclude_tags,
                author_id=author_id, limit=limit, offset=offset)
    if current() is not None:
        from tinyassets.broker.owner_reads import remote

        return remote("catalog", **args)
    return _list_definitions(base_path, **args)


def _list_definitions(base_path, *, query="", tags=(), exclude_tags=(), author_id="",
                      limit=30, offset=0):
    if type(offset) is not int or offset < 0:
        raise AgentValidationError("offset must be a non-negative integer")
    matched = 0
    bounded_limit = max(1, min(int(limit), 100))
    wanted_query = (query or "").strip().casefold()
    wanted_tags = {str(tag).strip() for tag in tags if str(tag).strip()}
    excluded_tags = {str(tag).strip() for tag in exclude_tags if str(tag).strip()}
    wanted_author = (author_id or "").strip()

    with _definition_connect(base_path) as conn:
        if conn is None:
            return []
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
    if _read_definition_row(conn, definition_id) is not None:
        return
    from tinyassets.owner_runtime import current

    if current() is not None:
        from tinyassets.broker.owner_reads import remote

        definition = remote("definition", definition_id=definition_id, include_catalogue=False)
        if definition is not None:
            # A private binding keeps an immutable public copy for its foreign key.
            # This copy never participates in the published catalog or authorship.
            conn.execute(
                "INSERT OR IGNORE INTO agent_definitions (agent_definition_id, author_id, "
                "name, description, schema_version, tags_json, components_json, "
                "external_origins_json, portable_json, content_fingerprint, "
                "idempotency_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)",
                (definition_id, definition["author_id"], definition["name"],
                 definition["description"], definition["schema_version"],
                 _canonical_json(definition["tags"]), _canonical_json(definition["components"]),
                 _canonical_json(definition["external_origins"]),
                 _canonical_json(definition["portable_definition"]),
                 definition["content_fingerprint"], definition["created_at"]))
            return
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


__all__ = [
    "AGENT_SCHEMA_VERSION",
    "AgentConflictError",
    "AgentNotFoundError",
    "AgentValidationError",
    "MAX_AGENT_JSON_BYTES",
    "MAX_LINEAGE_DEPTH",
    "SINGLETON_BINDING_ROLES",
    "create_binding",
    "get_binding",
    "get_definition",
    "list_bindings",
    "list_definitions",
    "publish_definition",
    "reconnect_binding_candidates_in_transaction",
    "serving_binding_candidates",
    "set_binding_provider_ref_in_transaction",
    "set_binding_serving_in_transaction",
    "update_binding",
]

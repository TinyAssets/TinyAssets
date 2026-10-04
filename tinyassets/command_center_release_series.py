"""Explicit publisher-approved release lineage; never infer a series from names.

Root integration must add capture_release_link's link and consent text to the
platform publication pin BEFORE the owner confirms it, then call record_release
only after its activation. Old publication pins cannot be silently enrolled.
"""

from __future__ import annotations

import json
import uuid

from tinyassets import command_center_update_registry as registry
from tinyassets.api.command_center_updates import _scope
from tinyassets.command_center_updates import digest
from tinyassets.custom_agents import _check_secret_fields, _normalize_definition_payload

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS command_center_release_series (
        series_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, publisher_home TEXT NOT NULL,
        head_release_id TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS command_center_releases (
        release_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, sequence INTEGER NOT NULL,
        definition_id TEXT NOT NULL, record_json TEXT NOT NULL,
        UNIQUE(series_id,sequence), UNIQUE(series_id,definition_id))""",
    """CREATE TABLE IF NOT EXISTS command_center_release_evidence (
        release_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, publisher_home TEXT NOT NULL,
        request_id TEXT NOT NULL, identity_hashes_json TEXT NOT NULL,
        UNIQUE(publisher_home,request_id))""",
)


def ensure_schema(conn):
    for statement in _SCHEMA:
        conn.execute(statement)


def _normalized(definition):
    return _normalize_definition_payload(definition.get("portable_definition", definition))


def _identities(author, action, components):
    """Private identity hashes derived from the validated, pinned source selection.

    These hashes stay in publisher-private records and are omitted from public
    release listings. Public component keys cannot move between source objects.
    """
    sources = {}
    if "ui" in components:
        sources["ui"] = action["ui_id"]
    for index, source in enumerate(action.get("branch_ids", []), 1):
        sources[f"workflow-{index}"] = source
    for index, source in enumerate(action.get("automation_ids", []), 1):
        sources[f"automation-{index}"] = source
    sources.update(action.get("agent_templates", {}))
    if set(sources) != set(components):
        raise ValueError("release component identity mapping is incomplete")
    if any(not isinstance(value, str) or not value for value in sources.values()):
        raise ValueError("release components require explicit stable source identities")
    identities = {
        key: digest([author, components[key]["kind"], source]) for key, source in sources.items()
    }
    if len(set(identities.values())) != len(identities):
        raise ValueError("one source cannot have multiple component identities")
    return identities


def _record(conn, release_id):
    row = conn.execute(
        "SELECT record_json FROM command_center_releases WHERE release_id=?", (release_id,)
    ).fetchone()
    if row is None:
        raise LookupError("release not found")
    record = json.loads(row[0])
    if (
        record.get("release_id") != release_id
        or digest({k: v for k, v in record.items() if k != "release_id"}) != release_id
    ):
        raise ValueError("release record integrity mismatch")
    return record


def _parent(conn, *, series_id, parent_id, author, uid, identities):
    row = conn.execute(
        "SELECT * FROM command_center_release_series WHERE series_id=?", (series_id,)
    ).fetchone()
    if not parent_id:
        if row is not None or conn.execute(
            "SELECT 1 FROM command_center_releases WHERE series_id=?", (series_id,)
        ).fetchone():
            raise ValueError("existing series requires its exact current parent release")
        return None
    if (
        row is None
        or row["owner_id"] != author
        or row["publisher_home"] != uid
        or row["head_release_id"] != parent_id
    ):
        raise ValueError("series ownership or parent head changed")
    parent = _record(conn, parent_id)
    # A key remains reserved even if an intermediate release removed it.
    # Otherwise remove/re-add could silently rebind recipients to another source.
    prior = {}
    for historic in _chain(conn, series_id):
        evidence = conn.execute(
            "SELECT identity_hashes_json FROM command_center_release_evidence "
            "WHERE release_id=? AND owner_id=? AND publisher_home=?",
            (historic["release_id"], author, uid),
        ).fetchone()
        if evidence is None:
            raise ValueError("publisher release evidence is unavailable")
        prior.update(json.loads(evidence[0]))
    reverse = {value: key for key, value in prior.items()}
    for key, identity in identities.items():
        if (key in prior and prior[key] != identity) or (
            identity in reverse and reverse[identity] != key
        ):
            raise ValueError("stable component keys changed; do not reorder or reuse identities")
    return parent


def consent_text(link):
    """Exact text root must append to the platform-owned publication consent."""
    return (
        "Publish a command-center release\n"
        f"Series: {link['series_id']}\n"
        f"Parent release: {link['parent_release_id'] or 'First release'}\n"
        f"Change summary: {link['summary']}\n"
        f"Source declaration: {link['definition_digest']}\n"
        f"Release consent: {digest(link)}"
    )


def capture_release_link(*, universe_id, action, summary, series_id="", parent_release_id=""):
    """Read-only publication hook; builds source snapshot with real owner checks."""
    from tinyassets.api.publish_requests import build_snapshot

    if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
        raise ValueError("a release needs a change summary of 1 to 2000 characters")
    _check_secret_fields({"release_summary": summary})
    base, author, uid = _scope(universe_id)
    if action.get("package") is not None:
        raise ValueError("release series currently supports component systems only")
    snapshot = build_snapshot(uid, action)
    definition = _normalized(snapshot["definition"])
    identities = _identities(author, action, definition["components"])
    series_id = series_id or "ccs_" + uuid.uuid4().hex
    if not isinstance(series_id, str) or len(series_id) > 100:
        raise ValueError("invalid publication series ID")
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=author, uid=uid)
        ensure_schema(conn)
        _parent(
            conn,
            series_id=series_id,
            parent_id=parent_release_id,
            author=author,
            uid=uid,
            identities=identities,
        )
    link = {
        "version": 1,
        "series_id": series_id,
        "author_id": author,
        "publisher_home": uid,
        "parent_release_id": parent_release_id,
        "summary": summary.strip(),
        "definition_digest": digest(definition),
        "identity_hashes": identities,
    }
    return {"release_link": link, "consent_text": consent_text(link)}


def _public(record):
    return {
        k: value
        for k, value in record.items()
        if k not in {"identity_hashes", "publisher_home", "request_id"}
    }


def record_release(*, universe_id, request_id):
    """Post-publication hook using only an actual activated platform consent pin."""
    from tinyassets.api.system_copy_requests import _source
    from tinyassets.command_center_packages import pin_for_request

    base, author, uid = _scope(universe_id)
    pin = pin_for_request(base, universe_id=uid, request_id=request_id)
    if (
        not pin
        or pin["state"] != "activated"
        or pin["kind"] != "publish"
        or not pin["progress"].get("published")
        or pin["progress"].get("publication_kind") != "system"
    ):
        raise ValueError("an activated component-system publication pin is required")
    action = pin["record"]["action"]
    link = action.get("release_link")
    if not isinstance(link, dict) or set(link) != {
        "version",
        "series_id",
        "author_id",
        "publisher_home",
        "parent_release_id",
        "summary",
        "definition_digest",
        "identity_hashes",
    }:
        raise ValueError("publication has no explicit release-lineage consent")
    if (
        link["version"] != 1
        or link["author_id"] != author
        or link["publisher_home"] != uid
        or not isinstance(link["summary"], str)
        or not 1 <= len(link["summary"]) <= 2000
        or consent_text(link) not in pin["record"].get("tab", {}).get("body", "")
        or pin["digest"] != action.get("snapshot_digest")
    ):
        raise ValueError("publication release consent does not match its owner or pinned display")
    _check_secret_fields({"release_summary": link["summary"]})
    definition_id = pin["progress"]["agent_definition_id"]
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=author, uid=uid)
        ensure_schema(conn)
        existing = conn.execute(
            "SELECT release_id FROM command_center_release_evidence "
            "WHERE owner_id=? AND publisher_home=? AND request_id=?",
            (author, uid, request_id),
        ).fetchone()
        if existing:
            return _public(_record(conn, existing[0]))
        definition, _ = _source(definition_id)
        if definition["author_id"] != author:
            raise ValueError("published definition belongs to a different author")
        normalized = _normalized(definition)
        identities = _identities(author, action, normalized["components"])
        if digest(normalized) != link["definition_digest"] or identities != link["identity_hashes"]:
            raise ValueError("published definition or identities differ from release consent")
        parent = _parent(
            conn,
            series_id=link["series_id"],
            parent_id=link["parent_release_id"],
            author=author,
            uid=uid,
            identities=identities,
        )
        record = {
            **_public(link),
            "definition_id": definition_id,
            "definition_fingerprint": definition["content_fingerprint"],
            "sequence": 1 if parent is None else parent["sequence"] + 1,
            "components": {
                key: {"kind": value["kind"], "digest": digest(value)}
                for key, value in normalized["components"].items()
            },
        }
        record["release_id"] = digest(record)
        conn.execute(
            "INSERT INTO command_center_releases VALUES (?,?,?,?,?)",
            (
                record["release_id"],
                link["series_id"],
                record["sequence"],
                definition_id,
                json.dumps(record, sort_keys=True),
            ),
        )
        # Private publication evidence is erasable by principal independently of
        # the publisher's current home. It is never part of the public hash.
        conn.execute(
            "INSERT INTO command_center_release_evidence VALUES (?,?,?,?,?)",
            (record["release_id"], author, uid, request_id, json.dumps(identities, sort_keys=True)),
        )
        if parent is None:
            conn.execute(
                "INSERT INTO command_center_release_series VALUES (?,?,?,?)",
                (link["series_id"], author, uid, record["release_id"]),
            )
        else:
            conn.execute(
                "UPDATE command_center_release_series SET head_release_id=? WHERE series_id=?",
                (record["release_id"], link["series_id"]),
            )
        return _public(record)


def _chain(conn, series_id):
    rows = conn.execute(
        "SELECT release_id FROM command_center_releases WHERE series_id=? ORDER BY sequence",
        (series_id,),
    ).fetchall()
    result, parent = [], ""
    for sequence, row in enumerate(rows, 1):
        record = _record(conn, row[0])
        if (
            record["series_id"] != series_id
            or record["sequence"] != sequence
            or record["parent_release_id"] != parent
            or result
            and record["author_id"] != result[0]["author_id"]
        ):
            raise ValueError("release chain integrity mismatch")
        result.append(record)
        parent = row[0]
    if not result:
        raise LookupError("series not found")
    return result


def list_releases(*, universe_id, series_id):
    """Owner-facing immutable summaries; missing/withdrawn sources are unavailable."""
    from tinyassets.api.system_copy_requests import _source

    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        ensure_schema(conn)
        result = []
        for record in _chain(conn, series_id):
            try:
                _source(record["definition_id"])
                available = True
            except (ValueError, LookupError):
                available = False
            result.append({**_public(record), "available": available})
        return {"series_id": series_id, "releases": result}


def histories_for_definition(*, universe_id, definition_id):
    """Find recorded membership by exact immutable ID, never by matching names."""
    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        ensure_schema(conn)
        ids = [row[0] for row in conn.execute(
            "SELECT DISTINCT series_id FROM command_center_releases WHERE definition_id=? "
            "ORDER BY series_id", (definition_id,))]
    return [list_releases(universe_id=uid, series_id=value) for value in ids]

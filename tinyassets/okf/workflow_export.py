"""Owner-scoped portable workflow data; no execution or source authority travels."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType

from tinyassets.okf.bundle import InvalidBundle

KIND = "tinyassets.workflow.v1"
SCHEDULES = "automations.json"
PREFIX = "workflows/exported/"
MAX_RECORDS = 100
MAX_BYTES = 2 * 1024 * 1024
_SECRETS = {"api_key", "access_token", "refresh_token", "client_secret", "password",
            "authorization", "private_key", "secret", "token"}
_CONNECTIONS = {"connection", "connection_id", "connection_name"}
_AUTHORITY = {"owner_id", "owner_user_id", "user_id", "principal_id", "universe_id",
              "command_center_id", "account_id", "goal_id"}


def encoded(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         indent=2).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidBundle("Workflow data must be portable JSON") from exc
    if len(raw) > MAX_BYTES:
        raise InvalidBundle("Workflow data exceeds its bound")
    return raw


@dataclass(frozen=True)
class Capture:
    files: Mapping[str, bytes]
    digest: str


def capture(base, *, owner, center):
    """Read existing platform definitions, not owner files or execution records."""
    from tinyassets.automations import AutomationStore
    from tinyassets.daemon_server import list_branch_definitions

    branches = list_branch_definitions(base, author=owner, viewer=owner)
    schedules = [asdict(row) for row in AutomationStore(base).list(universe_id=center)
                 if row.owner_principal_id == owner]
    return serialize(branches, schedules, owner=owner, center=center)


def serialize(branches, schedules, *, owner, center):
    try:
        return _serialize(branches, schedules, owner=owner, center=center)
    except InvalidBundle:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, RecursionError) as exc:
        raise InvalidBundle("Invalid source workflow or schedule data") from exc


def _serialize(branches, schedules, *, owner, center):
    if not owner or not center or len(branches) > MAX_RECORDS or len(schedules) > MAX_RECORDS:
        raise InvalidBundle("Workflow capture needs a bounded named owner scope")
    if any(row.get("author") != owner for row in branches) or any(
        row.get("owner_principal_id") != owner or row.get("universe_id") != center
        for row in schedules
    ):
        raise InvalidBundle("Workflow capture contains another owner's source")
    branches = sorted(branches, key=lambda row: row["branch_def_id"])
    schedules = sorted(schedules, key=lambda row: row["automation_id"])
    keys = {row["branch_def_id"]: f"workflow-{n}" for n, row in enumerate(branches, 1)}
    if len(keys) != len(branches) or not all(keys):
        raise InvalidBundle("Workflow source identities must be unique")
    if len({row["automation_id"] for row in schedules}) != len(schedules):
        raise InvalidBundle("Schedule source identities must be unique")
    # Server-side only; source secrets and source IDs never appear in output.
    digest = hashlib.sha256(encoded([branches, schedules])).hexdigest()
    connections, bindings = {}, {}

    def portable(value, field="", *, depth=0):
        if depth > 32:
            raise InvalidBundle("Workflow configuration is nested too deeply")
        if field.casefold() in _SECRETS and value not in (None, ""):
            key = f"binding-{len(bindings) + 1}"
            bindings[key] = {"environment": f"BINDING_{len(bindings) + 1}"}
            return {"local_binding": key}
        if field in _CONNECTIONS and value not in (None, ""):
            if not isinstance(value, str):
                raise InvalidBundle("Connection references must be text")
            return connections.setdefault(value, f"connection-{len(connections) + 1}")
        if field in _AUTHORITY and value not in (None, ""):
            return {"local_binding": "recipient-" + field.replace("_", "-")}
        if field == "branch_def_id" and value:
            if value not in keys:
                raise InvalidBundle("A workflow dependency is not owned or included")
            return keys[value]
        if field == "branch_version_id" and value:
            # A live definition is not a frozen version. Never quietly substitute it.
            raise InvalidBundle("Frozen child workflow versions need an explicit portable snapshot")
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise InvalidBundle("Workflow field names must be strings")
            return {key: portable(child, key, depth=depth + 1) for key, child in value.items()}
        if isinstance(value, list):
            return [portable(child, depth=depth + 1) for child in value]
        return value

    files = {}
    for row in branches:
        key = keys[row["branch_def_id"]]
        # Preserve node/configuration fields verbatim in structure; do not run
        # BranchDefinition normalization, which drops unknown authored fields.
        graph = row.get("graph", {})
        definition = {field: row[field] for field in (
            "name", "description", "domain_id", "tags", "skills", "node_defs",
            "state_schema", "concurrency_budget",
        ) if field in row}
        definition["graph"] = graph
        definition["entry_point"] = row.get("entry_point") or graph.get("entry_point", "")
        # Source model routing is not a transferable connection or credential.
        definition["model_reference"] = "local-model"
        definition = portable(definition)
        nodes = definition.get("node_defs", [])
        refs = definition["graph"].get("nodes", [])
        node_ids = {node["node_id"]: f"node-{n}" for n, node in enumerate(nodes, 1)}
        step_ids = {ref["id"]: f"step-{n}" for n, ref in enumerate(refs, 1)}
        if len(node_ids) != len(nodes) or len(step_ids) != len(refs):
            raise InvalidBundle("Workflow graph identities must be unique")
        def step(value):
            if value in ("__start__", "__end__", "END", "START"):
                return value
            if value not in step_ids:
                raise InvalidBundle("Workflow graph reference is unresolved")
            return step_ids[value]
        for ref in refs:
            original_node = ref.get("node_def_id") or ref["id"]
            ref["id"] = step(ref["id"])
            if original_node not in node_ids:
                raise InvalidBundle("Workflow node definition is unresolved")
            ref["node_def_id"] = node_ids[original_node]
        for node in nodes:
            node["node_id"] = node_ids[node["node_id"]]
            if node.get("workspace"):
                node["workspace"] = step(node["workspace"])
            if node.get("dependencies"):
                node["dependencies"] = [step(value) for value in node["dependencies"]]
            if "model_hint" in node:
                node["model_hint"] = "local-model"
            if "llm_policy" in node:
                node["llm_policy"] = None
        definition["entry_point"] = step(definition["entry_point"])
        definition["graph"]["entry_point"] = definition["entry_point"]
        for edge in definition["graph"].get("edges", []):
            edge["from"], edge["to"] = step(edge["from"]), step(edge["to"])
        for edge in definition["graph"].get("conditional_edges", []):
            edge["from"] = step(edge["from"])
            edge["conditions"] = {
                label: step(target) for label, target in edge["conditions"].items()
            }
        files[PREFIX + key + ".json"] = encoded({
            "kind": KIND, "id": key, "definition": definition,
        })
    rows = []
    for n, source in enumerate(schedules, 1):
        if source.get("retired_at"):
            raise InvalidBundle("Retired schedules cannot be captured")
        workflow = keys.get(source.get("branch_def_id"))
        if workflow is None:
            raise InvalidBundle("Schedule workflow is not owned or included")
        kind = source["trigger_kind"]
        if kind == "interval":
            trigger = {"kind": kind, "seconds": source["interval_seconds"],
                       "start_at": source["created_at"]}
        elif kind == "once":
            trigger = {"kind": kind, "at": source["not_before"]}
        elif kind == "cron":
            trigger = {"kind": kind, "expression": source["cron_expr"],
                       "timezone": source.get("timezone") or "UTC"}
        elif kind == "event":
            trigger = {"kind": kind, "event_type": source["event_type"],
                       "event_filter": portable(source.get("event_filter") or {})}
        else:
            raise InvalidBundle("Unsupported source schedule trigger")
        rows.append({"id": f"schedule-{n}", "name": source["name"], "agent": "main",
                     "workflow": PREFIX + workflow + ".json",
                     "inputs": portable(source.get("inputs") or {}), "enabled": False,
                     "trigger": trigger, "overlap": source.get("overlap", "queue")})
    if rows:
        files[SCHEDULES] = encoded({"schema_version": 1, "automations": rows})
    # Bindings are descriptive references only. No generated variable holds a key.
    if connections or bindings:
        files[PREFIX + "bindings.json"] = encoded({
            "connections": sorted(connections.values()), "bindings": bindings,
        })
    if sum(map(len, files.values())) > 8 * MAX_BYTES:
        raise InvalidBundle("Structured export exceeds its bound")
    return Capture(MappingProxyType(files), digest)


def descriptors(files):
    """Derive manifest references from the selected bytes, including dependency closure."""
    workflows, automations = [], []
    for path, data in sorted(files.items()):
        if not path.startswith(PREFIX) or path == PREFIX + "bindings.json":
            continue
        try:
            document = json.loads(data)
        except (ValueError, UnicodeError):
            raise InvalidBundle("Invalid portable workflow document") from None
        if (not isinstance(document, dict) or set(document) != {"kind", "id", "definition"}
                or document["kind"] != KIND or path != PREFIX + document["id"] + ".json"
                or not isinstance(document["definition"], dict)):
            raise InvalidBundle("Invalid portable workflow identity")
        workflows.append({"id": document["id"], "path": path})
    if SCHEDULES in files:
        try:
            document = json.loads(files[SCHEDULES])
            automations = document["automations"]
            if document["schema_version"] != 1 or not isinstance(automations, list):
                raise ValueError
            for row in automations:
                if row.get("enabled") is not False or row.get("workflow") not in {
                    workflow["path"] for workflow in workflows
                }:
                    raise ValueError
        except (KeyError, TypeError, AttributeError, ValueError, UnicodeError):
            raise InvalidBundle(
                "Schedules must be disabled and include their selected workflows"
            ) from None
        automations = [{"id": row["id"], "workflow": row["workflow"], "path": SCHEDULES,
                        "enabled": False} for row in automations]
    def references(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "branch_def_id" and child and child not in {
                    workflow["id"] for workflow in workflows
                }:
                    raise InvalidBundle("Select every referenced child workflow")
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)
    for workflow in workflows:
        references(json.loads(files[workflow["path"]]))
    return workflows, automations

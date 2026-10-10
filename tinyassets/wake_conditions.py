"""Wake predicates read authoritative state; commands execute only in owner boxes."""
from __future__ import annotations

import asyncio
import re


def _run(home, owner, run_id):
    from tinyassets.runs import _connect, initialize_runs_db

    initialize_runs_db(home.parent)
    with _connect(home.parent) as conn:
        row = conn.execute("SELECT cause_principal,queue_universe_id,status FROM runs "
                           "WHERE run_id=?", (run_id,)).fetchone()
    if not row or (row[0], row[1]) != (owner, home.name):
        raise PermissionError("wake run unavailable")
    return row[2]


def _request(home, owner, agent, request_id):
    from tinyassets.storage.pending_requests import get_request

    row = get_request(home, request_id)
    origin = row.get("asking_context", {}) if row else {}
    if (origin.get("owner"), origin.get("home"), origin.get("agent")) != (
            owner, home.name, agent):
        raise PermissionError("wake request unavailable")
    return row["status"]


def validate(home, owner, agent, condition):
    if not isinstance(condition, dict):
        raise ValueError("condition must be an object")
    if not condition:
        return {}
    fields = {
        "run_finished": {"kind", "run_id"}, "run_failed": {"kind", "run_id"},
        "request_answered": {"kind", "request_id"},
        "connection": {"kind", "destination"},
        "release": {"kind", "commit", "pr"},
        "probe": {"kind", "command", "timeout_seconds"},
    }
    kind = condition.get("kind")
    if not isinstance(kind, str) or kind not in fields or set(condition) - fields[kind]:
        raise ValueError("unsupported wake condition or fields")
    for key in ("run_id", "request_id"):
        if key in fields[kind] and (
                not isinstance(condition.get(key), str) or not condition[key]):
            raise ValueError(f"{kind} requires {key}")
    if kind in {"run_finished", "run_failed"}:
        _run(home, owner, condition.get("run_id", ""))
    elif kind == "request_answered":
        _request(home, owner, agent, condition.get("request_id", ""))
    elif kind == "connection":
        # Match only the owner's granted catalogue, including connections they
        # have yet to add. A foreign connection ID is never an input or lookup.
        if not isinstance(condition.get("destination"), str) or not condition["destination"]:
            raise ValueError("connection requires an exact destination")
    elif kind == "release":
        if ("commit" in condition) == ("pr" in condition):
            raise ValueError("release requires exactly one full commit SHA or PR number")
        if "commit" in condition and not re.fullmatch(r"[0-9a-f]{40}", str(condition["commit"])):
            raise ValueError("release commit must be a full lowercase SHA")
        if "pr" in condition and (type(condition["pr"]) is not int or condition["pr"] < 1):
            raise ValueError("release PR must be a positive integer")
    elif kind == "probe":
        if not isinstance(condition.get("command"), str) or not condition["command"].strip():
            raise ValueError("probe requires a command")
        from tinyassets.agent_wakes import _positive

        _positive(condition.get("timeout_seconds", 60), "timeout_seconds")
        if condition.get("timeout_seconds", 60) > 600:
            raise ValueError("probe timeout exceeds the box command limit")
    return dict(condition)


def matches(home, row, condition, *, probe=None):
    validate(home, row["owner"], row["agent"], condition)
    kind = condition.get("kind")
    if kind is None:
        return True
    if kind in {"run_finished", "run_failed"}:
        status = _run(home, row["owner"], condition["run_id"])
        return status == "failed" if kind == "run_failed" else status in {"completed", "failed"}
    if kind == "request_answered":
        return _request(home, row["owner"], row["agent"], condition["request_id"]) == "answered"
    if kind == "connection":
        from tinyassets.broker.catalog import connections

        return any(view and view.owner_user_id == row["owner"] and view.revoked_at is None
                   and view.destination == condition["destination"]
                   for _, view, _ in connections(home.parent, principal=row["owner"],
                                                command_center=home.name))
    if kind == "release":
        from tinyassets.api.status import _load_release_state

        release = _load_release_state()
        # The manifest is scoped to the exact running revision in the receipt.
        manifest = release.get("extra", {}).get("containment", {})
        if not release.get("receipt_available"):
            return False
        if "commit" in condition and release.get("git_sha") == condition["commit"]:
            return True
        if manifest.get("git_sha") != release.get("git_sha"):
            return False
        return (condition["commit"] in manifest.get("commits", []) if "commit" in condition
                else condition["pr"] in manifest.get("prs", []))
    return (probe or run_probe)(home, row, condition)


def run_probe(home, row, condition):
    from tinyassets.agent_loop.box_tools import BOX_ROOT, BoxExecutor
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.api.permissions import owner_run_identity

    provider, limits = configured_box_provider()
    if provider is None:
        raise LookupError("wake probe needs the owner's box provider")
    attempt = f"wake:{row['wake_id']}:probe:{row['fires']}:{row['checks']}"
    with owner_run_identity(home.parent, home.name, row["owner"]) as authorized:
        if not authorized:
            raise PermissionError("wake probe owner unavailable")
        handle = provider.bind(home.name, account_id=row["owner"], turn_id=attempt)
        executor = BoxExecutor(provider, handle, limits=limits,
                               cwd=getattr(handle, "root", None) or BOX_ROOT)
        outcome = asyncio.run(executor.run(
            attempt, ["/bin/bash", "-c", condition["command"]],
            wall_seconds=condition.get("timeout_seconds", 60)))
    return outcome.exit_code == 0 and not outcome.killed

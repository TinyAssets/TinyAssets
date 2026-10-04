"""The one contract for activities, used by the agent's served tools and the
owner's app (harness D2; design D8).

The universe and the owner always come from the caller's verified pin -- the
engine surface's bound actor and graph, or the owner door's session -- never
from a payload. Every read is complete: a page carries a cursor to the next.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tinyassets import agent_activities as activities

logger = logging.getLogger(__name__)

OPERATIONS = ("start", "stop", "pause", "resume")


def _universe_dir(base_path: Path, universe_id: str) -> Path:
    if not universe_id or Path(universe_id).name != universe_id or universe_id.startswith("."):
        raise activities.ActivityRefused("That universe is not one you can reach.",
                                         kind="not_found")
    return Path(base_path) / universe_id


def _wake(base_path: Path, universe_id: str) -> None:
    """Dispatch now rather than at the next tick; a failure waits for the tick."""
    from tinyassets.activity_dispatcher import dispatch_universe

    try:
        dispatch_universe(base_path, universe_id)
    except Exception:  # noqa: BLE001 - the consumer tick retries
        logger.exception("activity wake failed universe=%s", universe_id)


def _public(record: dict) -> dict:
    """What the agent and the owner see; the runner's bookkeeping stays inside."""
    hidden = {"runner_token", "runner_generation", "owner_principal", "brief",
              "retiring_token", "claimed_at"}
    view = {k: v for k, v in record.items() if k not in hidden}
    view["brief"] = record["brief"][:2_000]
    return view


def write(base_path: Path, *, universe_id: str, actor_id: str, operation: str,
          payload: dict[str, Any], inside_activity: bool = False) -> dict:
    """Start, stop, pause or resume an activity in ``universe_id`` as ``actor_id``."""
    op = (operation or "").strip().lower()
    if op not in OPERATIONS:
        return {"error": "unknown_activity_operation", "allowed_operations": list(OPERATIONS)}
    universe_dir = _universe_dir(base_path, universe_id)
    try:
        if op == "start":
            if inside_activity:
                raise activities.ActivityRefused(
                    "An activity cannot start another activity yet; finish this one and "
                    "say what should come next.", kind="nested_activity_unavailable")
            record = activities.create(
                universe_dir, owner_principal=actor_id,
                title=str(payload.get("title") or ""), brief=str(payload.get("brief") or ""),
                origin_kind="ask")
            _wake(Path(base_path), universe_id)
            record = activities.get(universe_dir, record["activity_id"]) or record
            return {"activity_id": record["activity_id"], "status": record["status"],
                    "hint": "It runs in the background; read_graph target=activities shows "
                            "it, and its status reaches you as it changes."}
        activity_id = str(payload.get("activity_id") or "").strip()
        record = activities.get(universe_dir, activity_id)
        if record is None:
            raise activities.ActivityRefused("No such activity.", kind="not_found")
        expect = payload.get("expected_revision")
        expect = int(expect) if isinstance(expect, int) and expect > 0 else None
        if op == "stop":
            record = activities.transition(universe_dir, activity_id, activities.COMPLETED,
                                           expect_revision=expect, outcome="stopped",
                                           event="stopped")
        elif op == "pause":
            record = activities.transition(universe_dir, activity_id, activities.PAUSED,
                                           expect_revision=expect)
        else:
            record = activities.transition(universe_dir, activity_id, activities.SCHEDULED,
                                           expect_revision=expect)
            _wake(Path(base_path), universe_id)
        # The run that was executing is read off the same transaction that
        # retired it, so the cancel targets exactly that run.
        if record.get("retiring_token") and op in {"stop", "pause"}:
            from tinyassets import activity_runner

            activity_runner.stop(Path(base_path), record["retiring_token"])
        return {"activity_id": activity_id, "status": record["status"],
                "revision": record["revision"]}
    except activities.ActivityRefused as exc:
        return {"error": exc.kind, "message": str(exc)}


def read(base_path: Path, *, universe_id: str, activity_id: str = "", status: str = "",
         cursor: str = "", after: int = 0) -> dict:
    """Every activity (paged), or one activity with one page of its status lines."""
    universe_dir = _universe_dir(base_path, universe_id)
    try:
        if activity_id:
            record = activities.get(universe_dir, activity_id)
            if record is None:
                raise activities.ActivityRefused("No such activity.", kind="not_found")
            page = activities.events_page(universe_dir, activity_id, after=after)
            return {"activity": _public(record), "events": page["events"],
                    "next_after": page["next_after"]}
        page = activities.list_page(universe_dir, status=status or None,
                                    cursor=cursor or None)
        return {"activities": [_public(r) for r in page["activities"]],
                "next_cursor": page["next_cursor"]}
    except activities.ActivityRefused as exc:
        return {"error": exc.kind, "message": str(exc)}

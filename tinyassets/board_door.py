"""S5b: the task board's doors (W8, registry row S34), all on the trusted side.

The board is ``collection='board'`` in the owner's broker-held conversation store, the
one revision primitive (``broker/conversations.py``); a task is one row, kept verbatim.
The router answers ``read_graph target=board|board_task`` and ``write_graph
target=board_task`` beside the mailbox doors (``mailbox_door.mcp`` runs the identity and
kill-switch checks), ``/api/board*`` for API-key agents and ``/app/api/board`` for the
owner, from its verified identity; none reaches, asks or wakes a box.

The door owns the board's rules:

* The actor is the verified identity: ``owner``, or the one enabled agent its
  ``outside_origin`` names. A patch names no actor and no history; the door writes both.
* A write names the revision it read. Another revision, or a compare-and-swap lost on
  the row's etag, is a conflict carrying the current task.
* A claim holds a lease sized to the claimant's latency class. Nothing sweeps (ADR-026):
  a read shows a lapsed claim as ``open``, and the next write persists exactly that, with
  its ``lease_expired`` history entry.
* Claims and completions leave ledger receipts; assigning mails each new assignee a
  doorbell in its dm room. The board never grants spend.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from starlette.responses import JSONResponse

from tinyassets.mailbox_door import (
    _HEADERS,
    Refused,
    _agent,
    _answer,
    _count,
    _ledger,
    _owner,
    _payload,
    _query_int,
    post,
)

#: The tool targets the router answers; the box answers ``served_on_trusted_side``.
READS = frozenset({"board", "board_task"})
WRITES = frozenset({"board_task"})
COLLECTION = "board"
OWNER = "owner"  # the ledger's name for the owner
LAPSE = "board"  # the actor of a ``lease_expired`` entry: the board itself
EVERYONE = "everyone"
STATES = ("open", "claimed", "in_progress", "done", "blocked")
#: The states a claim holds, and its lease runs in.
HELD = frozenset({"claimed", "in_progress", "blocked"})
#: Leases by latency class: a ``pull`` agent checks in, an ``mcp_event`` agent is woken.
CHECKIN_LEASE_S = 4 * 3600
LIVE_LEASE_S = 15 * 60
LEASES = {"pull": CHECKIN_LEASE_S, "mcp_event": LIVE_LEASE_S}
FIELDS = frozenset({"title", "body", "assignees", "depends_on", "state", "result_refs"})
MAX_TITLE = 200
MAX_TEXT = 8000
MAX_IDS = 16
MAX_REF = 512
MAX_HISTORY = 50
PAGE = 100  # the store's own pull bound
DEPS_OPEN = "every depends_on task must be done first"
DOORBELL = ("Board task {task_id} is assigned to you. Read it with read_graph "
            "target=board_task query={task_id}, or GET /api/board/task?task_id={task_id}.")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_LOG = logging.getLogger(__name__)


class Conflict(Refused):
    """A stale revision, a lost compare-and-swap or a claim on a task that is not open."""

    def __init__(self, task: dict | None, reason: str):
        super().__init__(409, "conflict", reason)
        self.task = task

    def body(self) -> dict:
        return {**super().body(), "task": self.task}


def _store(identity, action: str, **fields) -> dict:
    """The verified identity's own board; a lost compare-and-swap raises CollectionConflict."""
    from tinyassets.broker.client import BrokerRefused, collection_request
    from tinyassets.storage import data_dir

    try:
        return collection_request(data_dir(), identity, action, owner=identity.user_id,
                                  collection=COLLECTION, **fields)
    except BrokerRefused as exc:
        if str(exc) == "ledger unavailable":
            raise Refused(503, "ledger_unavailable") from None
        raise Refused(403, "refused", str(exc)) from None
    except (OSError, RuntimeError):
        raise Refused(503, "ledger_unavailable") from None


def _row(identity, task_id: str) -> dict | None:
    """One task's row by its key, or None."""
    return _store(identity, "get", row_id=task_id)["row"]


def _agents(identity) -> list[dict]:
    return [agent for agent in _ledger(identity, "OUTSIDE_AGENTS", "list")["agents"]
            if agent["disabled_reason"] is None]


def _actor(identity) -> tuple[str, int]:
    """``(actor, lease seconds)`` of the verified identity; an outside one is one enabled agent."""
    origin = identity.metadata.get("outside_origin")
    if origin is None:
        return OWNER, CHECKIN_LEASE_S
    mine = [agent for agent in _agents(identity) if (
        agent["outside_client"], agent["outside_family"]) == (origin["client"], origin["family"])]
    if len(mine) != 1:  # as the ledger's own reader check
        raise Refused(403, "refused", "this outside identity is not one enabled agent")
    return mine[0]["agent_id"], LEASES.get(mine[0]["delivery_method"], CHECKIN_LEASE_S)


def _task_id(value) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise Refused(400, "invalid_request", "task_id is 1 to 64 letters, digits or _.:-")
    return value


def _ids(value, name: str) -> list[str]:
    if (not isinstance(value, list) or len(value) > MAX_IDS or len(set(map(str, value))) != len(
            value) or not all(isinstance(item, str) and _ID.fullmatch(item) for item in value)):
        raise Refused(400, "invalid_request", f"{name} is at most {MAX_IDS} distinct ids")
    return value


def _lapsed(task: dict, now: float) -> dict:
    """A held claim past its lease is open again; the entry is dated when the lease ran out."""
    claim = task["claim"]
    if task["state"] not in HELD or claim is None or claim["expires_at"] > now:
        return task
    entry = {"at": claim["expires_at"], "actor": LAPSE, "op": "lease_expired",
             "from_state": task["state"], "to_state": "open"}
    return {**task, "state": "open", "claim": None,
            "history": [*task["history"], entry][-MAX_HISTORY:]}


def _view(row: dict, now: float, outside: bool, *, whole: bool = True) -> dict:
    """A task as a reader sees it. Another party's text is enveloped, as mailbox bodies are:
    every text an outside agent reads, and every agent's text the owner reads."""
    from tinyassets.untrusted import envelope

    task = _lapsed(json.loads(row["body"]), now)

    def text(field):
        by = task["authors"].get(field, OWNER)
        value = task[field]
        return envelope(f"board:{by}", value) if outside or by != OWNER else value

    view = {"task_id": task["task_id"], "revision": row["revision"], "seq": row["seq"],
            "state": task["state"], "claim": task["claim"], "assignees": task["assignees"],
            "depends_on": task["depends_on"], "title": text("title")}
    if whole:
        view.update(body=text("body"), result_refs=text("result_refs"),
                    history=task["history"])
    return view


def _outside_reader(identity) -> bool:
    outside = identity.metadata.get("outside_origin") is not None
    if outside:
        _actor(identity)  # a disabled or unregistered agent reads nothing
    return outside


def board(identity, *, since=0, limit=30) -> dict:
    """Task summaries changed after ``since``, whole tasks under the read_graph ceiling."""
    from tinyassets.engine_read_views import CEILING_HEADROOM_BYTES
    from tinyassets.engine_result_bounds import resolve_ceiling

    _count(since, "since", 0)
    _count(limit, "limit", 1, PAGE)
    outside = _outside_reader(identity)
    page = _store(identity, "pull", after=since, limit=limit)
    budget = resolve_ceiling(dict(os.environ)) - CEILING_HEADROOM_BYTES
    tasks, used, now = [], 0, time.time()
    for row in page["rows"]:
        summary = _view(row, now, outside, whole=False)
        used += len(json.dumps(summary, separators=(",", ":"), ensure_ascii=False).encode())
        if tasks and used > budget:
            break
        tasks.append(summary)
    cut = len(tasks) < len(page["rows"])
    # Paging fields first, so a text-only client's clipped copy still has them.
    return {"next_cursor": tasks[-1]["seq"] if cut else page["cursor"],
            "more": cut or len(page["rows"]) == limit, "tasks": tasks}


def task(identity, *, task_id) -> dict:
    _task_id(task_id)
    outside = _outside_reader(identity)
    row = _row(identity, task_id)
    if row is None:
        raise Refused(404, "not_found", "no board task has this task_id")
    return {"task": _view(row, time.time(), outside)}


def _move(task: dict, state: str, *, actor: str, lease: int, now: float, lookup) -> str:
    """One state change, its rules and its history op. ``claimed`` comes only from ``open``."""
    before = task["state"]
    if state == "claimed":
        named = task["assignees"]
        # "everyone" or no assignee: any enabled agent may claim; one claimant holds it.
        if actor != OWNER and named and EVERYONE not in named and actor not in named:
            raise Refused(403, "refused", "this task is assigned to other agents")
        for dep in map(lookup, task["depends_on"]):
            if dep is None or json.loads(dep["body"])["state"] != "done":
                raise Refused(409, "refused", DEPS_OPEN)
        task["claim"] = {"agent_id": actor, "expires_at": now + lease}
        return "claim"
    if before == "open":
        raise Refused(409, "refused", "claim the task first")
    if before == "done":
        if state != "open" or actor != OWNER:
            raise Refused(403, "refused", "only the owner reopens a done task")
        return "reopen"
    if state in ("open", "done"):
        task["claim"] = None
        return "release" if state == "open" else "complete"
    return state  # in_progress or blocked


def _change(task: dict, patch: dict, *, actor: str, lease: int, now: float, lookup,
            created: bool, agents) -> tuple[str, list[str]]:
    """Apply ``patch`` for ``actor``: the history op and the agents newly assigned."""
    holder = task["claim"]["agent_id"] if task["claim"] else None
    state = patch.get("state", task["state"])
    if state not in STATES:
        raise Refused(400, "invalid_request", "state is " + ", ".join(STATES))
    if created and (state != "open" or "title" not in patch):
        raise Refused(400, "invalid_request", "a new task is open and has a title")
    if not created and actor not in (OWNER, holder) and patch != {"state": "claimed"}:
        # Another agent may only claim (an open task, checked by the caller).
        raise Refused(403, "refused", "only the claimant or the owner changes this task")
    if not (created or patch or actor == holder):
        raise Refused(400, "invalid_request", "patch changes nothing")
    newly = []
    if "assignees" in patch:
        if actor != OWNER:  # deviation from W8: assigning is the owner's
            raise Refused(403, "refused", "only the owner assigns a task")
        named, enabled = _ids(patch["assignees"], "assignees"), {
            agent["agent_id"] for agent in agents()}
        if not set(named) <= enabled | {EVERYONE}:
            raise Refused(400, "invalid_request", "assignees are enabled agent ids or everyone")
        fresh = [item for item in named if item not in task["assignees"]]
        newly = sorted(enabled) if EVERYONE in fresh else fresh
        task["assignees"] = named
    if "depends_on" in patch:
        if task["task_id"] in _ids(patch["depends_on"], "depends_on"):
            raise Refused(400, "invalid_request", "a task does not depend on itself")
        task["depends_on"] = patch["depends_on"]
    for field, limit in (("title", MAX_TITLE), ("body", MAX_TEXT)):
        if field in patch:
            value = patch[field]
            if not isinstance(value, str) or len(value) > limit or field == "title" and (
                    not value.strip() or not value.isprintable()):
                raise Refused(400, "invalid_request",
                              f"{field} is text of at most {limit} characters")
            task[field], task["authors"] = value, {**task["authors"], field: actor}
    if "result_refs" in patch:
        refs = patch["result_refs"]
        if not isinstance(refs, list) or len(refs) > MAX_IDS or not all(
                isinstance(ref, str) and 0 < len(ref) <= MAX_REF and ref.isprintable()
                for ref in refs):
            raise Refused(400, "invalid_request",
                          f"result_refs is at most {MAX_IDS} refs of {MAX_REF} characters")
        task["result_refs"], task["authors"] = refs, {**task["authors"], "result_refs": actor}
    op = ("create" if created else "renew" if not patch else
          "assign" if "assignees" in patch else "edit")
    if state != task["state"]:
        op = _move(task, state, actor=actor, lease=lease, now=now, lookup=lookup)
        task["state"] = state
    if task["state"] in HELD and task["claim"]["agent_id"] == actor:
        task["claim"] = {"agent_id": actor, "expires_at": now + lease}  # a claimant's write renews
    return op, newly


def write(identity, *, task_id, expected_revision, patch) -> dict:
    """Create (``expected_revision`` 0) or change one task at the revision its writer read."""
    from tinyassets.broker.client import CollectionConflict

    _task_id(task_id)
    _count(expected_revision, "expected_revision", 0)
    if not isinstance(patch, dict) or not set(patch) <= FIELDS:
        # An actor, history, claim or revision is the door's to write, never a patch's.
        raise Refused(400, "invalid_request", "patch takes " + ", ".join(sorted(FIELDS)))
    actor, lease = _actor(identity)
    outside = identity.metadata.get("outside_origin") is not None
    row, now = _row(identity, task_id), time.time()
    if (row["revision"] if row else 0) != expected_revision:
        raise Conflict(row and _view(row, now, outside),
                       "expected_revision is not the task's revision")
    task = _lapsed(json.loads(row["body"]), now) if row else {
        "task_id": task_id, "title": "", "body": "", "assignees": [], "depends_on": [],
        "state": "open", "claim": None, "result_refs": [], "authors": {}, "history": []}
    if patch.get("state") == "claimed" and task["state"] != "open":
        raise Conflict(_view(row, now, outside), "the task is not open")
    before = task["state"]
    op, newly = _change(task, patch, actor=actor, lease=lease, now=now,
                        lookup=lambda dep: _row(identity, dep), created=row is None,
                        agents=lambda: _agents(identity))
    task["history"] = [*task["history"], {"at": now, "actor": actor, "op": op,
                                          "from_state": before, "to_state": task["state"]}
                       ][-MAX_HISTORY:]
    try:
        saved = _store(identity, "write", row_id=task_id, expected_etag=row["etag"] if row
                       else "", private=False, body=json.dumps(
                           task, sort_keys=True, separators=(",", ":"), ensure_ascii=False))["row"]
    except CollectionConflict as exc:  # another writer committed after this one read
        raise Conflict(exc.current and _view(exc.current, now, outside),
                       "the task changed while this write was made") from None
    result = {"task": _view(saved, now, outside)}
    if op in ("claim", "complete"):
        try:
            _ledger(identity, "MAILBOX_RECEIPT", "board", kind=op, task_id=task_id,
                    revision=saved["revision"])
        except Refused:  # the task change is committed; say the receipt is not
            _LOG.exception("ALARM: board %s receipt unrecorded", op)
            result["receipt"] = "unrecorded"
    for agent_id in newly:
        try:
            post(identity, room_id="dm:" + agent_id, body=DOORBELL.format(task_id=task_id),
                 client_message_id=f"board:{task_id}:{saved['revision']}:{agent_id}")
            result.setdefault("notified", []).append(agent_id)
        except Refused:
            _LOG.exception("board doorbell to %s not posted", agent_id)
            result.setdefault("unnotified", []).append(agent_id)
    return result


def _write_body(identity, data) -> dict:
    if not isinstance(data, dict) or set(data) != {"task_id", "expected_revision", "patch"}:
        raise Refused(400, "invalid_request", "the body is {task_id, expected_revision, patch}")
    return write(identity, **data)


def tool(identity, name: str, target: str, arguments: dict) -> dict:
    """``mailbox_door.mcp``'s answer for the board targets."""
    if name == "write_graph":
        payload = _payload(arguments)
        if not set(payload) <= {"task_id", "patch"}:
            raise Refused(400, "invalid_request", "payload_json takes task_id and patch")
        return write(identity, task_id=payload.get("task_id"), patch=payload.get("patch"),
                     expected_revision=arguments.get("expected_revision", 0))
    if target == "board_task":
        return task(identity, task_id=arguments.get("query"))
    return board(identity, since=arguments.get("cursor", 0), limit=arguments.get("limit", 30))


async def mcp(front) -> None:
    """``read_graph target=board|board_task``, ``write_graph target=board_task``."""
    from tinyassets.mailbox_door import mcp as answer

    await answer(front, tool)


# -- /api: API-key agents --------------------------------------------------------


async def rest_board(request):
    identity, refused = await _agent(request)
    return refused or await _answer(board, identity, since=_query_int(request, "since", 0),
                                    limit=_query_int(request, "limit", 30))


async def rest_task(request):
    """``GET ?task_id=`` reads one task; ``POST {task_id, expected_revision, patch}`` writes."""
    from tinyassets.rest_api import _body

    identity, refused = await _agent(request)
    if refused:
        return refused
    if request.method == "GET":
        return await _answer(task, identity, task_id=request.query_params.get("task_id"))
    data = await _body(request)
    if data is None:
        return JSONResponse({"error": "invalid_json"}, status_code=400, headers=_HEADERS)
    return await _answer(_write_body, identity, data)


def rest_routes() -> list:
    from starlette.routing import Route

    return [Route("/api/board", rest_board, methods=["GET"]),
            Route("/api/board/task", rest_task, methods=["GET", "POST"])]


# -- /app/api: the owner, from a protected owner session -------------------------


async def owner_board(request):
    """GET reads the board (``since``, ``limit``) or one task (``task_id``); POST writes one."""
    from tinyassets.onboarding import _read_small_json

    identity, _, refused = await _owner(request)
    if refused:
        return refused
    if request.method == "GET":
        if "task_id" in request.query_params:
            return await _answer(task, identity, task_id=request.query_params["task_id"])
        return await _answer(board, identity, since=_query_int(request, "since", 0),
                             limit=_query_int(request, "limit", 30))
    data = await _read_small_json(request, limit=65536)
    if data is None:
        return JSONResponse({"error": "invalid_json"}, status_code=400, headers=_HEADERS)
    return await _answer(_write_body, identity, data)

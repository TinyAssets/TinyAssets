"""Settle agent turns a dead daemon left mid-flight. Never resurrect one.

A deploy recreates the container, which kills whatever turn was running. The
journal keeps the last thing that process committed, so the row stays in a
progressing state forever: on 2026-09-26 a 22:31:54Z deploy killed the founder's
turn ``652a2f31e82546a1bb56b5d158b5490f`` and its row read ``native_started`` 35
minutes later, which the app's server-driven status line rendered as "your
universe is thinking ... for 34m 55s". It would have stayed that way until the
granted-turn cap (3600s) aged it out.

This settles such a row at startup, into the terminal state the journal ALREADY
has for "we cannot tell what ran":

======================  ==========================  ===============================
Row state               Transition                  Settled state
======================  ==========================  ===============================
``ready``               ``abandon``                 ``abandoned``
``inference_started``   ``finish_inference(None)``   ``held_transport``
``native_started``      ``finish_native`` /          ``held_native_unknown``
                        ``indeterminate``
``tools_pending``       ``finish_tool`` failure     ``held_tool_not_sent`` or
                                                    ``held_tool_unknown``
======================  ==========================  ===============================

No new state, and no transition this codebase did not already perform: each one
is what the coordinator itself writes when that step fails. The point is that
uncertainty is PRESERVED -- a killed native round becomes indeterminate, not
completed and not retryable -- so the surface shows the existing "we can't tell
whether actions ran" notice instead of a thinking indicator. Nothing here
replays an effect or lets a turn continue; a settled turn is over.

A ``planned`` tool is settled ``not_sent`` rather than ``unknown`` because the
journal PROVES it: a tool is recorded ``started`` before it is dispatched, so one
still ``planned`` was never sent.

The sweep acquires the command center's owner lease before writing. Earlier
owner generations are recoverable; current-generation rows additionally need
proof that their unique runner claim is dead (or absent on legacy rows).
Unknown nonempty claims are preserved. Tokens are never reused for execution,
so a dead runner cannot restart between observation and settlement.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from tinyassets import owner_lease
from tinyassets.agent_turn_coordinator import turn_effects
from tinyassets.conversation_failure import failure_notice, turn_failure
from tinyassets.storage import agent_turn_runner as runner
from tinyassets.storage import db_path
from tinyassets.storage.agent_native_records import NativeTerminal
from tinyassets.storage.agent_turn_journal import (
    WORKING_STATES,
    AgentTurnJournal,
)

_LOG = logging.getLogger(__name__)

#: Recovery reason for operator logs. Quiescent abandonment also persists its
#: reason in agent_turns.settled_reason.
REASON = "turn runner exited without settling this turn"
#: The class the notice is composed from. "something broke on our side; this is
#: not a problem with your account, your credentials or your usage limits, and
#: sending again may well work" is exactly what a killed container is.
INTERRUPTED_CODE = "platform_fault"


def _progressing_rows(path: Path) -> list[tuple[str, str, str, str, int, str, str]]:
    """Every progressing row, with the generation of the owner that created it.

    Observational, like the status projection: ``mode=rw`` opens an existing
    database and refuses to create one, and it runs no DDL, so a daemon whose
    journal has never been written brings nothing into being. Read-write rather
    than ``mode=ro`` because a WAL database missing its ``-shm`` file cannot be
    opened read-only at all, which is the state a restarted box is in.
    """
    conn = sqlite3.connect(
        path.as_uri() + "?mode=rw", uri=True, timeout=30.0, isolation_level=None,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agent_turns'"
        ).fetchone():
            return []
        columns = {r[1] for r in conn.execute("PRAGMA table_info(agent_turns)")}
        generation = "owner_generation" if "owner_generation" in columns else "0"
        claim = "runner_token" if "runner_token" in columns else "''"
        agent = "agent_id" if "agent_id" in columns else "'main'"
        rows = conn.execute(
            f"SELECT {claim} AS runner_token, {agent} AS agent_id, owner_user_id, "
            f"universe_id, turn_id, state, {generation} AS g "
            f"FROM agent_turns WHERE state IN ({','.join('?' * len(WORKING_STATES))}) "
            "ORDER BY created_at",
            tuple(sorted(WORKING_STATES)),
        ).fetchall()
    finally:
        conn.close()
    return [
        (row["owner_user_id"], row["universe_id"], row["turn_id"], row["state"], int(row["g"]),
         row["runner_token"], row["agent_id"])
        for row in rows
    ]


def _settle(journal: AgentTurnJournal, owner: str, universe: str, turn_id: str):
    """Move one orphaned turn to its terminal state; returns the settled snapshot.

    Re-reads the turn under the journal's own transaction and passes the
    generation it saw, so a turn that started progressing between the scan and
    here yields a ``conflict`` transition rather than a stolen step. The SETTLED
    snapshot is what comes back, because that is the ledger the notice describes.
    """
    turn = journal.get(owner, universe, turn_id)
    if turn is None or turn.state not in WORKING_STATES:
        return turn
    ordinal = len(turn.rounds)
    if turn.state == "ready":
        transition = journal.abandon(
            owner, universe, turn_id, expected_generation=turn.generation,
        )
    elif turn.state == "native_started":
        transition = journal.finish_native(
            owner, universe, turn_id, expected_generation=turn.generation,
            ordinal=ordinal, terminal=NativeTerminal("indeterminate"),
        )
    elif turn.state == "inference_started":
        transition = journal.finish_inference(
            owner, universe, turn_id, expected_generation=turn.generation,
            ordinal=ordinal, reply=None,
        )
    else:
        transition = _settle_tool(journal, owner, universe, turn)
    if transition.status != "applied":
        raise RuntimeError(f"orphaned agent turn not settled: {transition.status}")
    return transition.snapshot


def _settle_tool(journal: AgentTurnJournal, owner: str, universe: str, turn):
    """Settle the one tool call a ``tools_pending`` turn stopped on.

    One call, not all of them: ``finish_tool``'s frontier is
    ``held_tool_<failure>`` whenever a failure is set, whatever other calls are
    still outstanding, so settling the FIRST non-completed call terminalizes the
    whole turn. It reads like it handles one of N and leaves the rest; it does not.
    """
    tools = turn.rounds[-1].tools
    target = next(tool for tool in tools if tool.state != "completed")
    generation = turn.generation
    if target.state == "planned":
        # ``finish_tool`` only settles a started call. Starting it records no
        # dispatch -- the row it writes is what ``not_sent`` is then proven from.
        started = journal.start_tool(
            owner, universe, turn.turn_id, expected_generation=generation,
            ordinal=len(turn.rounds), call_ordinal=target.ordinal,
        )
        if started.status != "applied":
            raise RuntimeError(f"orphaned agent tool not settled: {started.status}")
        generation = started.snapshot.generation
    return journal.finish_tool(
        owner, universe, turn.turn_id, expected_generation=generation,
        ordinal=len(turn.rounds), call_ordinal=target.ordinal, request=target.request,
        failure="not_sent" if target.state == "planned" else "unknown",
    )


def _notify(base_path: Path, owner: str, universe: str, turn, agent_id="main") -> bool:
    """Leave the interrupted turn visible in the founder's thread.

    Without this the turn does not merely stop reading as "thinking" -- it
    VANISHES. The notice is composed where every other failed turn's is
    (``conversation_failure``) and persisted where every other failed turn's is
    (``conversation_turns``), from this turn's own ledger evidence: a killed
    native round is ``unknown`` effects, a completed tool makes it ``some``.

    Three deliberate departures from ``record_failure``, which is what a turn
    that fails while its request is alive writes:

    * **One platform row, not a founder+platform pair.** The pair needs the
      founder's text and nothing persisted it -- the journal's ``prompt`` is
      ``history_block + founder_message`` for a granted turn
      (``universe_intelligence._call_writer``), so writing it back would re-post
      the rendered conversation history into the thread as if the founder had
      typed it. A notice with no founder half is honest about a message that was
      never stored; inventing the half is not.
    * **The session is derived, not carried.** ``f"principal:{owner}"`` is the
      same derivation ``conversation_run_admissions._Scope.session`` already uses
      over the same ``owner_user_id``, and ``check_current_home`` has proved that
      owner is the founder this universe is bound to. Safe to be wrong about:
      the notice is content-free -- stage, class, effects, ref -- so a misplaced
      one cannot carry anyone's message anywhere.
    * **Served requests only.** A ``work_invocation`` turn has no conversation
      thread to interrupt; its evidence belongs to its work receipt.

    Best-effort by contract, like every other conversation write: returns whether
    it landed and never raises into the sweep.
    """
    if turn.authority_kind != "served_request":
        return False
    effects, stage, ref = turn_effects(turn)
    record = turn_failure(
        INTERRUPTED_CODE, stage=stage or "platform", effects=effects, ref=ref,
    )
    from tinyassets.addressed_agents import memory_session
    from tinyassets.conversation_store import record_turn

    try:
        # ``ext_id`` makes this idempotent on the journal's own turn id: a sweep
        # that runs twice over one row leaves one notice, never two.
        return bool(record_turn(
            Path(base_path) / universe, memory_session(owner, agent_id), "platform",
            failure_notice(record), ext_id=f"interrupted:{turn.turn_id}", failure=record,
        ))
    except Exception:  # noqa: BLE001 - a settled row must not be reported unsettled
        _LOG.warning("interrupted-turn notice could not be written", exc_info=True)
        return False


def reconcile_orphaned_turns(base_path: str | Path, *, owner_id: str | None = None,
                             universe_id: str | None = None,
                             agent_id: str | None = None) -> list[dict[str, str]]:
    """Settle progressing rows whose owner generation or task runner has ended.

    Returns one record per row it touched: ``universe_id``, ``turn_id``, the
    ``was`` state, and either the ``settled`` state plus whether the thread was
    ``notified``, or the ``error`` that stopped it. Per-row failures do not stop
    the sweep and do not raise: one turn whose owner's home was rebound
    (``CurrentHomeChanged``) must not leave every other universe's orphan painting
    a thinking indicator, and the projection guard in ``universe_working_turn``
    covers whatever this could not settle.

    Settling is what stops the indicator; the notice (:func:`_notify`) is what
    stops the turn vanishing. They are ordered, not combined: a notice is only
    written for a row this call actually settled, so a failed settlement never
    tells the founder a turn was interrupted while its row still says otherwise.
    """
    path = db_path(Path(base_path))
    if not path.exists():
        return []
    journal = AgentTurnJournal(base_path)
    settled: list[dict[str, object]] = []
    held: dict[str, int | None] = {}
    for owner, universe, turn_id, state, generation, token, agent in _progressing_rows(path):
        if ((owner_id is not None and owner != owner_id)
                or (universe_id is not None and universe != universe_id)
                or (agent_id is not None and agent != agent_id)):
            continue
        if universe not in held:
            try:
                held[universe] = owner_lease.acquire(
                    base_path, owner_lease.key_for(universe), wait_s=0,
                ).generation
            except owner_lease.LeaseBusy:
                # A LIVE owner holds this command center: its rows are its own,
                # whatever their age. This process settles none of them.
                held[universe] = None
        if held[universe] is None:
            continue
        if generation >= held[universe] and not runner.orphan(base_path, token):
            continue
        record: dict[str, object] = {
            "universe_id": universe, "turn_id": turn_id, "was": state,
        }
        try:
            turn = _settle(journal, owner, universe, turn_id)
            record["settled"] = "" if turn is None else turn.state
            record["notified"] = turn is not None and _notify(
                base_path, owner, universe, turn, agent,
            )
        except Exception as exc:  # noqa: BLE001 - one unreachable row is not the sweep
            record["error"] = type(exc).__name__
            _LOG.error(
                "orphaned agent turn %s (%s) could not be settled: %s",
                turn_id, state, type(exc).__name__, exc_info=True,
            )
        else:
            _LOG.warning(
                "orphaned agent turn %s settled %s -> %s (thread notified: %s): %s",
                turn_id, state, record["settled"], record["notified"], REASON,
            )
        settled.append(record)
    return settled

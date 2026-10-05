"""The owner's Custom Rules for their agents (harness D1a).

Founder, 2026-10-01: a new user's universe works the way ChatGPT dots work,
and dots act under per-action Custom Rules with four behaviours: take action
without asking, take action if pre-approved, ask before taking action, or hand
off to you (design #4172 §4.8-4.10).

A rule names an *action class* (what kind of thing the agent is about to do),
optionally narrowed to one connection and one operation, and gives it one of
the four behaviours. Every enforcement point asks :func:`decide` before it acts;
the most specific matching rule wins, and between equally specific rules the
stricter one does.

**Who writes rules.** Only the owner, through the owner door. The store lives
beside the session records in the data root's ``.agent-sessions/<universe>/``,
outside every universe folder, so no process the universe runs (the agent's
tools, a workflow's provider jail, an extension) can create, read or change
it. An agent that wants a different rule raises a request.

**Defaults are rules, not policy.** A universe is seeded with rules that
reproduce dots: inside the universe the agent just works; reaching other people,
publishing, sharing and spending ask first; and three hand-backs (moving money,
security changes, granting others access) hand off to the owner. All of them
are the owner's to edit -- the only fixed platform floor is cross-user isolation
-- but loosening a hand-back must be confirmed against the plain words in
:data:`HANDBACK_CONSEQUENCES`.

D1a enforces at the credential-blind effector, where rules can only TIGHTEN the
standing destination grants already checked there. Declared operation kinds
(which make the hand-back classes bind), the agent's execution context, and the
auto-review arrive in D1b-D1d.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from tinyassets import agent_sessions
from tinyassets.owner_control import serialized

DO = "do"
DO_IF_PREAPPROVED = "do_if_preapproved"
ASK_FIRST = "ask_first"
HAND_OFF = "hand_off"
#: Strictest last; ties between equally specific rules go to the stricter.
BEHAVIOURS = (DO, DO_IF_PREAPPROVED, ASK_FIRST, HAND_OFF)

#: Plain words for each behaviour, as the Rules tab says them.
BEHAVIOUR_LABELS = {
    DO: "Take action without asking",
    DO_IF_PREAPPROVED: "Take action if pre-approved",
    ASK_FIRST: "Ask before taking action",
    HAND_OFF: "Hand off to you",
}

#: The action classes the platform can recognise, with what each covers.
ACTION_CLASSES = {
    "workspace.files": "Reading and changing files in its own workspace",
    "workspace.shell": "Running commands in its own workspace",
    "workspace.workflows": "Creating, editing and running its own workflows",
    "shell.egress": "Using the public internet from its shell",
    "app.read": "Reading from a connected app",
    "app.write": "Changing something in a connected app",
    "people.message": "Sending a message to a person",
    "commons.publish": "Publishing to the commons",
    "share": "Sharing your universe or its work with someone",
    "spend": "Spending money over a budget you set",
    "browser.action": "Submitting a form or pressing a purchase button in its browser",
    "money.move": "Moving money or making a payment",
    "security.change": "Changing a password, credential or security setting",
    "access.grant": "Giving another person access to your accounts or data",
}

#: The three dots hand-backs, and what turning one off allows, said plainly.
HANDBACK_CONSEQUENCES = {
    "money.move": "Your agent will be able to move money or pay from connected "
                  "accounts without handing it to you.",
    "security.change": "Your agent will be able to change passwords, credentials "
                       "and security settings on your accounts without handing it to you.",
    "access.grant": "Your agent will be able to give other people access to your "
                    "accounts or data without handing it to you.",
}

#: The seed a new universe starts from (design #4172 §4.8 seed table).
SEED_RULES = (
    ("workspace.files", DO), ("workspace.shell", DO), ("workspace.workflows", DO),
    ("shell.egress", DO), ("app.read", DO),
    # A write to a destination the owner already granted proceeds on that grant
    # (checked at the effector); this rule decides everything before it.
    ("app.write", DO),
    ("people.message", ASK_FIRST), ("commons.publish", ASK_FIRST), ("share", ASK_FIRST),
    ("spend", ASK_FIRST), ("browser.action", ASK_FIRST),
    ("money.move", HAND_OFF), ("security.change", HAND_OFF), ("access.grant", HAND_OFF),
)

#: The agent a rule is for. D8 adds a roster; until then every rule is the main agent's.
MAIN_AGENT = "main"

#: What an operation on a connection MEANS, as its owner declares it (harness
#: D1b), and the action class each meaning is decided under. Nothing in a raw
#: HTTP method or URL establishes "payment" or "message", so a meaning exists
#: only where the owner declared it; an undeclared operation is a write.
OPERATION_KINDS = {
    "read": "app.read",
    "write": "app.write",
    "delete": "app.write",
    "message": "people.message",
    "payment": "money.move",
    "security": "security.change",
    "access": "access.grant",
}

_FILE = "rules.db"
_KINDS_SCHEMA = """CREATE TABLE IF NOT EXISTS operation_kinds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    path_prefix TEXT NOT NULL DEFAULT '/',
    kind TEXT NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(connection, method, path_prefix))"""
_SCHEMA = """CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    action_class TEXT NOT NULL,
    connection TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT '',
    behaviour TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    seeded INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    UNIQUE(agent, action_class, connection, operation))"""


class RuleRefused(ValueError):
    """A rule change cannot be saved as asked (unknown class or behaviour, or a
    hand-back loosened without confirming what that allows)."""


@dataclass(frozen=True, slots=True)
class Rule:
    id: int
    agent: str
    action_class: str
    connection: str
    operation: str
    behaviour: str
    note: str = ""
    seeded: bool = False
    kind: str = "behavior"
    grant: dict | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id, "agent": self.agent, "action_class": self.action_class,
            "covers": ACTION_CLASSES.get(self.action_class, ""),
            "connection": self.connection, "operation": self.operation,
            "behaviour": self.behaviour, "label": BEHAVIOUR_LABELS[self.behaviour],
            "note": self.note, "seeded": self.seeded, "kind": self.kind,
            "grant": self.grant,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    behaviour: str
    rule_id: int | None
    reason: str

    @property
    def proceeds(self) -> bool:
        return self.behaviour == DO


def _connect(universe_dir: Path) -> sqlite3.Connection:
    path = agent_sessions._records_dir(Path(universe_dir)) / _FILE
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute(_SCHEMA)
    conn.execute(_KINDS_SCHEMA)
    if "record_kind" not in {r[1] for r in conn.execute("PRAGMA table_info(rules)")}:
        from tinyassets.owner_control import control

        with control(universe_dir):
            conn.execute("BEGIN IMMEDIATE")
            if "record_kind" not in {r[1] for r in conn.execute("PRAGMA table_info(rules)")}:
                # Preserve IDs, behavior precedence and owner edits. A separate
                # partial index permits several independently revocable grants.
                conn.execute("ALTER TABLE rules RENAME TO rules_before_scopes")
                conn.execute(_SCHEMA.replace(
                    "UNIQUE(agent, action_class, connection, operation)",
                    "record_kind TEXT NOT NULL DEFAULT 'behavior', "
                    "decision_id TEXT UNIQUE, grant_json TEXT NOT NULL DEFAULT '{}'"
                ))
                conn.execute(
                    "INSERT INTO rules (id,agent,action_class,connection,operation,behaviour,"
                    "note,seeded,updated_at) SELECT id,agent,action_class,connection,operation,"
                    "behaviour,"
                    "note,seeded,updated_at FROM rules_before_scopes"
                )
                conn.execute("DROP TABLE rules_before_scopes")
                conn.execute(
                    "CREATE UNIQUE INDEX behavior_rule_key ON rules "
                    "(agent,action_class,connection,operation) WHERE record_kind='behavior'"
                )
            conn.commit()
    return conn


def _seed(conn: sqlite3.Connection, agent: str) -> None:
    """Write the seed for ``agent`` once; an owner's edits are never re-seeded."""
    present = conn.execute("SELECT COUNT(*) FROM rules WHERE agent = ?", (agent,)).fetchone()[0]
    if present:
        return
    now = time.time()
    conn.executemany(
        "INSERT OR IGNORE INTO rules (agent, action_class, behaviour, seeded, updated_at) "
        "VALUES (?, ?, ?, 1, ?)",
        [(agent, cls, behaviour, now) for cls, behaviour in SEED_RULES],
    )


def _rule(row) -> Rule:
    return Rule(int(row[0]), row[1], row[2], row[3], row[4], row[5], row[6], bool(row[7]),
                row[8] if len(row) > 8 else "behavior",
                json.loads(row[9]) if len(row) > 9 else None)


_SELECT = ("SELECT id, agent, action_class, connection, operation, behaviour, note, seeded, "
           "record_kind, grant_json "
           "FROM rules WHERE agent = ? ORDER BY action_class, connection, operation")


def list_rules(universe_dir: Path, agent: str = MAIN_AGENT) -> list[Rule]:
    """Every rule of ``agent``, seeding a new universe's defaults first.

    An ordinary read once seeded; only the first read takes the write lock (and
    rechecks inside it), so decisions never queue behind each other.
    """
    with closing(_connect(universe_dir)) as conn:
        rows = conn.execute(_SELECT, (agent,)).fetchall()
        if not rows:
            conn.execute("BEGIN IMMEDIATE")
            _seed(conn, agent)
            rows = conn.execute(_SELECT, (agent,)).fetchall()
            conn.execute("COMMIT")
    return [rule for row in rows if not (rule := _rule(row)).grant
            or not rule.grant.get("revoked")]


def configured_agents(universe_dir: Path) -> set[str]:
    """Agents with stored rules, for conservative checks without run provenance."""
    with closing(_connect(universe_dir)) as conn:
        return {row[0] for row in conn.execute("SELECT DISTINCT agent FROM rules")}


def _specificity(rule: Rule) -> int:
    """How many dimensions a rule narrows. Connection and operation count the
    same: neither outranks the other, so overlapping narrow rules go to the
    stricter (gpt-6-astra on #4193)."""
    return (1 if rule.connection else 0) + (1 if rule.operation else 0)


def decide(universe_dir: Path, action_class: str, *, connection: str = "",
           operation: str = "", agent: str = MAIN_AGENT) -> Decision:
    """The behaviour for one action, from the owner's rules.

    A class no rule names is asked about first, never assumed allowed.
    """
    candidates = [
        rule for rule in list_rules(universe_dir, agent)
        if rule.kind == "behavior" and rule.action_class == action_class
        and rule.connection in ("", connection)
        and rule.operation in ("", operation.upper())
    ]
    if not candidates:
        return Decision(ASK_FIRST, None, f"no rule covers {action_class}; asking first")
    best = max(candidates, key=lambda r: (_specificity(r), BEHAVIOURS.index(r.behaviour)))
    return Decision(best.behaviour, best.id,
                    f"{BEHAVIOUR_LABELS[best.behaviour]} ({best.action_class}"
                    + (f" on {best.connection}" if best.connection else "")
                    + (f" {best.operation}" if best.operation else "") + ")")


@serialized
def set_rule(universe_dir: Path, action_class: str, behaviour: str, *, connection: str = "",
             operation: str = "", note: str = "", agent: str = MAIN_AGENT,
             confirm_handback: bool = False) -> Rule:
    """Save one rule (the owner door is the only caller).

    Loosening a hand-back class below ``hand_off`` is refused unless the owner
    confirmed what that allows (:data:`HANDBACK_CONSEQUENCES`).
    """
    if action_class not in ACTION_CLASSES:
        raise RuleRefused(f"unknown action class {action_class!r}")
    if behaviour not in BEHAVIOURS:
        raise RuleRefused(f"unknown behaviour {behaviour!r}")
    if (action_class in HANDBACK_CONSEQUENCES and behaviour != HAND_OFF
            and not confirm_handback):
        raise RuleRefused(HANDBACK_CONSEQUENCES[action_class]
                          + " Confirm to save this rule.")
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _seed(conn, agent)
        conn.execute(
            "INSERT INTO rules (agent, action_class, connection, operation, behaviour, note, "
            "seeded, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?) "
            "ON CONFLICT(agent, action_class, connection, operation) "
            "WHERE record_kind='behavior' DO UPDATE SET "
            "behaviour = excluded.behaviour, note = excluded.note, seeded = 0, "
            "updated_at = excluded.updated_at",
            (agent, action_class, connection.strip(), operation.strip().upper(),
             behaviour, note.strip()[:500], time.time()),
        )
        row = conn.execute(
            "SELECT id, agent, action_class, connection, operation, behaviour, note, seeded "
            "FROM rules WHERE agent = ? AND action_class = ? AND connection = ? "
            "AND operation = ? AND record_kind='behavior'",
            (agent, action_class, connection.strip(), operation.strip().upper()),
        ).fetchone()
        conn.execute("COMMIT")
    return _rule(row)


@serialized
def delete_rule(universe_dir: Path, rule_id: int, *, agent: str = MAIN_AGENT,
                confirm_handback: bool = False) -> bool:
    """Remove one narrowed rule. A class-wide rule is changed, never removed, so
    every class keeps a visible behaviour.

    Removing a hand-back rule that leaves a weaker behaviour in its place needs
    the same confirmation as setting one (gpt-6-astra on #4193).
    """
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT action_class, connection, operation, behaviour, record_kind, grant_json "
            "FROM rules "
            "WHERE id = ? AND agent = ?",
            (int(rule_id), agent),
        ).fetchone()
        if row is None or not (row[1] or row[2]):
            conn.execute("ROLLBACK")
            return False
        if row[4] == 'preapproval':
            grant = json.loads(row[5])
            grant['revoked'] = True
            conn.execute('UPDATE rules SET grant_json=?,updated_at=? WHERE id=?',
                         (json.dumps(grant), time.time(), int(rule_id)))
            conn.commit()
            return True
        action_class, connection, operation, behaviour = row[:4]
        if (action_class in HANDBACK_CONSEQUENCES and behaviour == HAND_OFF
                and not confirm_handback):
            remaining = [
                _rule(r) for r in conn.execute(_SELECT, (agent,)).fetchall()
                if r[8] == "behavior" and r[0] != int(rule_id) and r[2] == action_class
                and r[3] in ("", connection) and r[4] in ("", operation)
            ]
            after = max(remaining, key=lambda r: (_specificity(r),
                                                  BEHAVIOURS.index(r.behaviour)),
                        default=None)
            if after is None or after.behaviour != HAND_OFF:
                conn.execute("ROLLBACK")
                raise RuleRefused(HANDBACK_CONSEQUENCES[action_class]
                                  + " Confirm to remove this rule.")
        conn.execute("DELETE FROM rules WHERE id = ?", (int(rule_id),))
        conn.execute("COMMIT")
    return True


# -- declared operation kinds (D1b) ----------------------------------------------


@dataclass(frozen=True, slots=True)
class OperationKind:
    id: int
    connection: str
    method: str
    path_prefix: str
    kind: str

    def as_dict(self) -> dict:
        return {"id": self.id, "connection": self.connection, "method": self.method,
                "path_prefix": self.path_prefix, "kind": self.kind,
                "decided_as": OPERATION_KINDS[self.kind]}


def _normal_prefix(path_prefix: str) -> str:
    prefix = str(path_prefix or "/").strip() or "/"
    if not prefix.startswith("/") or any(c in prefix for c in "?#\\\x00"):
        raise RuleRefused("a path prefix starts with / and has no query or fragment")
    return _canonical_path(prefix)


def _canonical_path(path: str) -> str:
    """One spelling per path: no trailing slash except the root itself, so
    ``/v1/charges/`` and ``/v1/charges`` are one declaration and rank alike
    (gpt-6-astra on #4199)."""
    path = str(path or "/") or "/"
    return path.rstrip("/") or "/"


def _scope_decision(universe_dir: Path, connection: str, method: str, prefix: str) -> Decision:
    action_class, operation = classify(universe_dir, connection, method or "POST", prefix)
    return decide(universe_dir, action_class, connection=connection, operation=operation)


def _loosening(before: Decision, after: Decision) -> bool:
    return BEHAVIOURS.index(after.behaviour) < BEHAVIOURS.index(before.behaviour)


def _loosening_words(scope: str, before: Decision, after: Decision) -> str:
    return (f"Calls to {scope} will be decided as {after.reason} instead of "
            f"{before.reason}. Confirm to save this.")


@serialized
def declare_kind(universe_dir: Path, connection: str, kind: str, *,
                 method: str = "", path_prefix: str = "/",
                 confirm: bool = False) -> OperationKind:
    """The owner says what operations on one connection mean.

    A declaration that makes its calls decided more permissively than before
    (``read`` over a path that asked first as a write, or replacing a payment)
    needs the owner to confirm the server's own description of the change.
    """
    connection = str(connection or "").strip()
    if not connection:
        raise RuleRefused("a declaration names its connection")
    if kind not in OPERATION_KINDS:
        raise RuleRefused(f"unknown operation kind {kind!r}")
    method, prefix = str(method or "").strip().upper(), _normal_prefix(path_prefix)
    if not confirm:
        before = _scope_decision(universe_dir, connection, method, prefix)
        after = decide(universe_dir, OPERATION_KINDS[kind], connection=connection,
                       operation=method or "POST")
        if _loosening(before, after):
            raise RuleRefused(_loosening_words(
                f"{connection} {method or 'any method'} {prefix}", before, after))
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO operation_kinds (connection, method, path_prefix, kind, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(connection, method, path_prefix) DO UPDATE "
            "SET kind = excluded.kind, updated_at = excluded.updated_at",
            (connection, method, prefix, kind, time.time()),
        )
        row = conn.execute(
            "SELECT id, connection, method, path_prefix, kind FROM operation_kinds "
            "WHERE connection = ? AND method = ? AND path_prefix = ?",
            (connection, method, prefix),
        ).fetchone()
        conn.execute("COMMIT")
    return OperationKind(int(row[0]), row[1], row[2], row[3], row[4])


def _kind_rows(conn: sqlite3.Connection) -> list[OperationKind]:
    rows = conn.execute(
        "SELECT id, connection, method, path_prefix, kind FROM operation_kinds "
        "ORDER BY connection, path_prefix, method",
    ).fetchall()
    return [OperationKind(int(r[0]), r[1], r[2], r[3], r[4]) for r in rows]


def list_kinds(universe_dir: Path) -> list[OperationKind]:
    with closing(_connect(universe_dir)) as conn:
        return _kind_rows(conn)


@serialized
def delete_kind(universe_dir: Path, kind_id: int, *, confirm: bool = False) -> bool:
    """Remove one declaration; a removal that loosens its calls needs confirming."""
    target = next((k for k in list_kinds(universe_dir) if k.id == int(kind_id)), None)
    if target is None:
        return False
    if not confirm:
        before = _scope_decision(universe_dir, target.connection, target.method,
                                 target.path_prefix)
        with closing(_connect(universe_dir)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM operation_kinds WHERE id = ?", (target.id,))
            after_class, operation = _classify_rows(
                _kind_rows(conn), target.connection, target.method or "POST",
                target.path_prefix)
            conn.execute("ROLLBACK")
        after = decide(universe_dir, after_class, connection=target.connection,
                       operation=operation)
        if _loosening(before, after):
            raise RuleRefused(_loosening_words(
                f"{target.connection} {target.method or 'any method'} {target.path_prefix}",
                before, after))
    with closing(_connect(universe_dir)) as conn:
        cursor = conn.execute("DELETE FROM operation_kinds WHERE id = ?", (target.id,))
    return cursor.rowcount > 0


def classify(universe_dir: Path, connection: str, method: str, path: str) -> tuple[str, str]:
    """``(action_class, operation)`` for one call, from the owner's declarations.

    The longest matching path prefix wins, a method-specific declaration over
    an any-method one. No declaration: a consequential write.
    """
    return _classify_rows(list_kinds(universe_dir), connection, method, path)


def _classify_rows(rows: list[OperationKind], connection: str, method: str,
                   path: str) -> tuple[str, str]:
    method = str(method or "").upper()
    path = _canonical_path(path)
    best: OperationKind | None = None
    for item in rows:
        if item.connection != connection or item.method not in ("", method):
            continue
        if not (path == item.path_prefix or path.startswith(item.path_prefix.rstrip("/") + "/")
                or item.path_prefix == "/"):
            continue
        rank = (len(item.path_prefix), 1 if item.method else 0)
        if best is None or rank > (len(best.path_prefix), 1 if best.method else 0):
            best = item
    if best is None:
        return "app.write", method
    return OPERATION_KINDS[best.kind], method

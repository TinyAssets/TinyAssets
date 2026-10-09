"""Owner-scoped inference usage, independent of chat delivery and effect receipts.

The daemon creates roots and dispatch references. Broker processes only consume
references against the same database. A reservation is accounting permission,
never provider authority: the existing grant, fence and cancellation checks
still govern each send. Nothing here proves upstream receipt or billing.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import timezone
from pathlib import Path

from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.storage import DB_FILENAME

_SCOPE = "owner = ? AND universe = ? AND usage_id = ?"
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS agent_request_usage (
      owner TEXT NOT NULL, universe TEXT NOT NULL, usage_id TEXT NOT NULL,
      policy_json TEXT NOT NULL, failures_json TEXT NOT NULL,
      owner_token TEXT NOT NULL, parent_token TEXT NOT NULL, closed INTEGER NOT NULL,
      created_at TEXT NOT NULL, PRIMARY KEY(owner, universe, usage_id))""",
    """CREATE TABLE IF NOT EXISTS agent_request_attempts (
      owner TEXT NOT NULL, universe TEXT NOT NULL, usage_id TEXT NOT NULL,
      ordinal INTEGER NOT NULL, attempt_json TEXT NOT NULL, dispatched_at TEXT,
      source_ref TEXT NOT NULL, state TEXT NOT NULL,
      PRIMARY KEY(owner, universe, usage_id, ordinal))""",
    """CREATE INDEX IF NOT EXISTS agent_request_attempt_day
      ON agent_request_attempts(owner, source_ref, dispatched_at)""",
    """CREATE TABLE IF NOT EXISTS agent_request_usage_links (
      owner TEXT NOT NULL, universe TEXT NOT NULL, usage_id TEXT NOT NULL,
      kind TEXT NOT NULL, subject_id TEXT NOT NULL,
      PRIMARY KEY(owner, universe, kind, subject_id, usage_id))""",
    """CREATE TABLE IF NOT EXISTS agent_request_dispatches (
      reference_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, universe TEXT NOT NULL,
      usage_id TEXT NOT NULL, first_ordinal INTEGER NOT NULL, last_ordinal INTEGER NOT NULL,
      grant_id TEXT NOT NULL, connection_id TEXT NOT NULL, request_digest TEXT NOT NULL,
      operation_id TEXT NOT NULL, claimed INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,
      UNIQUE(owner, universe, usage_id, first_ordinal))""",
)


# Each parent owns a kernel lock independently of the daemon process. Releasing
# it fences future work even if SQLite cannot commit the close. Fork children
# close inherited descriptors without unlocking the parent's open-file lock.
_PARENT_LEASES = {}
_PARENT_LEASES_LOCK = threading.RLock()


def _after_fork():
    global _PARENT_LEASES_LOCK
    for held in _PARENT_LEASES.values():
        if held.fd is not None:
            os.close(held.fd)
    _PARENT_LEASES.clear()
    _PARENT_LEASES_LOCK = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


class ParentUsageLease:
    def __init__(self, base, token, *, broker_readable=False):
        from tinyassets.process_liveness import hold_liveness

        self.token = token
        with _PARENT_LEASES_LOCK:
            _PARENT_LEASES[token] = (hold_liveness(base, token, broker_readable=True)
                                     if broker_readable else hold_liveness(base, token))

    def close(self):
        from tinyassets.singleton_lock import release_singleton_lock

        with _PARENT_LEASES_LOCK:
            held = _PARENT_LEASES.pop(self.token, None)
            if held is not None:
                release_singleton_lock(held)


def request_digest(grant_id, connection_id, verb, request):
    raw = json.dumps([grant_id, connection_id, verb, request], sort_keys=True,
                     separators=(",", ":"), allow_nan=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _reference_hash(reference):
    if (not isinstance(reference, str) or len(reference) != 64
            or any(c not in "0123456789abcdef" for c in reference)):
        raise ProviderAuthorityHeldError("invalid inference usage reference")
    return hashlib.sha256(reference.encode()).hexdigest()


@dataclass(frozen=True)
class InferenceUsageReference:
    """A private IPC selector, not part of an HTTP request or an authorization grant."""

    usage_id: str
    reference: str
    operation_id: str

    def document(self):
        return asdict(self)

    @classmethod
    def from_document(cls, value):
        if (not isinstance(value, dict)
                or set(value) != {"usage_id", "reference", "operation_id"}
                or any(not isinstance(v, str) or not v for v in value.values())):
            raise ProviderAuthorityHeldError("invalid inference usage envelope")
        _reference_hash(value["reference"])
        return cls(**value)


class InferenceUsageRequired(ProviderAuthorityHeldError):
    """An unaccounted model POST; fixed text is safe across private IPC."""

    def __init__(self):
        super().__init__(
            "HTTP inference requires a parent usage reference. Run the review as a "
            "prompt_template node through run_graph with an owner-approved model; "
            "direct connection POSTs cannot supply inference accounting. Read "
            'read_graph target="handbook" query="write_graph.connections" for the '
            "inference recovery and model-access approval steps."
        )


class InferenceUsageStopped(ProviderAuthorityHeldError):
    """Fixed private IPC stop. The caller reloads its own durable receipt."""

    REASONS = frozenset({"parent_closed", "dispatch_closed", "dispatch_deadline",
                         "automatic_learning_disabled", "turn_attempt_limit",
                         "free_pool_attempt_limit", "free_attempt_limit", "consecutive_failures"})

    def __init__(self, reason, usage_id):
        if (not isinstance(reason, str) or reason not in self.REASONS
                or not isinstance(usage_id, str) or len(usage_id) != 32
                or any(c not in "0123456789abcdef" for c in usage_id)):
            raise ProviderAuthorityHeldError("invalid inference accounting stop")
        self.reason, self.usage_id = reason, usage_id
        super().__init__("parent inference request allocation stopped")


def resolve_inference_usage(base, owner, universe, ledger, resource, grant_id, verb, request,
                            envelope, operation_id):
    """Trusted factory binding; wire fields cannot turn accounting off or buy capacity."""
    # Only the broker's relocated ledger lives under .broker/; any other ledger
    # keeps the daemon's own accounting database.
    db_path = getattr(ledger, "_db_path", None)
    relocated = (db_path is not None and
                 Path(db_path).resolve() == Path(base).resolve() / ".broker" / "outbound.db")
    store = UsageStore(base, broker_ledger=ledger if relocated else None)
    root = (store.base / universe).resolve()
    if root.parent != store.base:
        raise ProviderAuthorityHeldError("inference accounting command center changed")
    if envelope is None:
        if resource.connection_type != "http" or str(verb).upper() != "POST":
            return None
        # Both installed capabilities and registered compute descriptors identify
        # inference sources; a request cannot evade this by omitting model/body.
        is_model = any(ledger.get_connection_capability(resource.connection_id, kind) is not None
                       for kind in ("model_use", "model_discovery"))
        from tinyassets.providers.definition import _verified_definition
        from tinyassets.universe_files import read_universe_file

        try:
            definitions = json.loads(read_universe_file(
                store.base, f"{universe}/provider_definitions.json",
            ))
        except FileNotFoundError:
            definitions = ()
        except (OSError, ValueError, TypeError) as exc:
            raise ProviderAuthorityHeldError(
                "inference source definitions are unavailable",
            ) from exc
        is_model = is_model or any(
            (definition := _verified_definition(row, expect_universe=universe)).ref == grant_id
            and definition.owner_user_id == owner and definition.access_method == "api_key_http"
            for row in definitions
        )
        if is_model:
            raise InferenceUsageRequired()
        return None
    ref = InferenceUsageReference.from_document(envelope)
    if ref.operation_id != operation_id:
        raise ProviderAuthorityHeldError("inference usage operation changed")
    return store.claim_reference(ref.reference, owner=owner, universe=universe,
                                 usage_id=ref.usage_id, grant_id=grant_id,
                                 connection_id=resource.connection_id, verb=verb, request=request,
                                 operation_id=operation_id)


class UsageStore:
    """One trusted data root. No database path is ever read from a wire envelope."""

    def __init__(self, base_path, *, broker_ledger=None):
        from tinyassets.broker.supervisor import broker_selected

        self.base = Path(base_path).resolve()
        self._ledger = broker_ledger
        self._remote = broker_ledger is None and broker_selected()
        self.path = broker_ledger._db_path if broker_ledger is not None else self.base / DB_FILENAME

    def _rpc(self, scope, action, **fields):
        from tinyassets.broker.usage import operation

        return operation(self.base, scope, {"action": action, **fields})

    @contextmanager
    def _connection(self, *, write=False):
        if self._remote:
            raise ProviderAuthorityHeldError("daemon accounting requires broker IPC")
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            for statement in _SCHEMA:
                conn.execute(statement)
            if write:
                conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
            else:
                if conn.in_transaction:
                    conn.commit()

    def create(self, budget):
        from tinyassets.process_liveness import owner_token

        usage_id = secrets.token_hex(16)
        policy = {name: getattr(budget, name) for name in (
            "max_requests", "free_limit", "free_pool_limit", "failure_limit", "source_limits",
            "deadline",
        )}
        scope = (budget.owner, budget.universe, usage_id)
        token = owner_token(self.base, broker_readable=self._remote)
        parent_token = "request_" + usage_id
        lease = ParentUsageLease(self.base, parent_token, broker_readable=self._remote)
        try:
            if self._remote:
                self._rpc(scope, "create", policy=policy, failures=budget._failures,
                          attempts=[asdict(a) for a in budget._attempts], closed=budget._closed,
                          owner_token=token, parent_token=parent_token)
            else:
                self._insert(scope, budget, token, parent_token)
        except BaseException:
            lease.close()
            raise
        if budget._closed:
            lease.close()
        return usage_id, lease

    def _insert(self, scope, budget, token, parent_token):
        policy = {name: getattr(budget, name) for name in (
            "max_requests", "free_limit", "free_pool_limit", "failure_limit", "source_limits",
            "deadline",
        )}
        with self._connection(write=True) as conn:
            conn.execute("INSERT INTO agent_request_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (*scope, json.dumps(policy), json.dumps(budget._failures),
                          token, parent_token, int(budget._closed),
                          budget.wall_clock().astimezone(timezone.utc).isoformat()))
            self._save(conn, scope, budget)

    def _load(self, conn, scope, *, clock=time.monotonic, wall_clock=None):
        from tinyassets.process_liveness import ALIVE, owner_state
        from tinyassets.request_budget import RequestAttempt, TurnRequestBudget

        row = conn.execute(f"SELECT * FROM agent_request_usage WHERE {_SCOPE}", scope).fetchone()
        if row is None:
            raise ProviderAuthorityHeldError("inference usage scope is unavailable")
        budget = TurnRequestBudget(scope[0], scope[1], clock=clock, wall_clock=wall_clock,
                                   **json.loads(row["policy_json"]))
        budget._closed = bool(row["closed"])
        budget._failures = json.loads(row["failures_json"])
        budget._attempts = [RequestAttempt(**json.loads(a[0])) for a in conn.execute(
            f"SELECT attempt_json FROM agent_request_attempts WHERE {_SCOPE} ORDER BY ordinal",
            scope,
        )]
        # Monotonic deadlines are used only while the original process is
        # provably alive. A reboot/dead/unknown parent can never renew them.
        if owner_state(self.base, row["parent_token"]) != ALIVE:
            budget._closed = True
        if owner_state(self.base, row["owner_token"]) != ALIVE:
            from dataclasses import replace

            budget._closed = True
            budget._attempts = [replace(a, state="unknown") if a.state == "dispatched" else a
                                for a in budget._attempts]
        return budget

    @staticmethod
    def _save(conn, scope, budget):
        conn.execute(f"UPDATE agent_request_usage SET closed = ?, failures_json = ? WHERE {_SCOPE}",
                     (int(budget._closed), json.dumps(budget._failures), *scope))
        for a in budget._attempts:
            conn.execute(
                "INSERT INTO agent_request_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(owner, universe, usage_id, ordinal) DO UPDATE SET "
                "attempt_json=excluded.attempt_json, dispatched_at=excluded.dispatched_at, "
                "state=excluded.state",
                (*scope, a.ordinal, json.dumps(asdict(a), separators=(",", ":")),
                 a.dispatched_at, a.source_ref, a.state),
            )

    def mutate(self, scope, operation, *args, clock=time.monotonic, wall_clock=None, **kwargs):
        from tinyassets.request_budget import RequestBudgetExceeded

        if self._remote:
            from tinyassets.broker.usage import mutation_document

            if (operation == "reserve"
                    and (kwargs.get("owner"), kwargs.get("universe")) != scope[:2]):
                raise ProviderAuthorityHeldError("parent request budget scope changed")
            document = mutation_document(operation, args, kwargs)
            return self._rpc(scope, **document)["value"]
        error = None
        with self._connection(write=True) as conn:
            budget = self._load(conn, scope, clock=clock, wall_clock=wall_clock)
            try:
                result = getattr(budget, operation)(*args, **kwargs)
            except RequestBudgetExceeded as exc:
                error, result = exc, None
            self._save(conn, scope, budget)
        if error is not None:
            error.request_receipt["usage_id"] = scope[2]
            raise error
        return result

    def receipt(self, scope, *, clock=time.monotonic, wall_clock=None):
        if self._remote:
            return self._rpc(scope, "receipt")["receipt"]
        # Reconcile a provably abandoned dispatch conservatively; no replay.
        with self._connection(write=True) as conn:
            budget = self._load(conn, scope, clock=clock, wall_clock=wall_clock)
            self._save(conn, scope, budget)
            return {**budget.receipt(), "usage_id": scope[2]}

    def link(self, scope, kind, subject_id):
        if self._remote:
            return self._rpc(scope, "link", kind=kind, subject_id=subject_id)["value"]
        if kind not in {"turn", "run"} or not isinstance(subject_id, str) or not subject_id:
            raise ValueError("invalid inference usage link")
        with self._connection(write=True) as conn:
            budget = self._load(conn, scope)
            self._save(conn, scope, budget)
            conn.execute("INSERT OR IGNORE INTO agent_request_usage_links VALUES (?, ?, ?, ?, ?)",
                         (*scope, kind, subject_id))

    def for_subject(self, owner, universe, kind, subject_id):
        if self._remote:
            return self._rpc((owner, universe, ""), "for_subject",
                             kind=kind, subject_id=subject_id)["receipts"]
        with self._connection() as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT usage_id FROM agent_request_usage_links "
                "WHERE owner=? AND universe=? AND kind=? AND subject_id=?",
                (owner, universe, kind, subject_id),
            )]
        return [self.receipt((owner, universe, uid)) for uid in ids]

    def _validate_source(self, scope, attempt, grant_id, connection_id, verb, request):
        """Bind server admission to its installed descriptor and current grant.

        A paid reservation cannot be relabelled as another HTTP source. Model
        discovery can select a model other than the descriptor's default; the
        wire model must be the model the router actually reserved.
        """
        from tinyassets.providers.definition import _verified_definition

        try:
            root = (self.base / scope[1]).resolve()
            if (root.parent != self.base or not attempt.source_ref.startswith("api_key_http:")
                    or verb != "POST" or not isinstance(request, dict)
                    or not isinstance(request.get("body"), dict)
                    or request["body"].get("model") != attempt.model):
                raise ValueError("source or model mismatch")
            from tinyassets.universe_files import read_universe_file

            rows = json.loads(read_universe_file(
                self.base, f"{scope[1]}/provider_definitions.json",
            ))
            definition = next(_verified_definition(row, expect_universe=scope[1])
                              for row in rows
                              if row.get("id") == attempt.source_ref.removeprefix("api_key_http:"))
            if (definition.owner_user_id != scope[0] or definition.ref != grant_id
                    or definition.access_method != "api_key_http"):
                raise ValueError("source grant mismatch")
            self._validate_source_grant(scope, grant_id, connection_id)
        except (OSError, ValueError, TypeError, KeyError, StopIteration, sqlite3.Error) as exc:
            raise ProviderAuthorityHeldError(
                "inference usage reservation does not match its admitted source"
            ) from exc

    def _validate_source_grant(self, scope, grant_id, connection_id):
        from tinyassets.broker.supervisor import broker_selected

        if self._ledger is not None:
            from tinyassets.broker.ledger_queries import GRANTED_RESOURCE, local_query

            local_query(self._ledger, query=GRANTED_RESOURCE, principal=scope[0],
                        command_center=scope[1], grant_id=grant_id, connection_id=connection_id)
            return
        if broker_selected():
            from tinyassets.broker.ledger_queries import granted_resource_row

            try:
                row = granted_resource_row(self.base, principal=scope[0],
                                           command_center=scope[1], grant_id=grant_id)
                if row["connection_id"] != connection_id:
                    raise ValueError("source connection changed")
            except (RuntimeError, PermissionError, ValueError, KeyError) as exc:
                raise ProviderAuthorityHeldError("source grant unavailable") from exc
            return
        uri = (self.base / "outbound.db").as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as ledger:
            grant = ledger.execute(
                "SELECT 1 FROM outbound_connection_grants g "
                "JOIN outbound_connections c ON g.connection_id=c.connection_id "
                "WHERE g.grant_id=? AND g.connection_id=? AND g.owner_user_id=? "
                "AND c.owner_user_id=? AND g.universe_id=? "
                "AND g.revoked_at IS NULL AND c.revoked_at IS NULL",
                (grant_id, connection_id, scope[0], scope[0], scope[1]),
            ).fetchone()
        if grant is None:
            raise ValueError("source grant unavailable")

    def issue_reference(self, scope, ordinal, *, grant_id, connection_id, verb, request,
                        operation_id):
        if self._remote:
            result = self._rpc(scope, "issue_reference", ordinal=ordinal, grant_id=grant_id,
                               connection_id=connection_id, verb=verb, request=request,
                               operation_id=operation_id)
            return InferenceUsageReference.from_document(result["reference"])
        from tinyassets.broker.ops import canonical_op_id
        from tinyassets.request_budget import RequestBudgetExceeded

        canonical_op_id(operation_id)
        reference = secrets.token_hex(32)
        error = None
        with self._connection(write=True) as conn:
            budget = self._load(conn, scope)
            try:
                if budget._closed:
                    raise RequestBudgetExceeded("dispatch_closed", budget.receipt())
                if type(ordinal) is not int or not 1 <= ordinal <= len(budget._attempts):
                    raise ProviderAuthorityHeldError("inference usage reservation is unavailable")
                if budget._attempts[ordinal - 1].state != "reserved":
                    raise ProviderAuthorityHeldError("inference usage reservation already consumed")
                self._validate_source(scope, budget._attempts[ordinal - 1],
                                      grant_id, connection_id, verb, request)
                conn.execute(
                    "INSERT INTO agent_request_dispatches "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)",
                    (_reference_hash(reference), *scope, ordinal, ordinal, grant_id, connection_id,
                     request_digest(grant_id, connection_id, verb, request), operation_id),
                )
            except ProviderAuthorityHeldError as exc:
                error = exc
            self._save(conn, scope, budget)
        if error is not None:
            if isinstance(error, RequestBudgetExceeded):
                error.request_receipt["usage_id"] = scope[2]
            raise error
        return InferenceUsageReference(scope[2], reference, operation_id)

    def claim_reference(self, reference, *, owner, universe, usage_id, grant_id, connection_id,
                        verb, request, operation_id):
        key = _reference_hash(reference)
        if not isinstance(operation_id, str) or not operation_id:
            raise ProviderAuthorityHeldError("inference usage requires a broker operation")
        error = None
        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM agent_request_dispatches WHERE reference_hash=?",
                               (key,)).fetchone()
            if row is None or not all((
                row["owner"] == owner, row["universe"] == universe,
                row["usage_id"] == usage_id,
                row["grant_id"] == grant_id, row["connection_id"] == connection_id,
                row["request_digest"] == request_digest(grant_id, connection_id, verb, request),
                row["operation_id"] == operation_id, not row["claimed"], bool(row["active"]),
            )):
                raise ProviderAuthorityHeldError(
                    "inference usage reference does not match dispatch")
            scope = (owner, universe, row["usage_id"])
            budget = self._load(conn, scope)
            if budget._closed:
                from tinyassets.request_budget import RequestBudgetExceeded

                error = RequestBudgetExceeded("parent_closed", budget.receipt())
                error.request_receipt["usage_id"] = scope[2]
            else:
                conn.execute("UPDATE agent_request_dispatches SET claimed=1 WHERE reference_hash=?",
                             (key,))
            self._save(conn, scope, budget)
        if error is not None:
            raise error
        return UsageDispatch(self, scope, key, operation_id)

    def settle_invocation(self, scope, ordinal, outcome):
        """Only pending attempts settle; a first 401 never becomes retry success."""
        if self._remote:
            return self._rpc(scope, "settle_invocation", ordinal=ordinal, outcome=outcome)["value"]
        with self._connection(write=True) as conn:
            budget = self._load(conn, scope)
            row = conn.execute(
                f"SELECT * FROM agent_request_dispatches WHERE {_SCOPE} AND first_ordinal=?",
                (*scope, ordinal),
            ).fetchone()
            ordinals = [ordinal]
            if row is not None:
                # Retry ordinals need not be adjacent: concurrent helpers share
                # this root. Membership is recorded on the private ticket.
                ordinals = [row["first_ordinal"], row["last_ordinal"]]
                conn.execute("UPDATE agent_request_dispatches SET active=0 WHERE reference_hash=?",
                             (row["reference_hash"],))
            for item in set(ordinals):
                attempt = budget._attempts[item - 1]
                if attempt.state == "reserved":
                    budget.settle(item, "not_sent")
                elif attempt.state == "dispatched":
                    budget.settle(item, outcome)
            self._save(conn, scope, budget)
            return sum(budget._attempts[item - 1].dispatched_at is not None
                       for item in set(ordinals))


class UsageDispatch:
    """Broker-local, resolved from a one-use server record, never deserialized code."""

    def __init__(self, store, scope, key, operation_id):
        self.store, self.scope, self.key, self.operation_id = store, scope, key, operation_id

    def _change(self, action, *, outcome=None):
        from tinyassets.request_budget import RequestBudgetExceeded

        error = None
        with self.store._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM agent_request_dispatches WHERE reference_hash=?",
                               (self.key,)).fetchone()
            if (row is None
                    or tuple(row[n] for n in ("owner", "universe", "usage_id")) != self.scope
                    or row["operation_id"] != self.operation_id):
                raise ProviderAuthorityHeldError("inference usage operation changed")
            budget = self.store._load(conn, self.scope)
            ordinal = row["last_ordinal"]
            try:
                if (action in {"check", "dispatch", "retry"}
                        and (not row["active"] or budget._closed)):
                    raise RequestBudgetExceeded("dispatch_closed", budget.receipt())
                if action == "check":
                    if budget.deadline is not None and budget.clock() >= budget.deadline:
                        raise RequestBudgetExceeded("dispatch_deadline", budget.receipt())
                elif action == "dispatch":
                    budget.dispatched(ordinal)
                elif action == "retry":
                    if row["last_ordinal"] != row["first_ordinal"]:
                        raise ProviderAuthorityHeldError("inference OAuth resend already reserved")
                    first = budget._attempts[row["first_ordinal"] - 1]
                    if first.state != "failed":
                        raise ProviderAuthorityHeldError("inference resend lacks a failed attempt")
                    ordinal = budget.reserve(owner=self.scope[0], universe=self.scope[1],
                                             source_ref=first.source_ref, model=first.model,
                                             free=first.free, purpose=first.purpose)
                    conn.execute("UPDATE agent_request_dispatches SET last_ordinal=? "
                                 "WHERE reference_hash=?", (ordinal, self.key))
                elif action == "settle":
                    state = budget._attempts[ordinal - 1].state
                    if state == "reserved" and outcome == "not_sent":
                        budget.settle(ordinal, outcome)
                    elif state == "dispatched" and outcome != "not_sent":
                        budget.settle(ordinal, outcome)
            except RequestBudgetExceeded as exc:
                error = exc
            self.store._save(conn, self.scope, budget)
        if error is not None:
            error.request_receipt["usage_id"] = self.scope[2]
            raise error

    def check(self):
        self._change("check")

    def dispatched(self):
        self._change("dispatch")

    def reserve_retry(self):
        self._change("retry")

    def settle(self, outcome):
        self._change("settle", outcome=outcome)

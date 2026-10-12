"""One structured event format for a person's session trail.

Every line is a single JSON object on stdout (``tinyassets.events``). The
droplet already forwards that stdout through Vector into the host journal, so
this module adds no second log pipeline. A later funnel count or an error
digest reads the same objects; nothing here aggregates them.

The unit is the session, not the request. ``session_id`` is stable for one
journey. ``trace_id`` is one HTTP request inside it. ``links`` joins a browser
cookie, an MCP session, an OAuth state, and a checkout attempt once those
identifiers show up together. The read-side timeline walks those links.

No token, email, or other personal value is written. Account ids are opaque
and are dropped when they do not look opaque. Stack text is passed through
:func:`redact` before it is serialized.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

SCHEMA = "tinyassets.event.v1"
SESSION_COOKIE = "ta_session"
SESSION_HEADER = "x-session-id"
TRACE_HEADER = "x-trace-id"
MCP_SESSION_HEADER = "mcp-session-id"

_SESSION_RE = re.compile(r"^s_[0-9a-f]{32}$")
_TRACE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_OPAQUE_ACCOUNT = re.compile(r"^[A-Za-z0-9_|.-]{1,128}$")
_MCP_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_SLUG = re.compile(r"^[a-z0-9._-]{1,40}$")
_TOOL = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_CLIENT_NAME = re.compile(r"[^A-Za-z0-9_. -]+")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_SECRET_PREFIX = re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}\b")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_+/=-]{80,}\b")
_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s#]*")
_PAIR = re.compile(
    r"(?i)\b(code|token|access_token|refresh_token|id_token|state|"
    r"client_secret|password|authorization)=([^\s&]+)"
)

_SQL_VERBS = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE", "BEGIN", "COMMIT", "ROLLBACK",
    "PRAGMA", "CREATE", "ALTER", "DROP", "REPLACE", "WITH", "SAVEPOINT",
    "RELEASE", "SQL",
})
_BODY_PATHS = frozenset({"/mcp", "/mcp/", "/oauth/token", "/app/token"})
_BODY_CAP = 65_536

_MISSING = object()
_request_active: ContextVar[bool] = ContextVar("ta_obs_active", default=False)
_session_id: ContextVar[str | None] = ContextVar("ta_obs_session", default=None)
_trace_id: ContextVar[str | None] = ContextVar("ta_obs_trace", default=None)
_actor: ContextVar[dict[str, Any] | None] = ContextVar("ta_obs_actor", default=None)
_token_facts: ContextVar[dict[str, Any] | None] = ContextVar("ta_obs_token", default=None)
_client: ContextVar[str] = ContextVar("ta_obs_client", default="other")
_stashed: ContextVar[dict[str, Any] | None] = ContextVar("ta_obs_stashed", default=None)
_db_depth: ContextVar[int] = ContextVar("ta_obs_db_depth", default=0)

_configured = False
_commit_cache: str | None = None
_original_connect: Callable[..., Any] | None = None
_clients: dict[str, str] = {}
_client_order: deque[str] = deque()
_client_lock = threading.Lock()
_report_buckets: OrderedDict[str, tuple[float, int]] = OrderedDict()
_report_lock = threading.Lock()

_log = logging.getLogger("tinyassets.events")


def configure() -> None:
    """Attach a stdout handler that writes the message unchanged.

    Idempotent. The logger does not propagate, so the process's text formatter
    cannot wrap the JSON in a second shape.
    """
    global _configured
    if _configured:
        return
    _log.setLevel(logging.INFO)
    _log.propagate = False
    if not _log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _log.addHandler(handler)
    _configured = True


def reset_commit_cache() -> None:
    """Test hook. Production caches the binary sha for the life of the process."""
    global _commit_cache
    _commit_cache = None


def live_commit() -> str:
    """The commit this process is running, or ``unknown``.

    The image bakes ``TINYASSETS_BUILD_SHA``. The deploy receipt is only a
    fallback for a process whose image did not record one; the receipt can
    move during a swap, so a baked sha wins.
    """
    global _commit_cache
    if _commit_cache:
        return _commit_cache
    sha = _sha_from_env() or _sha_from_receipt() or "unknown"
    if sha != "unknown":
        _commit_cache = sha
    return sha


def _sha_from_env() -> str:
    raw = os.environ.get("TINYASSETS_BUILD_SHA", "").strip().lower()
    return raw if _HEX40.fullmatch(raw) else ""


def _sha_from_receipt() -> str:
    override = os.environ.get("TINYASSETS_RELEASE_STATE_PATH", "").strip()
    if override:
        path = Path(override)
    else:
        root = os.environ.get("TINYASSETS_DATA_DIR", "").strip()
        if not root:
            return ""
        path = Path(root) / "release-state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    sha = str(payload.get("git_sha") or "").strip().lower()
    return sha if _HEX40.fullmatch(sha) else ""


def redact(text: str) -> str:
    """Remove tokens, emails, and credential-shaped text from one string."""
    if not text:
        return ""
    cleaned = _QUERY.sub(r"\1", text)
    cleaned = _PAIR.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)
    cleaned = _EMAIL.sub("[redacted]", cleaned)
    cleaned = _BEARER.sub(lambda match: f"{match.group(1)} [redacted]", cleaned)
    cleaned = _JWT.sub("[redacted]", cleaned)
    cleaned = _SECRET_PREFIX.sub("[redacted]", cleaned)
    cleaned = _LONG_TOKEN.sub("[redacted]", cleaned)
    return cleaned


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return None
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        out = {}
        for key, item in list(value.items())[:40]:
            if isinstance(key, str) and _SLUG.fullmatch(key.replace("_", "-")) or (
                isinstance(key, str) and re.fullmatch(r"[a-z0-9_]{1,40}", key)
            ):
                out[key] = _jsonable(item, depth=depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [_jsonable(item, depth=depth + 1) for item in list(value)[:40]]
    return None


def link_token(prefix: str, value: str) -> str:
    """A stable join key that is not the original identifier."""
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]
    return f"{prefix}{digest}"


def emit(event: str, **fields: Any) -> None:
    """Write one trail event. Request context fills any field the caller omits."""
    configure()
    session_id = fields.pop("session_id", _MISSING)
    trace_id = fields.pop("trace_id", _MISSING)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "ts": _now(),
        "event": event,
        "session_id": _session_id.get() if session_id is _MISSING else session_id,
        "trace_id": _trace_id.get() if trace_id is _MISSING else trace_id,
        "commit": live_commit(),
    }
    if event not in {"process.startup", "process.crash"} or _request_active.get():
        snapshot = _context_snapshot()
        if snapshot is not None and "context" not in fields:
            record["context"] = snapshot
    for key, value in fields.items():
        if value is None:
            continue
        record[key] = value
    _log.info(json.dumps(_jsonable(record), separators=(",", ":"), sort_keys=True))


def _now() -> str:
    clock = time.time()
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(clock))
    return f"{base}.{int((clock % 1) * 1_000_000):06d}Z"


def _context_snapshot() -> dict[str, Any] | None:
    if not _request_active.get():
        return None
    actor = _actor.get() or {}
    facts = _token_facts.get() or {}
    return {
        "client": _client.get() or "other",
        "account_id": actor.get("account_id"),
        "signed_in": bool(actor.get("signed_in")),
        "token_age_s": facts.get("token_age_s"),
        "token_exp": facts.get("token_exp"),
        "token_iss": facts.get("token_iss"),
    }


def observe_bearer(token: str) -> None:
    """Record issuer and lifetime of a presented bearer. Never the bearer."""
    if not _request_active.get() or not token:
        return
    _token_facts.set(_peek_token(token))


def observe_identity(identity: object) -> None:
    """Record whether a request resolved a principal, and the opaque account id."""
    if not _request_active.get():
        return
    user_id = getattr(identity, "user_id", None) if identity is not None else None
    signed_in = isinstance(user_id, str) and bool(user_id.strip())
    account = user_id if isinstance(user_id, str) and _OPAQUE_ACCOUNT.fullmatch(user_id) else None
    if isinstance(account, str) and "@" in account:
        account = None
    _actor.set({"signed_in": signed_in, "account_id": account})


def _peek_token(token: str) -> dict[str, Any]:
    """Issuer, issued-at, and expiry only, from the JWT payload."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    facts: dict[str, Any] = {}
    issuer = payload.get("iss")
    if isinstance(issuer, str) and _safe_issuer(issuer):
        facts["token_iss"] = issuer
    now = int(time.time())
    issued = _unix(payload.get("iat"))
    if issued is not None:
        age = now - issued
        if -300 <= age <= 86400 * 30:
            facts["token_age_s"] = age
    expires = _unix(payload.get("exp"))
    if expires is not None:
        facts["token_exp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires))
    return facts


def _unix(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = int(value)
    if number < 1_000_000_000 or number > 10_000_000_000:
        return None
    return number


def _safe_issuer(issuer: str) -> bool:
    if len(issuer) > 200 or "@" in issuer or "?" in issuer or "#" in issuer:
        return False
    parts = urlsplit(issuer)
    return parts.scheme == "https" and bool(parts.hostname) and not parts.username


def emit_startup() -> None:
    emit("process.startup", session_id=None, trace_id=None)


def emit_crash(exc: BaseException) -> None:
    emit("process.crash", error={"type": type(exc).__name__, "stack": _stack(exc)})


def emit_checkout_attempt(attempt_id: str) -> None:
    """Join this request's session to a checkout attempt without logging the id."""
    if not attempt_id or not _request_active.get():
        return
    emit(
        "session.link",
        step="checkout.open",
        links={"checkout": link_token("c_", attempt_id)},
    )


def emit_billing_event(event: Mapping[str, Any]) -> None:
    """A verified Stripe event, as a step on the webhook request's trail."""
    if not _request_active.get():
        return
    kind = str(event.get("type") or "")
    step = {
        "checkout.session.completed": "checkout.paid",
        "checkout.session.expired": "checkout.expired",
        "customer.subscription.deleted": "checkout.lapsed",
    }.get(kind)
    if step is None:
        return
    links: dict[str, str] = {}
    data = event.get("data")
    obj = data.get("object") if isinstance(data, Mapping) else None
    meta = obj.get("metadata") if isinstance(obj, Mapping) else None
    attempt = ""
    if isinstance(meta, Mapping):
        raw = meta.get("tinyassets_attempt_id")
        attempt = raw if isinstance(raw, str) else ""
    if attempt:
        links["checkout"] = link_token("c_", attempt)
    emit("session.step", step=step, links=links or None)


class Span:
    def __init__(self) -> None:
        self.status = 0
        self.error = ""


@contextmanager
def dependency(kind: str, target: str) -> Iterator[Span]:
    """Time one outbound call and write a ``dependency.call`` event."""
    span = Span()
    started = time.perf_counter()
    try:
        yield span
    except BaseException as exc:
        if not span.error:
            span.error = type(exc).__name__
        raise
    finally:
        failed = bool(span.error)
        if kind == "db" and not _request_active.get() and not failed:
            return
        if _db_depth.get() and kind == "db":
            return
        safe_kind = kind if kind in {"workos", "stripe", "db"} else "other"
        emit(
            "dependency.call",
            dependency={
                "kind": safe_kind,
                "target": _dependency_target(safe_kind, target),
                "status": int(span.status or 0),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "error": span.error or None,
            },
        )


def _dependency_target(kind: str, target: str) -> str:
    if kind == "stripe":
        segment = target.strip("/").split("/", 1)[0]
        if not re.fullmatch(r"[a-z_]{1,40}", segment):
            segment = "request"
        return f"stripe:{segment}"
    if kind == "db":
        return target if target.startswith("sqlite:") else f"sqlite:{_db_label(target)}"
    parts = urlsplit(target)
    if parts.scheme in {"http", "https"} and parts.hostname:
        path = parts.path or "/"
        return urlunsplit((parts.scheme, parts.hostname, path, "", ""))
    return redact(target)[:120]


def _db_label(database: object) -> str:
    name = Path(str(database)).name or "sqlite"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:40]
    return name or "sqlite"


def _stack(exc: BaseException) -> str:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return redact(text)[:8000]


def install_crash_hooks() -> None:
    """Report an uncaught exception as ``process.crash``, then the previous hook."""
    if getattr(install_crash_hooks, "_done", False):
        return
    install_crash_hooks._done = True  # type: ignore[attr-defined]
    previous = sys.excepthook

    def _hook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
        try:
            if isinstance(exc, BaseException):
                emit_crash(exc)
        except Exception:
            pass
        previous(exc_type, exc, tb)

    sys.excepthook = _hook
    previous_thread = threading.excepthook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        try:
            if isinstance(args.exc_value, BaseException):
                emit_crash(args.exc_value)
        except Exception:
            pass
        previous_thread(args)

    threading.excepthook = _thread_hook


class _TracingConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        database = args[0] if args else kwargs.get("database", "")
        self._ta_label = f"sqlite:{_db_label(database)}"  # type: ignore[attr-defined]

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        def run() -> sqlite3.Cursor:
            return sqlite3.Connection.execute(self, sql, parameters)
        return _traced(self._ta_label, sql, run)

    def executemany(self, sql: str, seq: Any) -> sqlite3.Cursor:
        return _traced(self._ta_label, sql, lambda: sqlite3.Connection.executemany(self, sql, seq))

    def executescript(self, sql: str) -> sqlite3.Cursor:
        return _traced(self._ta_label, "SQL", lambda: sqlite3.Connection.executescript(self, sql))


def _sql_verb(sql: str) -> str:
    word = (sql or "").lstrip().split(None, 1)
    verb = word[0].upper() if word else "SQL"
    return verb if verb in _SQL_VERBS else "SQL"


def install_db_trace() -> None:
    """Time sqlite statements that happen inside a request. Failures always log."""
    global _original_connect
    if getattr(sqlite3, "_ta_obs", False):
        return
    _original_connect = sqlite3.connect

    def connect(database: str, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs.setdefault("factory", _TracingConnection)
        assert _original_connect is not None
        return _original_connect(database, *args, **kwargs)

    sqlite3.connect = connect  # type: ignore[assignment]
    sqlite3._ta_obs = True  # type: ignore[attr-defined]


def uninstall_db_trace() -> None:
    """Test hook. Production leaves the tracer installed."""
    global _original_connect
    if _original_connect is not None:
        sqlite3.connect = _original_connect  # type: ignore[assignment]
        _original_connect = None
    if hasattr(sqlite3, "_ta_obs"):
        delattr(sqlite3, "_ta_obs")


def install_serving() -> None:
    """Arm crash hooks, the sqlite tracer, and the JSON logger. Idempotent."""
    configure()
    install_crash_hooks()
    install_db_trace()


def _traced(label: str, sql: str, fn: Callable[[], Any]) -> Any:
    if _db_depth.get():
        return fn()
    token = _db_depth.set(_db_depth.get() + 1)
    started = time.perf_counter()
    error = ""
    try:
        return fn()
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        _db_depth.reset(token)
        if not error and not _request_active.get():
            return
        try:
            emit(
                "dependency.call",
                dependency={
                    "kind": "db",
                    "target": label if str(label).startswith("sqlite:") else f"sqlite:{label}",
                    "status": 0 if error else 1,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error": error or None,
                    "operation": _sql_verb(sql),
                },
            )
        except Exception:
            return


class StashExceptionMiddleware:
    """Remember an exception the server later turns into an HTTP response."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        try:
            await self.app(scope, receive, send)
        except BaseException as exc:
            holder = _stashed.get()
            if holder is not None:
                holder["type"] = type(exc).__name__
                holder["stack"] = _stack(exc)
            raise


class RequestLogMiddleware:
    """Outermost ASGI wrapper. Records the trail and sets ``X-Trace-Id``."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        trace = _incoming_trace(scope) or uuid.uuid4().hex
        session, minted = _incoming_session(scope)
        links = _incoming_links(scope, session)
        body = b""
        if _should_buffer(scope):
            body, receive = await _buffer_small(receive)
        step, tool, client_name = classify_step(scope, body)
        client = _remember_client(session, links.get("mcp_session", ""), client_name, scope)
        holder: dict[str, Any] = {}
        tokens = (
            _request_active.set(True),
            _session_id.set(session),
            _trace_id.set(trace),
            _actor.set({"signed_in": False, "account_id": None}),
            _token_facts.set({}),
            _client.set(client),
            _stashed.set(holder),
        )
        started = time.perf_counter()
        status = 500
        caught: BaseException | None = None

        async def send_wrapper(message: dict) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status") or 500)
                headers = [
                    (key, value)
                    for key, value in message.get("headers") or []
                    if key.lower() != b"x-trace-id"
                ]
                headers.append((b"x-trace-id", trace.encode("ascii")))
                if minted:
                    headers.append((b"set-cookie", _session_cookie(session, scope)))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException as exc:
            caught = exc
            raise
        finally:
            try:
                error = None
                if caught is not None:
                    error = {"type": type(caught).__name__, "stack": _stack(caught)}
                elif holder.get("type"):
                    error = {"type": holder["type"], "stack": holder.get("stack") or ""}
                elif status >= 500:
                    error = {"type": f"HTTP{status}", "stack": ""}
                fields: dict[str, Any] = {
                    "step": step,
                    "http": {
                        "method": str(scope.get("method") or ""),
                        "route": route_for(str(scope.get("path") or "/")),
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        "user_agent": _user_agent(scope),
                    },
                }
                if tool:
                    fields["tool"] = tool
                if links:
                    fields["links"] = links
                if error:
                    fields["error"] = error
                emit("session.step", **fields)
            except Exception:
                pass
            _reset(tokens)


def _reset(tokens: tuple[Any, ...]) -> None:
    variables = (
        _request_active, _session_id, _trace_id, _actor, _token_facts, _client, _stashed,
    )
    for variable, token in zip(variables, tokens, strict=True):
        variable.reset(token)


def _header(scope: Mapping[str, Any], name: str) -> str:
    wanted = name.lower().encode("ascii")
    for key, value in scope.get("headers") or []:
        if key.lower() == wanted:
            try:
                return value.decode("latin-1")
            except Exception:
                return ""
    return ""


def _incoming_trace(scope: Mapping[str, Any]) -> str:
    raw = _header(scope, TRACE_HEADER).strip()
    return raw if _TRACE_RE.fullmatch(raw) else ""


def _incoming_session(scope: Mapping[str, Any]) -> tuple[str, bool]:
    cookie = _cookie(scope, SESSION_COOKIE)
    if _SESSION_RE.fullmatch(cookie):
        return cookie, False
    header = _header(scope, SESSION_HEADER).strip()
    if _SESSION_RE.fullmatch(header):
        return header, False
    mcp = _header(scope, MCP_SESSION_HEADER).strip()
    if _MCP_SESSION_RE.fullmatch(mcp) and mcp.count(".") < 2:
        return mcp, False
    return "s_" + uuid.uuid4().hex, True


def _cookie(scope: Mapping[str, Any], name: str) -> str:
    raw = _header(scope, "cookie")
    prefix = name + "="
    for part in raw.split(";"):
        item = part.strip()
        if item.startswith(prefix):
            return item[len(prefix):]
    return ""


def _incoming_links(scope: Mapping[str, Any], session: str) -> dict[str, str]:
    links: dict[str, str] = {}
    cookie = _cookie(scope, SESSION_COOKIE)
    if _SESSION_RE.fullmatch(cookie):
        links["cookie"] = cookie
    header = _header(scope, SESSION_HEADER).strip()
    if _SESSION_RE.fullmatch(header) and header != session:
        links["header_session"] = header
    mcp = _header(scope, MCP_SESSION_HEADER).strip()
    if _MCP_SESSION_RE.fullmatch(mcp) and mcp.count(".") < 2:
        links["mcp_session"] = mcp
    state = _oauth_state(scope)
    if state:
        links["oauth_state"] = link_token("o_", state)
    return links


def _oauth_state(scope: Mapping[str, Any]) -> str:
    raw = scope.get("query_string") or b""
    if isinstance(raw, str):
        raw = raw.encode("latin-1", "replace")
    try:
        values = parse_qs(
            raw.decode("latin-1", "replace"), max_num_fields=20, keep_blank_values=False,
        )
    except ValueError:
        return ""
    state = values.get("state") or []
    if not state:
        return ""
    text = state[0]
    if not text or len(text) > 512:
        return ""
    return text


def _session_cookie(session: str, scope: Mapping[str, Any]) -> bytes:
    secure = ""
    scheme = str(scope.get("scheme") or "")
    forwarded = _header(scope, "x-forwarded-proto").split(",")[0].strip().lower()
    if scheme == "https" or forwarded == "https":
        secure = "; Secure"
    value = (
        f"{SESSION_COOKIE}={session}; Path=/; Max-Age=2592000; SameSite=Lax{secure}"
    )
    return value.encode("ascii")


def _user_agent(scope: Mapping[str, Any]) -> str:
    return redact(_header(scope, "user-agent"))[:160]


def _should_buffer(scope: Mapping[str, Any]) -> bool:
    if str(scope.get("method") or "").upper() not in {"POST", "PUT", "PATCH"}:
        return False
    if str(scope.get("path") or "") not in _BODY_PATHS:
        return False
    length = _header(scope, "content-length").strip()
    return bool(length) and length.isdigit() and int(length) <= _BODY_CAP


async def _buffer_small(receive: Any) -> tuple[bytes, Any]:
    messages: list[dict] = []
    chunks: list[bytes] = []
    total = 0
    overflow = False
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            messages.append(message)
            break
        piece = message.get("body") or b""
        total += len(piece)
        messages.append(message)
        if total > _BODY_CAP:
            overflow = True
            break
        chunks.append(piece)
        if not message.get("more_body"):
            break
    payload = b"" if overflow else b"".join(chunks)
    queue = deque(messages)

    async def replay() -> dict:
        if queue:
            return queue.popleft()
        if overflow:
            return await receive()
        return {"type": "http.disconnect"}

    return payload, replay


def classify_step(scope: Mapping[str, Any], body: bytes) -> tuple[str, str, str]:
    """Return ``(step, tool name, client name)`` for this request."""
    method = str(scope.get("method") or "").upper()
    path = str(scope.get("path") or "")
    normalized = path.rstrip("/") or "/"
    if normalized.endswith("/.well-known/oauth-protected-resource") or (
        normalized == "/.well-known/oauth-protected-resource"
    ):
        return "oauth.protected_resource", "", ""
    if normalized.endswith("/.well-known/oauth-authorization-server"):
        return "oauth.authorization_server", "", ""
    if normalized in {"/oauth/authorize", "/app/owner-sign-in"} or normalized.endswith(
        "/authorize",
    ):
        return "oauth.authorize", "", ""
    if "callback" in normalized.split("/"):
        return "oauth.callback", "", ""
    if normalized in {"/oauth/token", "/app/token"}:
        if b"refresh_token" in body[:4000]:
            return "oauth.refresh", "", ""
        return "oauth.token", "", ""
    if normalized == "/app/billing/checkout":
        return "checkout.open", "", ""
    if normalized == "/app/billing/cancel":
        return "checkout.cancel", "", ""
    if normalized == "/app/billing/webhook":
        return "checkout.webhook", "", ""
    if normalized == "/app/client-error":
        return "client.report", "", ""
    if method == "OPTIONS":
        return "http.preflight", "", ""
    if normalized in {"/mcp", "/mcp/"} and method == "POST":
        return _mcp_step(body)
    if method in {"GET", "HEAD"} and normalized in {"/", "/app", "/mcp"}:
        accept = _header(scope, "accept").lower()
        if "text/event-stream" in accept or _header(scope, MCP_SESSION_HEADER):
            return "mcp.listen", "", ""
        return "page.load", "", ""
    return "http", "", ""


def _mcp_step(body: bytes) -> tuple[str, str, str]:
    try:
        data = json.loads(body[:_BODY_CAP] or b"{}")
    except (ValueError, UnicodeError):
        return "mcp.other", "", ""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return "mcp.other", "", ""
    name = str(data.get("method") or "")
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
    client = ""
    raw_client = info.get("name")
    if isinstance(raw_client, str):
        client = _CLIENT_NAME.sub("", raw_client).strip()[:40]
    if name == "initialize":
        return "mcp.initialize", "", client
    if name == "notifications/initialized":
        return "mcp.initialized", "", client
    if name == "tools/list":
        return "mcp.tools_list", "", client
    if name == "tools/call":
        tool = params.get("name")
        tool_name = tool if isinstance(tool, str) and _TOOL.fullmatch(tool) else ""
        return "mcp.tool_call", tool_name, client
    if name == "ping":
        return "mcp.ping", "", client
    return "mcp.other", "", client


def route_for(path: str) -> str:
    """The path with secret-shaped segments replaced."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    parts = path.split("/")
    out: list[str] = []
    for index, part in enumerate(parts):
        previous = parts[index - 1] if index else ""
        if previous == "hooks" and part:
            out.append("{token}")
        elif part and not _SLUG.fullmatch(part):
            out.append("{id}")
        else:
            out.append(part)
    rendered = "/".join(out)
    return rendered or "/"


def client_type(user_agent: str, client_name: str = "") -> str:
    """Claude, ChatGPT, a browser, or something else. From names, not accounts."""
    blob = f"{client_name} {user_agent}".lower()
    if "claude" in blob or "anthropic" in blob:
        return "claude"
    if "chatgpt" in blob or "openai" in blob:
        return "chatgpt"
    if any(token in blob for token in ("mozilla", "chrome", "safari", "firefox", "edg/")):
        return "browser"
    return "other"


def _remember_client(
    session: str, mcp_session: str, client_name: str, scope: Mapping[str, Any],
) -> str:
    kind = client_type(_header(scope, "user-agent"), client_name)
    keys = [key for key in (session, mcp_session) if key]
    with _client_lock:
        if kind == "other":
            for key in keys:
                remembered = _clients.get(key)
                if remembered:
                    kind = remembered
                    break
        elif client_name or kind in {"claude", "chatgpt"}:
            for key in keys:
                _clients[key] = kind
                _client_order.append(key)
            while len(_client_order) > 1024:
                _clients.pop(_client_order.popleft(), None)
    return kind


def _client_report_allowed(session: str) -> bool:
    now = time.monotonic()
    with _report_lock:
        start, count = _report_buckets.get(session, (now, 0))
        if now - start > 60:
            start, count = now, 0
        if count >= 30:
            _report_buckets[session] = (start, count)
            _report_buckets.move_to_end(session)
            return False
        _report_buckets[session] = (start, count + 1)
        _report_buckets.move_to_end(session)
        while len(_report_buckets) > 5000:
            _report_buckets.popitem(last=False)
    return True


async def client_error(request: Any) -> Any:
    """Browser reports: uncaught errors and page loads, on the same trail.

    Public on purpose. The marketing site has no bearer. The body is small,
    the rate limit is per session, and every string is redacted before it is
    written. This does not echo the report back.
    """
    from starlette.responses import Response

    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > 4096:
                return Response(status_code=413)
        except ValueError:
            return Response(status_code=400)
    raw = await request.body()
    if len(raw) > 4096:
        return Response(status_code=413)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return Response(status_code=400)
    if not isinstance(data, dict):
        return Response(status_code=400)
    kind = str(data.get("kind") or "")
    if kind not in {"page.load", "window.error", "unhandledrejection", "react"}:
        return Response(status_code=400)
    session = str(data.get("session_id") or "")
    if not _SESSION_RE.fullmatch(session):
        session = _session_id.get() or ""
    if not _SESSION_RE.fullmatch(session):
        return Response(status_code=400)
    if not _client_report_allowed(session):
        return Response(status_code=429)
    page = str(data.get("path") or "/")
    if not page.startswith("/") or "?" in page or "\\" in page or len(page) > 200:
        page = "/"
    page = route_for(page)
    current = _session_id.get() or ""
    links = {"cookie": current} if _SESSION_RE.fullmatch(current) and current != session else None
    browser = {
        "client": "browser", "account_id": None, "signed_in": False,
        "token_age_s": None, "token_exp": None, "token_iss": None,
    }
    if kind == "page.load":
        emit(
            "session.step",
            session_id=session,
            step="page.load",
            page=page,
            links=links,
            context=browser,
        )
    else:
        name = redact(str(data.get("name") or "Error"))[:80]
        text = redact(str(data.get("text") or ""))[:500]
        frames = redact(str(data.get("frames") or ""))[:4000]
        emit(
            "client.error",
            session_id=session,
            step="client.error",
            page=page,
            links=links,
            error={"type": name or "Error", "message": text, "stack": frames},
            context=browser,
        )
    return Response(status_code=204)

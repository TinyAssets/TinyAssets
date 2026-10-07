"""Trusted end of credential-free, turn-bound reverse box RPC.

Only the bound execution supplies requests. There is no network listener and
no caller-selected authority. Intent is durable before any capability runs.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from tinyassets import owner_lease
from tinyassets.engine_tool_client import EngineToolError
from tinyassets.storage.owner_fence import check_fence
from tinyassets.ta_capabilities import MAX_REQUEST, MAX_RESPONSE

PREFIX = b"\x1eTA1 "
UNKNOWN = {"error": "ta outcome unknown; do not retry with a new request id"}
_ID = re.compile(r"[a-f0-9]{32}\Z")

async def engine_ta(engine, message, mounts=()):
    """Reuse local ta's grants, connection custody, owner gates and review.

    The box message rides INSIDE the trusted envelope, so a box can never claim
    a delivery; ``mounts`` are only revisions this host verified and delivered.
    """
    return await engine.call_ta({"ta": message, "mounts": sorted(map(list, mounts))})


async def engine_deliver(engine):
    """Fetch this launch's active extension revisions on the same signed session."""
    return await engine.call_ta({"deliver": "extensions"})


def _mounts(value):
    if not isinstance(value, list):
        raise ValueError
    found = set()
    for item in value:
        if (not isinstance(item, list) or len(item) != 3
                or not all(isinstance(x, str) for x in item[:2])
                or type(item[2]) is not int):
            raise ValueError
        found.add(tuple(item))
    return found


async def engine_resource(server, payload):
    """Private MCP resource: no shell, argv limits, or model-facing tool schema."""
    from tinyassets.extension_capabilities import ExtensionCapabilities, delivered
    from tinyassets.ta_capabilities import engine_dispatch

    if len(payload) > 4 * ((MAX_REQUEST + 2) // 3):
        return json.dumps({"error": "ta request too large"})
    try:
        raw = base64.b64decode(payload, altchars=b"-_", validate=True)
        if len(raw) > MAX_REQUEST:
            raise ValueError
        envelope = json.loads(raw)
        if envelope == {"deliver": "extensions"}:
            message, mounts = None, set()
        elif isinstance(envelope, dict) and set(envelope) == {"ta", "mounts"}:
            message, mounts = envelope["ta"], _mounts(envelope["mounts"])
        else:
            raise ValueError
    except (ValueError, TypeError):
        return json.dumps({"error": "invalid ta request"})
    dispatch = await engine_dispatch(server)
    if dispatch is None:
        return json.dumps({"error": "ta authority unavailable"})
    if message is None:
        answer = await asyncio.to_thread(ExtensionCapabilities(dispatch.extension_backend).bundle)
    else:
        # Dispatch re-checks every revision against current active state.
        with delivered(mounts):
            answer = await asyncio.to_thread(dispatch, message)
    encoded = json.dumps(answer)
    if len(encoded.encode()) > MAX_RESPONSE:
        return json.dumps({"error": "ta response too large; request a smaller page"})
    return encoded


def verified_bundle(answer):
    """Content-addressed check on the trusted host before any byte enters a box."""
    from tinyassets.extension_manifest import Revision

    if not isinstance(answer, dict) or set(answer) != {"extensions", "undelivered"}:
        raise ValueError("extension delivery unavailable")
    extensions, mounts = [], set()
    for item in answer["extensions"]:
        if set(item) != {"name", "revision", "generation", "blob"}:
            raise ValueError("invalid extension delivery")
        blob = base64.b64decode(item["blob"], validate=True)
        if hashlib.sha256(blob).hexdigest() != item["revision"]:
            raise ValueError("extension delivery digest mismatch")
        doc, files = Revision(item["name"], item["revision"], blob).content()
        if doc["name"] != item["name"] or type(item["generation"]) is not int:
            raise ValueError("invalid extension delivery")
        extensions.append({"name": item["name"], "revision": item["revision"],
                           "files": {path: base64.b64encode(data).decode()
                                     for path, data in sorted(files.items())}})
        mounts.add((item["name"], item["revision"], item["generation"]))
    return extensions, frozenset(mounts)


class TurnBridge:
    """One live turn. Receipts survive this object and the platform process."""

    def __init__(self, *, owner, center, turn, handle, database: Path, dispatch,
                 deliver=None):
        if not all(isinstance(x, str) and x for x in (owner, center, turn)):
            raise ValueError("ta requires owner, center and turn")
        expected = (owner, center, turn)
        actual = (getattr(handle, "account_id", None),
                  getattr(handle, "command_center_id", None),
                  getattr(handle, "turn_id", None))
        if actual != expected:
            raise EngineToolError("remote_ta_binding_refused")
        self.handle = handle
        self.scope = json.dumps(expected)
        self.database = database
        self.dispatch = dispatch
        self.deliver = deliver
        self._mounts = {}
        self.loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._active = True
        self._pending = set()
        self._executions = {}
        self._cancelled = set()
        database.parent.mkdir(parents=True, exist_ok=True)
        # Create the file before acquisition advances its cataloged fence.
        sqlite3.connect(database).close()
        owner_lease.register_store(database.parent, database, "remote_ta_receipts")
        self._lease = owner_lease.acquire(database.parent, owner_lease.key_for(center))
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS ta_receipts ("
                       "scope TEXT, execution TEXT, request TEXT, digest TEXT NOT NULL,"
                       "answer TEXT, PRIMARY KEY(scope, execution, request))")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.database)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                check_fence(db, self._lease)
                yield db
        finally:
            db.close()

    def close(self):
        with self._lock:
            self._active = False
            for future in self._pending:
                future.cancel()

    def cancel_execution(self, execution):
        with self._lock:
            self._cancelled.add(execution)
            for future in self._executions.get(execution, ()):
                future.cancel()

    async def extensions(self, handle, execution):
        """Verified revision files for one execution; any failure refuses the launch."""
        if self.deliver is None:
            return []
        with self._lock:
            if not self._active or handle != self.handle or execution in self._cancelled:
                raise EngineToolError("remote_ta_binding_refused")
        try:
            extensions, mounts = verified_bundle(await self.deliver())
        except EngineToolError:
            raise
        except Exception:
            # Nothing has started: fail closed, never run against absent bytes.
            raise EngineToolError("remote_extension_delivery_failed") from None
        with self._lock:
            self._mounts[execution] = mounts
        return extensions

    def request(self, handle, execution, request, message):
        """Called on the box collector thread; duplicates never redispatch."""
        if not isinstance(request, str) or not _ID.fullmatch(request):
            return {"error": "invalid ta request id"}
        raw = json.dumps(message, sort_keys=True, separators=(",", ":"))
        if len(raw.encode()) > MAX_REQUEST:
            return {"error": "ta request too large"}
        digest = hashlib.sha256(raw.encode()).hexdigest()
        key = (self.scope, execution, request)
        with self._lock:
            if not self._active or handle != self.handle or execution in self._cancelled:
                return {"error": "ta turn expired or binding refused"}
            with self._transaction() as db:
                row = db.execute("SELECT digest, answer FROM ta_receipts WHERE "
                                 "scope=? AND execution=? AND request=?", key).fetchone()
                if row:
                    if row[0] != digest:
                        return {"error": "ta request id reused with different arguments"}
                    return json.loads(row[1]) if row[1] else dict(UNKNOWN)
                db.execute("INSERT INTO ta_receipts VALUES (?,?,?,?,NULL)", (*key, digest))
            mounts = self._mounts.get(execution)
            call = (self.dispatch(message) if mounts is None
                    else self.dispatch(message, mounts=mounts))
            future = asyncio.run_coroutine_threadsafe(call, self.loop)
            self._pending.add(future)
            self._executions.setdefault(execution, set()).add(future)
        try:
            answer = future.result(timeout=600)
            encoded = json.dumps(answer)
            if len(encoded.encode()) > MAX_RESPONSE:
                answer = {"error": "ta response too large; request a smaller page"}
                encoded = json.dumps(answer)
            with self._transaction() as db:
                db.execute("UPDATE ta_receipts SET answer=? WHERE "
                           "scope=? AND execution=? AND request=?", (encoded, *key))
            return answer
        except Exception:
            future.cancel()
            return dict(UNKNOWN)
        finally:
            with self._lock:
                self._pending.discard(future)
                self._executions[execution].discard(future)


def worker_argv(command, *, root, execution, extensions=()):
    """Only public source, command arguments and verified package bytes cross."""
    from tinyassets.ta_capabilities import CLIENT_SOURCE

    source = Path(__file__).with_name("box_ta_worker.py").read_text(encoding="utf-8")
    bootstrap = json.dumps({"command": command,
                            "client": CLIENT_SOURCE.read_text(encoding="utf-8"),
                            "extensions": list(extensions)}).encode() + b"\n"
    return ["python3", "-c", source], bootstrap

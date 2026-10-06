"""Trusted end of credential-free, turn-bound reverse box RPC.

Only the bound execution supplies requests. There is no network listener and
no caller-selected authority. Intent is durable before any capability runs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import sqlite3
import threading
from pathlib import Path

from tinyassets.engine_tool_client import EngineToolError
from tinyassets.ta_capabilities import MAX_REQUEST, MAX_RESPONSE

PREFIX = b"\x1eTA1 "
UNKNOWN = {"error": "ta outcome unknown; do not retry with a new request id"}
_ID = re.compile(r"[a-f0-9]{32}\Z")

# Fixed code in the existing engine jail, not a model-selected shell command.
# The broker is the exact same socket used by local ta.
_BROKER = '''import json,socket,sys
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.settimeout(600)
s.connect('/tmp/ta.sock')
s.sendall(sys.argv[1].encode()+b'\\n')
r=s.makefile('rb').readline(8388609)
assert len(r)<=8388608 and r.endswith(b'\\n')
print(r.decode(),end='')
'''


async def engine_ta(engine, message):
    """Reuse local ta's grants, connection custody, owner gates and review."""
    command = "python3 -c " + shlex.quote(_BROKER) + " " + shlex.quote(json.dumps(message))
    result = await engine.call("bash", {"command": command, "timeout": 600})
    texts = [block.text for block in result.content if block.type == "text"]
    if result.isError or not texts:
        raise EngineToolError("remote_ta_engine_unknown", outcome="unknown")
    text = texts[0]
    trailer = "[exit code 0]"
    if not text.rstrip().endswith(trailer):
        raise EngineToolError("remote_ta_engine_unknown", outcome="unknown")
    return json.loads(text.rstrip()[:-len(trailer)].strip())


class TurnBridge:
    """One live turn. Receipts survive this object and the platform process."""

    def __init__(self, *, owner, center, turn, handle, database: Path, dispatch):
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
        self.loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._active = True
        self._pending = set()
        self._executions = {}
        self._cancelled = set()
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS ta_receipts ("
                       "scope TEXT, execution TEXT, request TEXT, digest TEXT NOT NULL,"
                       "answer TEXT, PRIMARY KEY(scope, execution, request))")

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
            with sqlite3.connect(self.database) as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT digest, answer FROM ta_receipts WHERE "
                                 "scope=? AND execution=? AND request=?", key).fetchone()
                if row:
                    if row[0] != digest:
                        return {"error": "ta request id reused with different arguments"}
                    return json.loads(row[1]) if row[1] else dict(UNKNOWN)
                db.execute("INSERT INTO ta_receipts VALUES (?,?,?,?,NULL)", (*key, digest))
            future = asyncio.run_coroutine_threadsafe(self.dispatch(message), self.loop)
            self._pending.add(future)
            self._executions.setdefault(execution, set()).add(future)
        try:
            answer = future.result(timeout=600)
            encoded = json.dumps(answer)
            if len(encoded.encode()) > MAX_RESPONSE:
                answer = {"error": "ta response too large; request a smaller page"}
                encoded = json.dumps(answer)
            with sqlite3.connect(self.database) as db:
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


def worker_argv(command, *, root, execution):
    """Only public source and command arguments cross the box boundary."""
    from tinyassets.ta_capabilities import CLIENT_SOURCE

    source = Path(__file__).with_name("box_ta_worker.py").read_text(encoding="utf-8")
    directory = root + "/.ta-" + hashlib.sha256(execution.encode()).hexdigest()
    return ["python3", "-c", source, directory, command,
            CLIENT_SOURCE.read_text(encoding="utf-8")], directory

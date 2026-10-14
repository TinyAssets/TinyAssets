"""Host-owned bounded execution receipts over the one owner's sandbox."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait

from tinyassets.boxes.file_wire import MAX_DATA, decode, encode
from tinyassets.boxes.provider import BoxAuthError, BoxBusy, BoxError, BoxOperationRefused
from tinyassets.boxes.state import op_digest


def validate(operation, args):
    fields = {
        "start_exec": {"argv", "stdin", "env", "cwd", "wall_seconds", "output_bytes",
                       "interactive_stdin"},
        "exec_status": {"op_id"}, "stream": {"exec_id", "offset"},
        "cancel": {"exec_id"}, "send_stdin": {"exec_id", "request_id", "data"}}
    if not isinstance(args, dict) or set(args) != fields.get(operation):
        raise BoxOperationRefused("invalid execution fields")
    if operation == "start_exec":
        from tinyassets.boxes.provider import box_relpath

        box_relpath(args["cwd"])
        if (not isinstance(args["argv"], list) or not 1 <= len(args["argv"]) <= 4096
                or any(not isinstance(v, str) or "\0" in v for v in args["argv"])
                or not args["argv"][0] or not isinstance(args["stdin"], bytes)
                or len(args["stdin"]) > MAX_DATA or not isinstance(args["env"], dict)
                or any(not isinstance(k, str) or not k or "=" in k or "\0" in k
                       or not isinstance(v, str) or "\0" in v for k, v in args["env"].items())
                or type(args["wall_seconds"]) not in (int, float)
                or not math.isfinite(args["wall_seconds"])
                or not 0 < args["wall_seconds"] <= 3600
                or type(args["output_bytes"]) is not int
                or not 1 <= args["output_bytes"] <= MAX_DATA
                or type(args["interactive_stdin"]) is not bool):
            raise BoxOperationRefused("execution exceeds its platform bounds")
    if "exec_id" in args and (not isinstance(args["exec_id"], str)
                              or len(args["exec_id"]) != 32):
        raise BoxOperationRefused("invalid execution id")
    if operation == "exec_status" and (not isinstance(args["op_id"], str)
                                       or not 1 <= len(args["op_id"]) <= 200):
        raise BoxOperationRefused("invalid execution operation id")
    if operation == "stream" and (type(args["offset"]) is not int or args["offset"] < 0):
        raise BoxOperationRefused("invalid output offset")
    if operation == "send_stdin" and (
        not isinstance(args["request_id"], str) or not 1 <= len(args["request_id"]) <= 200
        or not isinstance(args["data"], bytes) or len(args["data"]) > MAX_DATA
    ):
        raise BoxOperationRefused("invalid input reply")
    encode(args)


class Running:
    def __init__(self, args):
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.output = bytearray()
        self.inputs = deque([args["stdin"]] if args["stdin"] else [])
        self.input_bytes = len(args["stdin"])
        self.replies = {}
        self.interactive = args["interactive_stdin"]

    def take_input(self):
        with self.lock:
            return self.inputs.popleft() if self.inputs else b""

    def append_output(self, data):
        with self.lock:
            self.output.extend(data)


class Executions:
    def __init__(self, host):
        self.host = host
        self.running = {}
        self.queues = {}
        self.guard = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="owner-exec")
        self.futures = set()
        self.scheduled = set()
        self.closing = False

    def _submit(self, owner):
        self.scheduled.add(owner)
        future = self.pool.submit(self._run_one, owner)
        self.futures.add(future)
        future.add_done_callback(self._completed)

    def _completed(self, future):
        with self.guard:
            self.futures.discard(future)

    def _remove(self, owner, job):
        with self.guard:
            self.queues[owner].remove(job)
            if not self.queues[owner]:
                del self.queues[owner]
            self.running.pop(job[3], None)

    def _cancel_queued(self, job):
        grant, center, op_id, exec_id, _, running = job
        if running.active:
            running.cancel.set()
            return
        self.host.state.finish(grant.owner_id, op_id, {
            "exec_id": exec_id, "center": center, "session": grant.session_id,
            "exit_code": None, "killed": "cancelled", "output": encode({"data": b""}).decode()})
        self.host.state.release(grant.owner_id)
        self._remove(grant.owner_id, job)

    def cancel_session(self, grant):
        # Caller owns the owner lock; unrelated sessions keep their place.
        with self.guard:
            jobs = list(self.queues.get(grant.owner_id, ()))
        for job in jobs:
            if job[0].session_id == grant.session_id:
                self._cancel_queued(job)

    def request(self, grant, center, operation, args, op_id):
        host, owner = self.host, grant.owner_id
        if operation == "start_exec":
            digest = op_digest("exec", [center, grant.session_id, args])
            with self.guard:
                previous = host.state.lookup(owner, op_id)
                queue = self.queues.get(owner, ())
                if previous is None and (self.closing or len(self.running) >= 16
                                         or host.state.pending(owner) > len(queue)):
                    raise BoxBusy("execution capacity or unquiesced owner writers")
                previous = host.state.begin(owner, op_id, "exec", digest)
                if previous is not None:
                    outcome = previous["outcome"] or {}
                    if previous["state"] == "unknown_after_restore" or not outcome.get("exec_id"):
                        raise BoxError("execution outcome is unknown; hold, never replay")
                    return outcome["exec_id"]
                exec_id = uuid.uuid4().hex
                running = Running(args)
                running.active = False
                running.start_lock = host.lock(owner)
                running.authorize = lambda: host._authorized(grant, center)
                host.state.register_exec(owner, op_id, exec_id)
                host.state.update(owner, op_id, {"exec_id": exec_id, "center": center,
                                               "session": grant.session_id})
                host.begin_work(grant, center)
                self.running[exec_id] = running
                first = owner not in self.scheduled
                self.queues.setdefault(owner, deque()).append(
                    (grant, center, op_id, exec_id, args, running))
                if first:
                    self._submit(owner)
            return exec_id
        record = (host.state.lookup(owner, args["op_id"]) if operation == "exec_status"
                  else host.state.find_exec(owner, args["exec_id"]))
        if record is None or (operation == "exec_status" and record["kind"] != "exec"):
            raise BoxAuthError("execution does not belong to this owner")
        outcome = record["outcome"] or {}
        if (outcome.get("center"), outcome.get("session")) != (center, grant.session_id):
            raise BoxAuthError("execution belongs to another admitted session")
        exec_id = outcome["exec_id"]
        unknown = record["state"] == "unknown_after_restore"
        if operation == "exec_status":
            return {"op_id": args["op_id"], "exec_id": exec_id,
                    "state": "unknown_after_restore" if unknown else
                    ("exited" if record["state"] == "done" else "running"),
                    "exit_code": outcome.get("exit_code"), "killed": outcome.get("killed")}
        running = self.running.get(exec_id)
        if operation == "stream":
            if unknown or (record["state"] != "done" and running is None):
                raise BoxError("execution outcome is unknown after host restart")
            if running is not None:
                with running.lock:
                    output = bytes(running.output)
            else:
                output = decode(outcome["output"].encode())["data"]
            offset = args["offset"]
            if offset > len(output):
                raise BoxOperationRefused("output offset exceeds recorded bytes")
            chunk = output[offset:offset + 65536]
            return {"data": chunk, "done": record["state"] == "done"
                    and offset + len(chunk) == len(output),
                    "exit_code": outcome.get("exit_code"), "killed": outcome.get("killed")}
        if unknown:
            raise BoxError("execution outcome is unknown after host restart")
        if operation == "cancel":
            if running is not None:
                with self.guard:
                    job = next(job for job in self.queues[owner] if job[3] == exec_id)
                self._cancel_queued(job)
            return None
        if running is None or not running.interactive:
            raise BoxError("interactive execution is no longer running")
        with running.lock:
            key, data = args["request_id"], args["data"]
            digest = hashlib.sha256(data).hexdigest()
            previous = running.replies.get(key)
            if previous is not None:
                if digest != previous:
                    raise BoxOperationRefused("input reply id was reused")
                return None
            if running.input_bytes + len(data) > MAX_DATA or len(running.replies) >= 1024:
                raise BoxOperationRefused("interactive input exceeds its lifetime bound")
            running.replies[key] = digest
            running.input_bytes += len(data)
            running.inputs.append(data)
        return None

    def _run_one(self, owner):
        host = self.host
        with host.lock(owner):
            with self.guard:
                queue = self.queues.get(owner)
                if not queue:
                    self.scheduled.discard(owner)
                    return
                job = queue[0]
                grant, center, op_id, exec_id, args, running = job
                running.active = True
        result, dispatched, quiescent = None, False, True
        try:
            if running.cancel.is_set():
                result = {"exit_code": None, "killed": "cancelled"}
            else:
                dispatched = True
                request = {**args, "cwd": "/cc/" + center + args["cwd"][3:]}
                result = host.backend.execute(grant.uid, request, running)
        except Exception:
            pass  # transport failure has an unknown outcome, never a false exit
        finally:
            with host.lock(owner):
                try:
                    if dispatched:
                        host.end_work(grant)
                    else:
                        host.state.release(owner)
                except Exception:
                    result, quiescent = None, False
                try:
                    if result is not None:
                        host.state.finish(owner, op_id, {
                            "exec_id": exec_id, "center": center, "session": grant.session_id,
                            **result, "output": encode({"data": bytes(running.output)}).decode()})
                    else:
                        host.state.unknown(owner, op_id)
                except (BoxError, sqlite3.Error):
                    try:
                        host.state.unknown(owner, op_id)
                    except (BoxError, sqlite3.Error):
                        pass  # full journals hold the operation, never permit replay
                finally:
                    self._remove(owner, job)
                    with self.guard:
                        self.scheduled.discard(owner)
                        queued = list(self.queues.get(owner, ()))
                    if not quiescent or self.closing:
                        for waiting in queued:
                            self._cancel_queued(waiting)
                    elif queued:
                        with self.guard:
                            self._submit(owner)

    def close(self):
        with self.guard:
            self.closing = True
            for running in self.running.values():
                running.cancel.set()
            futures = list(self.futures)
        _, pending = wait(futures, timeout=45)
        self.pool.shutdown(wait=False)
        if pending or self.running:
            raise BoxError("execution supervisor did not quiesce")

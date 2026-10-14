"""Host-owned authority, generations and receipts for production owner files.

The authenticated channel supplies OwnerGrant out of band. A request payload
cannot supply a grant, UID, host path, quota or journal. The execution supervisor
must hold the same owner lock and call begin_work/end_work for arbitrary code.
"""

from __future__ import annotations

import os
import stat
import threading
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Protocol

from tinyassets.boxes.file_wire import MAX_DATA, decode, encode
from tinyassets.boxes.handles import HostHandles, OwnerGrant
from tinyassets.boxes.provider import (
    BoxAuthError,
    BoxBusy,
    BoxError,
    BoxOperationRefused,
    StaleHandle,
    WriteConflict,
    box_relpath,
)
from tinyassets.boxes.state import BoxHostState, op_digest
from tinyassets.broker.owner_identities import validate_center


class FileBackend(Protocol):
    def recover(self) -> None:
        """Prove all pre-existing sandboxes stopped before returning."""
        ...

    def call(self, uid: int, operation: str, args: dict) -> dict: ...
    def stop(self, uid: int) -> None: ...
    def close(self) -> None: ...


class OwnerFileHost:
    def __init__(self, *, state_dir: Path, backend: FileBackend,
                 authorize: Callable[[OwnerGrant, str], bool],
                 authorize_destroy: Callable[[OwnerGrant, str], bool] | None = None) -> None:
        self.backend = backend
        self.authorize = authorize
        self.authorize_destroy = authorize_destroy
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = state_dir.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise BoxError("host journal requires a private host-owned directory")
        # Acquire the host lock BEFORE recovery, so a second service cannot kill
        # a live host's sandboxes. Recovery failure never exposes any handle.
        # Owner-controlled output/receipt growth must not fill the host root
        # outside the XFS owner quotas. Exhaustion refuses; receipts never expire
        # into replay permission. WAL connections are short and serialized.
        self.state = BoxHostState(state_dir / "boxhost.db", max_bytes=16 * 1024 * 1024)
        try:
            backend.recover()
        except BaseException:
            self.state.close()
            raise
        self.handles = HostHandles(self.state)
        self._centers: dict[str, str] = {}
        self._sessions: dict[tuple[str, str], set[str]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self._closed = False
        from tinyassets.boxes.executions import Executions

        self.executions = Executions(self)

    def lock(self, owner: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(owner, threading.RLock())

    def _authorized(self, grant: OwnerGrant, center: str) -> None:
        if self._closed:
            raise BoxError("owner file host is closed")
        if not isinstance(grant, OwnerGrant) or self.authorize(grant, center) is not True:
            raise BoxAuthError("owner session or command center admission is no longer valid")
        if self.state.owner_destroy(grant.owner_id) is not None:
            raise BoxAuthError("owner admission is permanently fenced for erasure")

    def _destroy_authorized(self, grant: OwnerGrant, center: str) -> None:
        if (self._closed or not isinstance(grant, OwnerGrant)
                or self.authorize_destroy is None
                or self.authorize_destroy(grant, center) is not True):
            raise BoxAuthError("owner tombstone and retained binding required for destruction")

    def bind(self, grant: OwnerGrant, center: str, *, destroying: bool = False) -> dict:
        validate_center(center)
        authorize = self._destroy_authorized if destroying else self._authorized
        authorize(grant, center)
        with self.lock(grant.owner_id):
            authorize(grant, center)
            handle = self.handles.bind(grant)
            self._centers[handle.token] = center
            self._sessions.setdefault((grant.owner_id, grant.session_id), set()).add(handle.token)
            return {"token": handle.token, "epoch": handle.epoch, "center": center}

    def destroy(self, grant: OwnerGrant, token: str, op_id: str) -> dict:
        if not isinstance(token, str) or token not in self._centers:
            raise BoxAuthError("unknown owner deletion handle")
        center = self._centers[token]
        with self.lock(grant.owner_id):
            self._destroy_authorized(grant, center)
            previous = self.state.owner_destroy(grant.owner_id)
            try:
                checked = self.handles.check(token, channel_owner=grant.owner_id,
                                             session_id=grant.session_id, op_id=op_id)
            except StaleHandle:
                if previous is None or (previous["op_id"], previous["uid"], previous["center"]) != (
                    op_id, grant.uid, center
                ):
                    raise
                # check() validated owner/session/op before checking the epoch.
            else:
                if checked.uid != grant.uid:
                    raise BoxAuthError("permanent owner identity changed")
            record = self.state.begin_owner_destroy(grant.owner_id, op_id, grant.uid, center)
            if record["state"] != "done":
                self.backend.stop(grant.uid)
                if record["entries"] is None:
                    self.state.count_owner_destroy(grant.owner_id,
                                                   self.backend.destroy_count(grant.uid))
                self.backend.destroy(grant.uid)
                self.state.finish_owner_destroy(grant.owner_id)
                record = self.state.owner_destroy(grant.owner_id)
            return {"result": {"command_center_id": center, "op_id": op_id,
                               "files_removed": record["entries"], "new_epoch": record["epoch"]}}

    def close_session(self, grant: OwnerGrant) -> None:
        with self.lock(grant.owner_id):
            self.handles.close_session(grant.owner_id, grant.session_id)
            for token in self._sessions.pop((grant.owner_id, grant.session_id), ()):
                self._centers.pop(token, None)

    def begin_work(self, grant: OwnerGrant, center: str) -> None:
        """Called under lock before arbitrary in-box processes are dispatched."""
        with self.lock(grant.owner_id):
            self._authorized(grant, center)
            self.state.hold(grant.owner_id)
            # Invalidate caches before any possible effect (including process death).
            self.state.bump_generation(grant.owner_id)

    def end_work(self, grant: OwnerGrant) -> None:
        """Call under lock ONLY after all guest writers are proven stopped."""
        with self.lock(grant.owner_id):
            self.backend.stop(grant.uid)
            self.state.bump_generation(grant.owner_id)
            self.state.release(grant.owner_id)

    def request(self, grant: OwnerGrant, token: str, operation: str,
                args: dict, *, op_id: str | None = None) -> dict:
        # The channel grant is not decoded from args. Require an exact operation
        # schema before touching the journal, sandbox or owner tree.
        exec_operation = operation in {
            "start_exec", "stream", "exec_status", "send_stdin", "cancel"}
        if exec_operation:
            from tinyassets.boxes.executions import validate as validate_exec

            validate_exec(operation, args)
        else:
            validate(operation, args)
        if operation == "destroy":
            return self.destroy(grant, token, op_id)
        if not isinstance(token, str):
            raise BoxAuthError("invalid owner file handle")
        center = self._centers.get(token)
        if center is None:
            raise BoxAuthError("unknown owner file handle")
        self._authorized(grant, center)
        with self.lock(grant.owner_id):
            self._authorized(grant, center)
            handle = self.handles.check(
                token, channel_owner=grant.owner_id, session_id=grant.session_id,
                op_id=op_id if op_id is not None else str(uuid.uuid4()),
            )
            if handle.uid != grant.uid:
                raise BoxAuthError("permanent owner identity changed")
            owner = grant.owner_id
            if exec_operation:
                return {"result": self.executions.request(grant, center, operation, args, op_id)}
            if self.state.pending(owner):
                raise BoxBusy("owner files have running or unquiesced writers")
            if operation == "committed_generation":
                return {"result": {"generation": self.state.generation(owner)}}
            if operation == "suspend":
                self.backend.stop(grant.uid)
                return {"result": None}
            if operation == "ensure_awake":
                self.backend.wake(grant.uid)
                return {"result": None}
            mutation = operation in {"write", "remove"}
            if mutation:
                if op_id is None:
                    raise BoxOperationRefused("mutation requires an operation id")
                # Include center, since siblings share the owner's operation log.
                previous = self.state.begin(owner, op_id, operation,
                                            op_digest(operation, [center, args]))
                if previous is not None:
                    if previous["state"] != "done":
                        raise BoxError("operation outcome is unknown; hold, never replay")
                    return decode(previous["outcome"]["response"].encode())
                if operation == "write" and args["mode"] == "cas" and (
                    args["expect_generation"] != self.state.generation(owner)
                ):
                    self.state.abandon(owner, op_id)
                    raise WriteConflict("owner generation changed")
                self.state.hold(owner)
                self.state.bump_generation(owner)
            try:
                forwarded = project(center, operation, args)
                result = self.backend.call(grant.uid, operation, forwarded)
                if set(result) not in ({"result"}, {"error"}):
                    raise BoxError("malformed sandbox file response")
                if "error" in result:
                    if not isinstance(result["error"], str):
                        raise BoxError("malformed sandbox file error")
                    if mutation:
                        # A guest error is not proof that dispatch had no effect.
                        # Never export a guest-supplied BoxOperationRefused as
                        # permission for the caller to issue a fresh mutation.
                        result = {"error": "BoxError"}
                # Do not trust any generation or path supplied from inside the box.
                if "result" in result:
                    result = unproject(center, operation, result, args)
                    if operation in {"read", "read_many", "write", "usage"}:
                        result["result"]["generation"] = self.state.generation(owner)
                if mutation:
                    response = encode(result).decode()
                    self.backend.stop(grant.uid)
                    if not self.state.finish(owner, op_id, {"response": response}):
                        raise BoxError("host incarnation changed during file mutation")
                    self.state.release(owner)
                return result
            except BaseException:
                if mutation:
                    # Keep the durable operation running: recovery marks it unknown.
                    # Keep pending if stop fails, so no reader sees a false snapshot.
                    self.backend.stop(grant.uid)
                    self.state.release(owner)
                raise

    def close(self) -> None:
        with self._guard:
            self._closed = True
            locks = list(self._locks.values())
        self.executions.close()
        with ExitStack() as stack:
            for lock in locks:
                stack.enter_context(lock)
            # Drain requests before releasing the journal lock. A failure keeps
            # the journal locked and the service closed to new requests.
            self.backend.close()
            self.state.close()


_FIELDS = {
    "read": {"path", "offset", "max_bytes"},
    "read_many": {"paths", "max_total"},
    "stat": {"path"}, "list": {"path", "cursor", "limit"},
    "write": {"path", "data", "max_bytes", "mode", "expect_generation"},
    "remove": {"path"}, "usage": set(), "committed_generation": set(), "suspend": set(),
    "destroy": set(), "ensure_awake": set(),
}


def validate(operation: str, args: dict) -> None:
    if not isinstance(operation, str) or operation not in _FIELDS or (
        not isinstance(args, dict) or set(args) != _FIELDS[operation]
    ):
        raise BoxOperationRefused("unsupported file operation or arguments")
    if "path" in args:
        box_relpath(args["path"])
    if operation == "read_many":
        if not isinstance(args["paths"], list) or len(args["paths"]) > 1000:
            raise BoxOperationRefused("snapshot paths exceed the bound")
        for path in args["paths"]:
            box_relpath(path)
    for key in ("max_bytes", "max_total", "offset", "limit"):
        if key in args and (type(args[key]) is not int or args[key] < 0):
            raise BoxOperationRefused("invalid file operation bound")
    for key in ("max_bytes", "max_total"):
        if args.get(key, 0) > MAX_DATA:
            raise BoxOperationRefused("file operation exceeds the byte bound")
    if operation == "list" and (
        not 1 <= args["limit"] <= 1000 or
        (args["cursor"] is not None and (
            not isinstance(args["cursor"], str) or len(args["cursor"]) > 4096))
    ):
        raise BoxOperationRefused("invalid directory page")
    if operation == "write":
        if not isinstance(args["data"], bytes) or len(args["data"]) > args["max_bytes"]:
            raise BoxOperationRefused("file write exceeds the byte bound")
        if args["mode"] not in {"create", "replace", "cas"} or (
            args["mode"] == "cas" and (
                type(args["expect_generation"]) is not int or args["expect_generation"] < 0)
        ):
            raise BoxOperationRefused("invalid write mode or generation")
    encode(args)  # bound total path/argument bytes before dispatch


def project(center: str, operation: str, args: dict) -> dict:
    def path(value):
        relative = box_relpath(value)
        return f"/cc/{center}" + ("/" + relative if relative else "")

    result = dict(args)
    if "path" in result:
        result["path"] = path(result["path"])
    if "paths" in result:
        result["paths"] = [path(value) for value in result["paths"]]
    if operation == "write":
        result.pop("expect_generation")
        if result["mode"] == "cas":
            result["mode"] = "replace"
    return result


def unproject(center: str, operation: str, response: dict, args: dict) -> dict:
    result = response["result"]
    if operation == "read_many":
        mapping = dict(zip(project(center, operation, args)["paths"], args["paths"]))
        result = {"files": {mapping[path]: value for path, value in result["files"].items()},
                  "missing": [mapping[path] for path in result["missing"]]}
    elif operation in {"write", "stat"} and result is not None:
        result = {**result, "path": args["path"]}
    return {"result": result}

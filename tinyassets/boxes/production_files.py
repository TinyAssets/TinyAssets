"""Production file and execution operations over authenticated owner channels.

The final cutover selects this provider through boxes.frontend. Legacy whole
box archive operations refuse explicitly; C12 owns the canonical export and
import implementation over the bounded file surface. No local fallback exists.
"""

from __future__ import annotations

import threading
import time
import uuid
import weakref

from tinyassets.boxes.file_wire import MAX_DATA
from tinyassets.boxes.provider import (
    BoxAuthError,
    BoxError,
    BoxHandle,
    BoxOperationRefused,
    BoxUsage,
    DestroyReceipt,
    DirEntry,
    DirPage,
    ExecEvent,
    ExecLimits,
    ExecState,
    ExecStatus,
    FileRead,
    FileStat,
    FileWrite,
    Snapshot,
    WriteMode,
)


class ProductionHandle(BoxHandle):
    @property
    def root(self):
        return "/home/owner/" + self.command_center_id


class ProductionFileProvider:
    def __init__(self, *, channel_factory) -> None:
        # The factory authenticates an admitted session; account/center strings
        # alone must never create a host grant. No environment-selected fallback.
        self._channel_factory = channel_factory
        self._bindings = {}
        self._guard = threading.RLock()

    def bind(self, command_center_id, *, account_id, turn_id=None):
        return self._bind(command_center_id, account_id, turn_id, False)

    def bind_for_destroy(self, command_center_id, *, account_id, turn_id=None):
        return self._bind(command_center_id, account_id, turn_id, True)

    def _bind(self, command_center_id, account_id, turn_id, destroying):
        session = turn_id or str(uuid.uuid4())
        channel = (self._channel_factory(account_id, command_center_id, session, destroying=True)
                   if destroying else self._channel_factory(account_id, command_center_id, session))
        try:
            result = channel.request({"bind_for_destroy" if destroying else "bind":
                                      command_center_id})
            if result["center"] != command_center_id or type(result["epoch"]) is not int:
                raise BoxAuthError("host returned the wrong center binding")
            handle = ProductionHandle(command_center_id, account_id, result["epoch"], session)
            with self._guard:
                self._bindings[id(handle)] = (weakref.ref(handle), channel, result["token"])
            weakref.finalize(handle, self._forget, id(handle))
            return handle
        except BaseException:
            channel.close()
            raise

    def _call(self, handle, operation, args=None, *, op_id=None):
        with self._guard:
            binding = self._bindings.get(id(handle))
        if binding is None or binding[0]() is not handle:
            raise BoxAuthError("handle was not bound on this provider")
        _, channel, token = binding
        return channel.request({"token": token, "operation": operation,
                                "args": args or {}, "op_id": op_id})

    def committed_generation(self, handle):
        return self._call(handle, "committed_generation")["generation"]

    def ensure_awake(self, handle, *, reason):
        return self._call(handle, "ensure_awake")

    def start_exec(self, handle, op_id, argv, *, stdin=b"", env=None, cwd="/cc",
                   limits=ExecLimits(), interactive_stdin=False):
        if cwd == handle.root or cwd.startswith(handle.root + "/"):
            cwd = "/cc" + cwd[len(handle.root):]
        return self._call(handle, "start_exec", {
            "argv": list(argv), "stdin": stdin, "env": dict(env or {}), "cwd": cwd,
            "wall_seconds": limits.wall_seconds, "output_bytes": limits.output_bytes,
            "interactive_stdin": interactive_stdin}, op_id=op_id)

    def exec_status(self, handle, op_id):
        result = self._call(handle, "exec_status", {"op_id": op_id})
        return ExecStatus(**{**result, "state": ExecState(result["state"])})

    def start_cli(self, handle, op_id, argv, *, env, cli, limits=ExecLimits(600, MAX_DATA)):
        return self._call(handle, "start_cli", {
            "argv": list(argv), "stdin": b"", "env": dict(env), "cwd": "/cc",
            "wall_seconds": limits.wall_seconds, "output_bytes": limits.output_bytes,
            "interactive_stdin": True, "cli": cli}, op_id=op_id)

    def cli_result(self, handle, exec_id):
        return self._call(handle, "cli_result", {"exec_id": exec_id})

    def ack_cli(self, handle, exec_id):
        return self._call(handle, "ack_cli", {"exec_id": exec_id})

    def close_stdin(self, handle, exec_id):
        return self._call(handle, "close_stdin", {"exec_id": exec_id})

    def stream(self, handle, exec_id, *, from_offset=0, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            result = self._call(handle, "stream", {"exec_id": exec_id, "offset": from_offset})
            if result["data"]:
                yield ExecEvent("output", offset=from_offset, data=result["data"])
                from_offset += len(result["data"])
            if result["done"]:
                yield ExecEvent("exit", exit_code=result["exit_code"], killed=result["killed"])
                return
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("execution stream wait expired")
            if not result["data"]:
                time.sleep(0.05)

    def send_stdin(self, handle, exec_id, request_id, data):
        return self._call(handle, "send_stdin", {"exec_id": exec_id,
                                                "request_id": request_id, "data": data})

    def cancel(self, handle, exec_id):
        return self._call(handle, "cancel", {"exec_id": exec_id})

    def read(self, handle, path, *, offset=0, max_bytes):
        return FileRead(**self._call(handle, "read", {
            "path": path, "offset": offset, "max_bytes": max_bytes}))

    def read_many(self, handle, paths, *, max_total):
        result = self._call(handle, "read_many", {"paths": list(paths), "max_total": max_total})
        return Snapshot(result["files"], tuple(result["missing"]), result["generation"])

    def record_etag(self, handle, path):
        return self._call(handle, "record_etag", {"path": path})["etag"]

    def write(self, handle, op_id, path, data, *, max_bytes,
              mode=WriteMode.REPLACE, expect_generation=None, expect_etag: int):
        if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_DATA:
            raise BoxOperationRefused("write exceeds the file transport bound")
        if not isinstance(data, bytes):
            body = bytearray()
            for chunk in data:
                if not isinstance(chunk, bytes) or len(body) + len(chunk) > max_bytes:
                    raise BoxOperationRefused("write exceeds its byte bound")
                body.extend(chunk)
            data = bytes(body)
        return FileWrite(**self._call(handle, "write", {
            "path": path, "data": data, "max_bytes": max_bytes,
            "mode": WriteMode(mode).value, "expect_generation": expect_generation,
            "expect_etag": expect_etag}, op_id=op_id))

    def list(self, handle, path, *, cursor=None, limit=200):
        result = self._call(handle, "list", {"path": path, "cursor": cursor, "limit": limit})
        return DirPage(tuple(DirEntry(**entry) for entry in result["entries"]),
                       result["next_cursor"])

    def stat(self, handle, path):
        result = self._call(handle, "stat", {"path": path})
        return FileStat(**result) if result is not None else None

    def remove(self, handle, op_id, path):
        return self._call(handle, "remove", {"path": path}, op_id=op_id)

    def usage(self, handle):
        return BoxUsage(**self._call(handle, "usage"))

    def download(self, handle, path, *, chunk_bytes=64 * 1024):
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= MAX_DATA:
            raise BoxOperationRefused("invalid download chunk bound")
        generation = self.committed_generation(handle)
        offset = 0
        while True:
            part = self.read(handle, path, offset=offset, max_bytes=chunk_bytes)
            if part.generation != generation:
                raise BoxError("owner generation changed during download")
            if not part.data:
                break
            yield part.data
            offset += len(part.data)
        if self.committed_generation(handle) != generation:
            raise BoxError("owner generation changed during download")

    def suspend(self, handle):
        return self._call(handle, "suspend")

    def destroy(self, handle, op_id):
        """Erase the entire owner's box, after tombstone, with a stable retry id."""
        return DestroyReceipt(**self._call(handle, "destroy", op_id=op_id))

    def end_session(self, handle):
        """Explicit terminal session cancellation; disconnect alone never cancels."""
        result = self._call(handle, "end_session")
        self._forget(id(handle))
        return result

    def close(self):
        with self._guard:
            for _, channel, _ in self._bindings.values():
                channel.close()
            self._bindings.clear()

    def _forget(self, handle_id):
        with self._guard:
            binding = self._bindings.pop(handle_id, None)
        if binding is not None:
            binding[1].close()

    def _requires_cutover(self, *args, **kwargs):
        raise BoxOperationRefused("production execution/cutover integration is not installed")

    export = import_bundle = _requires_cutover

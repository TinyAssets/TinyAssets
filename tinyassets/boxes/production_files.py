"""Production BoxProvider file surface over authenticated owner channels.

This preparation does not select the production execution path. The final
cutover must supply the broker-authenticated channel factory and execution
supervisor. Unimplemented execution/transfer operations refuse explicitly;
there is no local provider or host-path fallback.
"""

from __future__ import annotations

import threading
import uuid
import weakref

from tinyassets.boxes.file_wire import MAX_DATA
from tinyassets.boxes.provider import (
    BoxAuthError,
    BoxError,
    BoxHandle,
    BoxOperationRefused,
    BoxUsage,
    DirEntry,
    DirPage,
    FileRead,
    FileStat,
    FileWrite,
    Snapshot,
    WriteMode,
)


class ProductionFileProvider:
    def __init__(self, *, channel_factory) -> None:
        # The factory authenticates an admitted session; account/center strings
        # alone must never create a host grant. No environment-selected fallback.
        self._channel_factory = channel_factory
        self._bindings = {}
        self._guard = threading.RLock()

    def bind(self, command_center_id, *, account_id, turn_id=None):
        session = turn_id or str(uuid.uuid4())
        channel = self._channel_factory(account_id, command_center_id, session)
        try:
            result = channel.request({"bind": command_center_id})
            if result["center"] != command_center_id or type(result["epoch"]) is not int:
                raise BoxAuthError("host returned the wrong center binding")
            handle = BoxHandle(command_center_id, account_id, result["epoch"], session)
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

    def read(self, handle, path, *, offset=0, max_bytes):
        return FileRead(**self._call(handle, "read", {
            "path": path, "offset": offset, "max_bytes": max_bytes}))

    def read_many(self, handle, paths, *, max_total):
        result = self._call(handle, "read_many", {"paths": list(paths), "max_total": max_total})
        return Snapshot(result["files"], tuple(result["missing"]), result["generation"])

    def write(self, handle, op_id, path, data, *, max_bytes,
              mode=WriteMode.REPLACE, expect_generation=None):
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
            "mode": WriteMode(mode).value, "expect_generation": expect_generation}, op_id=op_id))

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

    ensure_awake = start_exec = send_stdin = stream = cancel = _requires_cutover
    exec_status = export = import_bundle = destroy = _requires_cutover

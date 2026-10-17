"""The four tools inside the owner process; no box-host execution bridge."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tinyassets.boxes.provider import BoxPathError, WriteMode
from tinyassets.owner_runtime import current

BOX_TOOLS: tuple[str, ...] = ("read", "write", "edit", "bash")

#: Where the command center's content sits inside its box (D2 default ``cwd``).
BOX_ROOT = "/cc"

_MiB = 1024 * 1024
MAX_WRITE_BYTES = 4 * _MiB
MAX_EDIT_BYTES = 1 * _MiB
DEFAULT_READ_LINES = 2000
DEFAULT_WALL_SECONDS = 120.0
MAX_BASH_SECONDS = 600.0
OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ExecOutcome:
    """What one execution reported: merged output, exit code, and why it was cut."""

    output: bytes
    exit_code: int | None
    killed: str | None = None


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _trailer(outcome: ExecOutcome, wall: float, output_bytes: int) -> str:
    if outcome.killed == "timeout":
        return f"[killed: ran longer than {wall:g}s]"
    if outcome.killed == "output_limit":
        return f"[killed: output passed {output_bytes} bytes]"
    if outcome.exit_code is None:
        return "[exit code unknown]"
    if outcome.exit_code in (128 + 24, 128 + 9):
        return "[killed: a cpu time or memory limit]"
    return f"[exit code {outcome.exit_code}]"


class OwnerTools:
    def __init__(self, universe_dir: Path):
        runtime = current()
        self.runtime = runtime
        self._root = str(universe_dir)
        self.provider = runtime.tools
        self.handle = self.provider.bind(runtime.principal, session_id=uuid.uuid4().hex)
        self.bridge = None

    def path(self, path):
        target = Path(path)
        if not target.is_absolute():
            target = Path(self._root) / target
        return "/cc/" + target.relative_to(self.runtime.root).as_posix()

    def read_content(self, path, maximum):
        return self.provider.read(self.handle, self.path(path), max_bytes=maximum)

    async def read(self, op_id, path, offset=0, limit=0):
        try:
            target = self.path(path)
            info = await asyncio.to_thread(self.provider.stat, self.handle, target)
            if info is not None and info.kind == "dir":
                page = await asyncio.to_thread(self.provider.list, self.handle, target,
                                               limit=DEFAULT_READ_LINES)
                body = "".join(item.name + ("/" if item.kind == "dir" else "") + "\n"
                               for item in page.entries)
                return body + ("[truncated; use bash for larger folders]"
                               if page.next_cursor else "")
            read = await asyncio.to_thread(self.read_content, path, MAX_EDIT_BYTES)
        except OSError as exc:
            return f"error: {exc}"
        start = max(1, int(offset or 1)) - 1
        lines = _text(read.data).splitlines(keepends=True)
        result = "".join(lines[start:start + (int(limit) if limit > 0 else DEFAULT_READ_LINES)])
        truncated = "\n[truncated; use bash for larger files]" if read.size > len(read.data) else ""
        encoded = result.encode("utf-8")
        if len(encoded) > OUTPUT_BYTES:
            return _text(encoded[:OUTPUT_BYTES]) + "\n[truncated; read a smaller range]"
        return result + truncated

    async def write(self, op_id, path, content):
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            return f"error: content is over the {MAX_WRITE_BYTES}-byte write limit"
        await asyncio.to_thread(self.provider.write, self.handle, op_id, self.path(path),
                                data, max_bytes=MAX_WRITE_BYTES)
        return f"wrote {len(data)} bytes to {path}"

    async def edit(self, op_id, path, old_text, new_text):
        if not old_text:
            return "error: old_text is required: the exact passage to replace"
        try:
            read = await asyncio.to_thread(self.read_content, path, MAX_EDIT_BYTES)
        except OSError as exc:
            return f"error: {exc}"
        if read.size > len(read.data):
            return "error: file is over the edit limit; use bash"
        try:
            text = read.data.decode("utf-8")
        except UnicodeDecodeError:
            return f"error: {path} is not UTF-8 text; use bash"
        if text.count(old_text) != 1:
            return "error: old_text must match exactly once"
        await asyncio.to_thread(
            self.provider.write, self.handle, op_id, self.path(path),
            text.replace(old_text, new_text, 1).encode("utf-8"),
            max_bytes=MAX_WRITE_BYTES, mode=WriteMode.CAS, expect_generation=read.generation,
        )
        return f"edited {path}"

    def enable_ta(self, *, dispatch, deliver=None, turn=None):
        from tinyassets.agent_loop.box_ta import TurnBridge

        session = turn or self.handle.turn_id
        self.bridge = TurnBridge(
            owner=self.runtime.principal, center=Path(self._root).name, turn=session,
            handle=replace(self.handle, command_center_id=Path(self._root).name, turn_id=session),
            database=self.runtime.root / ".remote-ta-receipts.sqlite3",
            dispatch=dispatch, deliver=deliver,
        )
        return self.bridge

    async def bash(self, op_id, command, timeout=0, *, env=None):
        if not command.strip():
            return "error: a command is required"
        wall = min(max(float(timeout or DEFAULT_WALL_SECONDS), 1), MAX_BASH_SECONDS)
        from tinyassets.owner_git import for_command

        with (tempfile.TemporaryDirectory(prefix="ta-owner-") as directory,
              for_command(env or {}, Path(self._root)) as command_env):
            launch_env = {**os.environ, **command_env}
            server = None
            bridge = self.bridge
            if bridge is not None:
                from tinyassets import ta_cli
                from tinyassets.agent_loop.extension_stage import stage
                from tinyassets.ta_capabilities import MAX_REQUEST
                from tinyassets.universe_files import read_data_path, write_data_path

                extensions = Path(directory) / "extensions"
                extensions.mkdir()
                stage(extensions, await bridge.extensions(bridge.handle, op_id))
                client = Path(directory) / "ta"
                write_data_path(client, read_data_path(Path(ta_cli.__file__)))
                client.chmod(0o700)
                address = "\0ta-owner-" + uuid.uuid4().hex

                async def serve(reader, writer):
                    try:
                        raw = await reader.readline()
                        doc = json.loads(raw)
                        answer = await asyncio.to_thread(
                            bridge.request, bridge.handle, op_id, doc["request"], doc["message"],
                        )
                        writer.write(json.dumps(answer).encode() + b"\n")
                        await writer.drain()
                    finally:
                        writer.close()
                        await writer.wait_closed()

                server = await asyncio.start_unix_server(serve, address, limit=MAX_REQUEST)
                launch_env.update(
                    TA_SOCKET="@" + address[1:], TA_EXTENSION_ROOT=str(extensions),
                    PATH=directory + os.pathsep + launch_env.get("PATH", "/usr/bin:/bin"),
                )
            from tinyassets.boxes.provider import BoxError
            from tinyassets.owner_process import OwnerProcessCancelled

            cancelled = threading.Event()
            task = asyncio.create_task(asyncio.to_thread(
                self.provider.run, ["/bin/bash", "-c", command], cwd=self._root,
                env=launch_env, wall_seconds=wall, output_bytes=OUTPUT_BYTES,
                should_cancel=cancelled.is_set,
            ))
            try:
                try:
                    done = await asyncio.shield(task)
                    outcome = ExecOutcome(done.stdout, done.returncode)
                except asyncio.CancelledError:
                    cancelled.set()
                    try:
                        await asyncio.shield(task)
                    except OwnerProcessCancelled:
                        pass
                    raise
                except BoxError as exc:
                    reason = str(exc).rsplit(": ", 1)[-1]
                    if reason not in {"timeout", "output_limit"}:
                        raise
                    outcome = ExecOutcome(exc.output, None, reason)
            finally:
                if server is not None:
                    server.close()
                    await server.wait_closed()
                if bridge is not None:
                    bridge.cancel_execution(op_id)
            body = _text(outcome.output)
            return body + ("\n" if body and not body.endswith("\n") else "") + _trailer(
                outcome, wall, OUTPUT_BYTES,
            )

    async def call(self, name: str, op_id: str, arguments: Mapping[str, Any]) -> str:
        """Dispatch one validated call; an argument the tool does not take is refused."""
        handler, allowed = {
            "read": (self.read, {"path", "offset", "limit"}),
            "write": (self.write, {"path", "content"}),
            "edit": (self.edit, {"path", "old_text", "new_text"}),
            "bash": (self.bash, {"command", "timeout"}),
        }[name]
        unexpected = set(arguments) - allowed
        if unexpected:
            return f"error: {name} does not take {sorted(unexpected)}"
        try:
            return await handler(op_id, **arguments)
        except (TypeError, ValueError, BoxPathError) as exc:
            return f"error: {exc}"




def tools_for(universe_dir):
    tools = OwnerTools(Path(universe_dir))
    return tools, tools._root


def box_tool_definitions(root: str = BOX_ROOT) -> dict[str, dict[str, Any]]:
    def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": required}

    text = {"type": "string"}
    integer = {"type": "integer"}
    return {
        "read": {
            "description": (f"Read a file in your folder {root} (relative paths are under "
                            f"{root}).\noffset: first line (1-based); limit: line count "
                            "(default 2000)."),
            "inputSchema": schema(
                {"path": text, "offset": dict(integer, default=0),
                 "limit": dict(integer, default=0)}, ["path"]),
        },
        "write": {
            "description": f"Create or replace a file in {root}, making parent folders.",
            "inputSchema": schema({"path": text, "content": text}, ["path", "content"]),
        },
        "edit": {
            "description": (f"In a file in {root}, replace old_text (must match exactly "
                            "once) with new_text."),
            "inputSchema": schema({"path": text, "old_text": text, "new_text": text},
                                  ["path", "old_text", "new_text"]),
        },
        "bash": {
            "description": (f"Run a bash command in {root}. Memory, processes and time are "
                            "limited.\ntimeout: seconds (default 120, max 600)."),
            "inputSchema": schema({"command": text, "timeout": dict(integer, default=0)},
                                  ["command"]),
        },
    }

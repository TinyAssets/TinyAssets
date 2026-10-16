"""The four tools inside the owner process; no box-host execution bridge."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

from tinyassets.agent_loop.box_tools import (
    DEFAULT_READ_LINES,
    DEFAULT_WALL_SECONDS,
    MAX_BASH_SECONDS,
    MAX_EDIT_BYTES,
    MAX_WRITE_BYTES,
    OUTPUT_BYTES,
    BoxTools,
    ExecOutcome,
    _text,
    _trailer,
)
from tinyassets.boxes.provider import WriteMode
from tinyassets.owner_runtime import current


class OwnerTools(BoxTools):
    def __init__(self, universe_dir: Path):
        runtime = current()
        if runtime is None:
            raise RuntimeError("owner tools require owner startup admission")
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
        text = read.data.decode("utf-8")
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
        with tempfile.TemporaryDirectory(prefix="ta-owner-") as directory:
            launch_env = {**os.environ, **(env or {})}
            server = None
            bridge = self.bridge
            if bridge is not None:
                from tinyassets import ta_cli
                from tinyassets.agent_loop.box_ta_worker import stage
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
            process = None
            killed = None
            output = bytearray()
            try:
                process = await asyncio.create_subprocess_exec(
                    "/bin/bash", "-c", command, cwd=self._root, env=launch_env,
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, start_new_session=True,
                )

                async def collect():
                    nonlocal killed
                    while block := await process.stdout.read(4096):
                        remaining = OUTPUT_BYTES - len(output)
                        output.extend(block[:remaining])
                        if len(block) > remaining:
                            killed = "output_limit"
                            return
                    await process.wait()

                try:
                    await asyncio.wait_for(collect(), wall)
                except TimeoutError:
                    killed = "timeout"
            finally:
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
                if server is not None:
                    server.close()
                    await server.wait_closed()
                if bridge is not None:
                    bridge.cancel_execution(op_id)
            body = _text(bytes(output))
            return body + ("\n" if body and not body.endswith("\n") else "") + _trailer(
                ExecOutcome(bytes(output), process.returncode, killed), wall, OUTPUT_BYTES,
            )


def tools_for(universe_dir):
    tools = OwnerTools(Path(universe_dir))
    return tools, tools._root

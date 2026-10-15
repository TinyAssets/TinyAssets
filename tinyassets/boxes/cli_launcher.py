"""External-agent process adapter for an explicitly admitted production binding."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

from tinyassets.boxes.file_wire import MAX_DATA
from tinyassets.boxes.provider import BoxAuthError, BoxError, BoxNotFound, ExecState


class Input:
    def __init__(self, process):
        self.process = process
        self.buffer = bytearray()
        self.closed = False
        self.closing = None
        self.lock = asyncio.Lock()

    def write(self, data):
        if self.closed or not isinstance(data, bytes) or len(self.buffer) + len(data) > MAX_DATA:
            raise BoxError("private CLI input closed or exceeds bound")
        self.buffer.extend(data)

    async def drain(self):
        async with self.lock:
            if self.buffer:
                data = bytes(self.buffer)
                self.buffer.clear()
                await asyncio.to_thread(self.process.provider.send_stdin, self.process.handle,
                                        self.process.ident, uuid.uuid4().hex, data)

    def close(self):
        if not self.closed:
            self.closed = True
            self.closing = asyncio.create_task(self._close())

    async def _close(self):
        await self.drain()
        await asyncio.to_thread(self.process.provider.close_stdin,
                                self.process.handle, self.process.ident)


class Process:
    def __init__(self, provider, handle, ident, workdir, *, resumed=False):
        self.provider, self.handle, self.ident = provider, handle, ident
        self.workdir = workdir
        self.resumed = resumed
        self.returncode = None
        self.stdin = Input(self)
        self.stdout = asyncio.StreamReader(limit=MAX_DATA)
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.output = asyncio.create_task(self._output())

    async def _output(self):
        events = self.provider.stream(self.handle, self.ident)
        try:
            # A call-scoped producer: at most MAX_DATA output bytes and one
            # provider stream. Cancellation remains a separate admitted RPC.
            while True:
                event = await asyncio.to_thread(next, events, None)
                if event is None:
                    break
                if event.kind == "output":
                    self.stdout.feed_data(event.data)
                else:
                    self.returncode = event.exit_code
                    if event.killed:
                        raise BoxError("private CLI execution did not complete")
            self.stdout.feed_eof()
        except BaseException as error:
            self.stdout.set_exception(error)
            raise
        finally:
            events.close()

    async def wait(self):
        if self.stdin.closing is not None:
            await self.stdin.closing
        await asyncio.shield(self.output)
        return self.returncode

    async def cancel(self):
        await asyncio.to_thread(self.provider.cancel, self.handle, self.ident)


class BoxLauncher:
    """Pass to the existing runner, which owns exclusive_refresh and auth CAS.

    C7 supplies this binding explicitly; no path, environment or owner string
    can select or mint a production channel here.
    """

    def __init__(self, provider, handle, custody, *, operation_id):
        if (handle.account_id != custody.owner_user_id
                or handle.command_center_id != custody.reference.universe_id):
            raise BoxAuthError("CLI custody differs from admitted owner binding")
        self.provider, self.handle, self.custody = provider, handle, custody
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise ValueError("CLI launch requires a stable invocation ID")
        self.operation_id = operation_id
        self.processes = set()

    async def resume(self):
        """Recover an exited operation without sending another CLI request."""
        try:
            status = await asyncio.to_thread(
                self.provider.exec_status, self.handle, self.operation_id)
        except BoxNotFound:
            return None
        except BoxAuthError as exc:
            # The host uses this exact refusal for an as-yet unstarted op.
            # A different session refusal must stay fatal.
            if str(exc) != "execution does not belong to this owner":
                raise
            return None
        if status.state != ExecState.EXITED:
            raise BoxError("native CLI outcome is pending or unknown; never replay")
        nonce = hashlib.sha256(self.operation_id.encode()).hexdigest()[:32]
        proc = Process(self.provider, self.handle, status.exec_id,
                       "/tmp/ta-agent-" + nonce, resumed=True)
        self.processes.add(proc)
        return proc

    async def spawn(self, argv, env, *, home, workdir, egress, auth_files, auth,
                    catalogue_rewrite=None, requested_model=None):
        if (home != "/agent-home" or egress != self.handle.command_center_id
                or auth["service"] != self.custody.reference.service
                or set(auth_files) != set(auth["files"]) or not self.custody.current()):
            raise BoxAuthError("CLI lease differs from current custody")
        nonce = hashlib.sha256(self.operation_id.encode()).hexdigest()[:32]
        cli = {"home_dir": auth["home_dir"], "workdir": workdir,
               "auth_files": auth_files, "nonce": nonce,
               "env_from_file": dict(auth.get("env_from_file") or {}),
               "catalogue_rewrite": catalogue_rewrite, "requested_model": requested_model,
               "custody": hashlib.sha256((auth["service"] + "\0" +
                   self.custody.reference.reference_digest).encode()).hexdigest()}
        ident = await asyncio.to_thread(self.provider.start_cli, self.handle,
                                        self.operation_id, argv,
                                        env=env, cli=cli)
        proc = Process(self.provider, self.handle, ident, "/tmp/ta-agent-" + nonce)
        self.processes.add(proc)
        return proc

    async def close(self, proc):
        from tinyassets.external_agents.writeback import AuthWriteback

        if proc not in self.processes:
            raise BoxAuthError("foreign CLI process")
        if not proc.resumed:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=15)
        except BaseException:
            await proc.cancel()
            raise
        result = await asyncio.to_thread(self.provider.cli_result, self.handle, proc.ident)
        return AuthWriteback(result["files"], result["exit_code"])

    async def acknowledge(self, proc):
        if proc not in self.processes:
            raise BoxAuthError("foreign CLI process")
        await asyncio.to_thread(self.provider.ack_cli, self.handle, proc.ident)
        self.processes.remove(proc)

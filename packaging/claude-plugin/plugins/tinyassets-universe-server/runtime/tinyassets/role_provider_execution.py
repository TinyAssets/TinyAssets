"""Provider-neutral text execution selected at the sole shared spawn boundary."""
from __future__ import annotations

import asyncio
import os
import weakref
from pathlib import Path
from types import SimpleNamespace

from tinyassets.providers.owned_process import OwnerCellProcess
from tinyassets.providers.provider_jail import ProviderConfinementError


class _PipeWriterProtocol(asyncio.streams.FlowControlMixin):
    """What ``asyncio.subprocess`` gives a child's stdin: a flow-controlled
    writer whose ``drain`` raises once the reader (the CLI) is gone."""

    def __init__(self, loop):
        super().__init__(loop=loop)
        self._closed = loop.create_future()

    def connection_lost(self, exc):
        super().connection_lost(exc)
        if not self._closed.done():
            if exc is None:
                self._closed.set_result(None)
            else:
                self._closed.set_exception(exc)

    def _get_close_waiter(self, stream):
        return self._closed

    def __del__(self):
        # Match asyncio.streams: an unretrieved close exception is not a warning.
        try:
            if self._closed.done() and not self._closed.cancelled():
                self._closed.exception()
        except Exception:  # noqa: BLE001 - interpreter shutdown
            pass


async def pipe_streams(cell, limit):
    """(stdout reader, stdin writer, stderr reader) over the cell's three pipes.

    The read transports take duplicates of the cell's descriptors, so the
    authenticated reap can close the cell's own ends without discarding bytes
    still queued for a reader. The write transport takes the cell's stdin end
    ITSELF: a pipe reader sees EOF only when every write end is closed, so the
    daemon must hold exactly one, and closing the writer must be that close.
    """
    loop = asyncio.get_running_loop()
    readers = []
    for source in (cell.stream, cell.stderr):
        reader = asyncio.StreamReader(limit=limit, loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        await loop.connect_read_pipe(lambda protocol=protocol: protocol,
                                     os.fdopen(os.dup(source.fileno()), 'rb', buffering=0))
        readers.append(reader)
    stdin_fd = os.dup(cell.stdin.fileno())
    cell.stdin.close()
    cell.stdin = None
    transport, protocol = await loop.connect_write_pipe(
        lambda: _PipeWriterProtocol(loop), os.fdopen(stdin_fd, 'wb', buffering=0))
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    return readers[0], writer, readers[1]


class ExecutionProcess(OwnerCellProcess):
    """Async process shape over three real pipes, with a mapper-owned lifetime.

    ``stdin`` is an ordinary pipe writer: ``close()`` ends only the CLI's
    input, and a CLI that exits without reading it costs the writer an EPIPE
    and nothing else. Stdout and stderr are read to their own EOFs.
    """

    def __init__(self, cell, reader, writer, error_reader):
        super().__init__(cell, reader, writer)
        self.stdin = writer  # a one-directional pipe: no half-close shim
        self.stderr = error_reader
        self._stdio_finalizer = weakref.finalize(self, self._close_stdio, writer)

    @staticmethod
    def _close_stdio(*writers):
        for writer in writers:
            writer.close()

    def close_stdio(self):
        self._stdio_finalizer()

    async def wait(self, timeout=None):
        # No wall-clock ceiling: the cell lives until it exits or is revoked.
        # Async readers hold dup endpoints: reaping may close OwnerCell's own
        # handles without discarding bytes still queued for the readers.
        result = await super().wait(timeout)
        if self.stdout.at_eof() and self.stderr.at_eof():
            self._stdio_finalizer()
        return result

    async def communicate(self, input=None):
        async def bounded_read(reader):
            parts, size = [], 0
            while part := await reader.read(65536):
                size += len(part)
                if size > 16 * 1024 * 1024:
                    raise RuntimeError('provider output exceeds its bound')
                parts.append(part)
            return b''.join(parts)

        async def send():
            try:
                if input:
                    self.stdin.write(input)
                    await self.stdin.drain()
                self.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(send())
                stdout = group.create_task(bounded_read(self.stdout))
                stderr = group.create_task(bounded_read(self.stderr))
            await self.wait()
            return stdout.result(), stderr.result()
        except BaseException:
            self.revoke()
            await self.wait()
            raise
        finally:
            self._stdio_finalizer()


def seal_launch_file(snapshot, stem, suffix, data: bytes) -> Path:
    """Add one launch file (an MCP config, a model catalog) to the sealed snapshot.

    The snapshot is the only daemon-written state a provider cell receives, so a
    file the CLI must read goes here, sealed for the owner exactly like its
    credentials, and is named in argv by its snapshot path.
    """
    import secrets

    from tinyassets.credential_vault import _write_exclusive_snapshot_file

    path = Path(snapshot) / f"{stem}-{secrets.token_hex(8)}{suffix}"
    _write_exclusive_snapshot_file(path, data)
    return path


def cell_path(snapshot, path) -> str:
    """Where ``path`` (a file of the launch snapshot) is inside the running cell.

    For a value the cell cannot rewrite itself, such as a path embedded in a
    ``key=value`` argument; a bare snapshot path in argv or env is rewritten.
    """
    from tinyassets.role_provider_cell import AUTH_DIR

    relative = Path(path).relative_to(Path(snapshot))
    return f"{AUTH_DIR}/{relative.as_posix()}"


async def spawn(argv, *, scope, shell, view, nested_sandbox, options):
    """No provider branches, caller mount policy or daemon subprocess fallback.

    The bound scope names the center, its sealed launch snapshot and, for a
    served turn, the owner's engine route; the cell reaches that route only
    through its pinned relay socket.
    """
    from tinyassets.broker import supervisor
    from tinyassets.role_provider_discovery import aspawn_cell

    if (scope is None or scope.universe_dir is None or scope.credential_dir is None
            or shell or view is not None or nested_sandbox
            or set(options) - {'env', 'cwd', 'stdin', 'stdout', 'stderr', 'limit'}
            or options.get('stdin') != asyncio.subprocess.PIPE
            or options.get('stdout') != asyncio.subprocess.PIPE
            or options.get('stderr') != asyncio.subprocess.PIPE):
        raise ProviderConfinementError('provider execution view is not admitted')
    center, snapshot = Path(scope.universe_dir), Path(scope.credential_dir)
    if options.get('cwd') is not None:
        raise ProviderConfinementError('provider execution cwd is not admitted')
    for value in argv:
        if (str(center.parent) in value and not (
                value == str(snapshot) or value.startswith(str(snapshot) + '/'))):
            raise ProviderConfinementError('provider execution host path is not admitted')
    supervisor._protect_daemon()
    return await aspawn_cell(argv, env=options.get('env') or {},
        view=SimpleNamespace(setenv=()), universe_dir=center, snapshot_dir=snapshot,
        limit=options.get('limit', 64 * 1024), execution=True,
        engine_route=scope.engine_route)

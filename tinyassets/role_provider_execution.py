"""Provider-neutral text execution selected at the sole shared spawn boundary."""
from __future__ import annotations

import asyncio
import weakref
from pathlib import Path
from types import SimpleNamespace

from tinyassets.providers.owned_process import OwnerCellProcess
from tinyassets.providers.provider_jail import ProviderConfinementError


class ExecutionProcess(OwnerCellProcess):
    """Async process shape with independent stderr and mapper-owned lifetime."""

    def __init__(self, cell, reader, writer, error_reader, error_writer):
        super().__init__(cell, reader, writer)
        self.stderr = error_reader
        self._error_writer = error_writer
        self._stdio_finalizer = weakref.finalize(self, self._close_stdio, writer, error_writer)

    @staticmethod
    def _close_stdio(*writers):
        for writer in writers:
            writer.close()

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
                self.stdin.write_eof()
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


async def spawn(argv, *, scope, shell, view, nested_sandbox, options):
    """No provider branches, caller mount policy or daemon subprocess fallback."""
    from tinyassets.broker import supervisor
    from tinyassets.role_provider_discovery import aspawn_cell

    if (scope is None or scope.universe_dir is None or scope.credential_dir is None
            or scope.engine_route is not None or shell or view is not None or nested_sandbox
            or set(options) - {'env', 'cwd', 'stdin', 'stdout', 'stderr', 'limit'}
            or options.get('stdin') != asyncio.subprocess.PIPE
            or options.get('stdout') != asyncio.subprocess.PIPE
            or options.get('stderr') != asyncio.subprocess.PIPE):
        raise ProviderConfinementError('provider execution view is not admitted')
    center, snapshot = Path(scope.universe_dir), Path(scope.credential_dir)
    cwd = options.get('cwd')
    if cwd is not None:
        raise ProviderConfinementError('provider execution cwd is not admitted')
    for value in argv:
        if (str(center.parent) in value and not (
                value == str(snapshot) or value.startswith(str(snapshot) + '/'))):
            raise ProviderConfinementError('provider execution host path is not admitted')
    supervisor._protect_daemon()
    return await aspawn_cell(argv, env=options.get('env') or {},
        view=SimpleNamespace(setenv=()), universe_dir=center, snapshot_dir=snapshot,
        limit=options.get('limit', 64 * 1024), execution=True)

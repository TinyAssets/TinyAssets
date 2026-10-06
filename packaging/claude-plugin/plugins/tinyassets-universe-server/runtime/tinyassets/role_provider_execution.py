"""Provider-neutral execution selected at the sole shared spawn boundary."""
from __future__ import annotations

import asyncio
import re
import weakref
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from tinyassets.providers.owned_process import OwnerCellProcess
from tinyassets.providers.provider_jail import ProviderConfinementError

_ENV_NAME = re.compile(r'[A-Z_][A-Z0-9_]{0,127}')
_STORE_NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}')
_RELATIVE = re.compile(r'[A-Za-z0-9._-]{1,128}(/[A-Za-z0-9._-]{1,128}){0,3}')


@dataclass(frozen=True, slots=True)
class CellView:
    """How one launch looks inside its owner's provider-exec cell (D88).

    The legacy jail never reads this; a selected broker requires it before it
    admits a caller view or working directory. Every field is data an adapter
    states about itself; none of it is a provider name the cell branches on.

    ``persistent``: ``/workspace`` is the owner's persistent provider workspace,
    and the launch's own center path (its cwd or an argv item equal to it) is
    presented there. Otherwise ``/workspace`` is private scratch.
    ``home``: an environment variable set to the private copy of the sealed
    launch snapshot. ``session``: ``(path under that home, store name)``; the
    path persists in the owner's session store ``/session/<store name>``.
    ``secret_fds``: ``(environment variable, snapshot file)`` pairs; the file's
    bytes arrive on an inherited pipe named by the variable, never in the
    environment, and that file is left out of the private home copy.
    """

    persistent: bool = False
    home: str | None = None
    session: tuple[str, str] | None = None
    secret_fds: tuple[tuple[str, str], ...] = ()

    def document(self):
        """The bounded, checked form that travels to the cell, or a refusal."""
        def relative(value):
            return (type(value) is str and _RELATIVE.fullmatch(value) is not None
                    and not any(part in ('.', '..') for part in value.split('/')))

        if (type(self.persistent) is not bool
                or (self.home is not None and (
                    type(self.home) is not str or not _ENV_NAME.fullmatch(self.home)))
                or (self.session is not None and (
                    self.home is None or not self.persistent
                    or type(self.session) is not tuple or len(self.session) != 2
                    or not relative(self.session[0])
                    or type(self.session[1]) is not str
                    or not _STORE_NAME.fullmatch(self.session[1])))
                or type(self.secret_fds) is not tuple or len(self.secret_fds) > 4
                or any(type(item) is not tuple or len(item) != 2
                       or type(item[0]) is not str or not _ENV_NAME.fullmatch(item[0])
                       or item[0] == self.home or not relative(item[1])
                       for item in self.secret_fds)
                or len({name for name, _ in self.secret_fds}) != len(self.secret_fds)):
            raise ProviderConfinementError('provider execution cell view is not admitted')
        return {'persistent': self.persistent, 'home': self.home,
                'session': None if self.session is None else list(self.session),
                'secret_fds': [list(item) for item in self.secret_fds]}


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


async def spawn(argv, *, scope, shell, view, nested_sandbox, options, cell_view=None):
    """No provider branches, caller mount policy or daemon subprocess fallback.

    A caller view (the legacy jail's mount list) is admitted only beside an
    explicit ``cell_view``, which replaces it; it is never translated. The only
    admitted working directory is the launch's own center, and only when the
    cell view presents it as the persistent owner workspace.
    """
    from tinyassets.broker import supervisor
    from tinyassets.role_provider_discovery import aspawn_cell

    if (scope is None or scope.universe_dir is None or scope.credential_dir is None
            or scope.engine_route is not None or shell or nested_sandbox
            or (view is not None and cell_view is None)
            or (cell_view is not None and type(cell_view) is not CellView)
            or set(options) - {'env', 'cwd', 'stdin', 'stdout', 'stderr', 'limit'}
            or options.get('stdin') != asyncio.subprocess.PIPE
            or options.get('stdout') != asyncio.subprocess.PIPE
            or options.get('stderr') != asyncio.subprocess.PIPE):
        raise ProviderConfinementError('provider execution view is not admitted')
    document = (cell_view or CellView()).document()
    center, snapshot = Path(scope.universe_dir), Path(scope.credential_dir)
    cwd = options.get('cwd')
    persistent = document['persistent']
    if cwd is not None and not (persistent and Path(cwd) == center):
        raise ProviderConfinementError('provider execution cwd is not admitted')
    if persistent:
        argv = ['/workspace' if value == str(center) else value for value in argv]
    for value in argv:
        if (str(center.parent) in value and not (
                value == str(snapshot) or value.startswith(str(snapshot) + '/'))):
            raise ProviderConfinementError('provider execution host path is not admitted')
    supervisor._protect_daemon()
    return await aspawn_cell(argv, env=options.get('env') or {},
        view=SimpleNamespace(setenv=()), universe_dir=center, snapshot_dir=snapshot,
        limit=options.get('limit', 64 * 1024), execution=True, cell_view=document)

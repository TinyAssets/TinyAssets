"""The one spawn point for every provider CLI: an owner cell, never a daemon child.

Every provider process -- a served turn, a workflow node's call, a metadata
read -- runs as its owner's uid inside that owner's ``provider-exec`` or
``provider-discovery`` cell (``tinyassets.role_provider_execution``,
``tinyassets.role_provider_discovery``). The bounded owner launcher (the
mapper) starts the cell, owns its lifetime and reports its exit. There is no
unconfined branch, no bubblewrap launched by the daemon and no Windows path:
a call that cannot be celled is refused with
:class:`~tinyassets.providers.provider_jail.ProviderConfinementError` before
anything runs.

The returned process has no local PID. Teardown is revocation through the
cell's authenticated lifetime channel, never a signal: the mapper ends the
cell's whole PID namespace, which is what used to need the family anchor.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import subprocess
import sys

logger = logging.getLogger(__name__)


class CellStdin:
    """Pipe-like stdin over a duplex socket: close only the writing half."""

    def __init__(self, writer):
        self._writer = writer
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._writer, name)

    def close(self):
        if not self._closed:
            self._closed = True
            self._writer.write_eof()

    def is_closing(self):
        return self._closed or self._writer.is_closing()

    async def wait_closed(self):
        # A half-close has no transport-close handshake to await.
        return


class FamilyAnchorError(RuntimeError):
    """A provider process could not be given an owner for its whole family.

    Kept as the name callers catch: a cell that fails to start is reported as
    this, so no adapter treats it as evidence about the provider."""


def no_window_kwargs() -> dict:
    """Subprocess kwargs that suppress the console window on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


async def aspawn_owned(
    cmd,
    *,
    shell: bool = False,
    universe_view=None,
    install_mounts=None,
    require_confinement: bool = True,
    nested_sandbox=False,
    **kwargs,
):
    """Start ``cmd`` in its owner's provider-exec cell and return the process.

    The owner, center and launch snapshot come from the bound
    :func:`~tinyassets.providers.provider_jail.provider_launch_scope`, never
    from the caller. ``universe_view``, ``shell`` and ``nested_sandbox`` are
    refused: the cell decides what exists inside it. ``install_mounts`` is
    accepted and ignored; the cell binds every shipped install tree.
    """
    del install_mounts, require_confinement
    from tinyassets.providers.provider_jail import _SCOPE
    from tinyassets.role_provider_execution import spawn

    return await spawn(list(cmd), scope=_SCOPE.get(), shell=shell, view=universe_view,
                       nested_sandbox=nested_sandbox, options=kwargs)


def disk_stop_note(proc) -> str:
    """Authenticated cell stop diagnostics, shared by every CLI adapter."""
    if isinstance(proc, OwnerCellProcess):
        reason = getattr(proc.cell, 'stop_reason', None)
        if reason:
            caller = getattr(proc.cell, 'revoke_caller', None)
            if reason == 'revoked' and caller:
                reason += f' by {caller}'
            return f' (owner cell: {reason})'
        if proc.returncode is not None and proc.returncode < 0:
            try:
                name = signal.Signals(-proc.returncode).name
            except ValueError:
                name = str(-proc.returncode)
            return f' (owner cell: signal {name}; no mapper kill recorded)'
    return ""


class OwnerCellProcess:
    """Stdio shim over a mapper-owned owner cell (D82); it has no local PID.

    Teardown is revocation through the authenticated lifetime channel, and the
    mapper's identity-checked receipt is the only completion fact.
    """

    pid = None

    def __init__(self, cell, reader, writer):
        self.cell, self.stdout, self.stdin = cell, CellOutput(self, reader), CellStdin(writer)
        self._transport = writer.transport
        self.returncode = None
        self._waiter = None

    def revoke(self) -> None:
        self.cell.revoke()

    def kill(self) -> None:
        self.revoke()

    terminate = kill

    async def wait(self, timeout=5):
        if self._waiter is None:
            def reap():
                # One thread owns every receipt read and the final close, even
                # if the caller is cancelled while awaiting it.
                try:
                    return self.cell.wait(timeout)
                finally:
                    self.cell.close()
            self._waiter = asyncio.create_task(asyncio.to_thread(reap))
        self.returncode = await asyncio.shield(self._waiter)
        return self.returncode


class CellOutput:
    """Collect the independent lifetime reason before surfacing a stream reset."""

    def __init__(self, process, reader):
        self.process, self.reader = process, reader

    def __getattr__(self, name):
        return getattr(self.reader, name)

    async def _read(self, method, *args):
        try:
            result = await getattr(self.reader, method)(*args)
            if not result and not (method == 'read' and args == (0,)):
                code = await self.process.wait()
                reason = getattr(self.process.cell, 'stop_reason', '') or ''
                if code and reason.startswith(('decoder:', 'launcher:', 'bwrap:', 'relay:')):
                    from tinyassets.exceptions import ProviderError

                    message = 'provider cell ended' + disk_stop_note(self.process)
                    logger.error('%s', message)
                    raise ProviderError(message)
            return result
        except (ConnectionResetError, BrokenPipeError) as exc:
            from tinyassets.exceptions import ProviderError

            try:
                await asyncio.wait_for(self.process.wait(5), 6)
                reason = disk_stop_note(self.process)
            except (OSError, RuntimeError, TimeoutError):
                reason = ' (owner cell: completion unavailable)'
            message = 'provider cell stream failed' + reason
            logger.error('%s', message)
            raise ProviderError(message) from exc

    async def readline(self):
        return await self._read('readline')

    async def read(self, size=-1):
        return await self._read('read', size)


def kill_owned_tree(proc) -> None:
    """Synchronously end ``proc`` and everything it started.

    An owner cell is revoked; its mapper ends the cell's PID namespace. Anything
    else (a test double) is killed individually.
    """
    if isinstance(proc, OwnerCellProcess):
        proc.revoke()  # Never a signal: the shim has no PID of its own.
        return
    if getattr(proc, "returncode", None) is None:
        with contextlib.suppress(ProcessLookupError, OSError, AttributeError):
            proc.kill()


async def akill_owned_tree(proc) -> None:
    """:func:`kill_owned_tree`, usable from an async cancellation path."""
    kill_owned_tree(proc)

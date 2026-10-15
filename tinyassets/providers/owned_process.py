"""The one spawn point for every provider CLI: the admitted owner box.

Every provider process -- a served turn, a workflow node's call, a metadata
read -- is an exec on the session's box handle through the same
:class:`~tinyassets.boxes.cli_launcher.BoxLauncher` the CLI packages use. The
credential bytes for the run come from the platform-side launch snapshot and
are leased into the box as files for that one run; no secret crosses in the
environment, and nothing runs on the daemon host. A call with no admitted
binding is refused with :class:`~tinyassets.exceptions.ProviderConfinementError`
before anything runs.

The returned process has no local PID. Ending it is a cancellation of the
execution in the box; the host's receipt is the only completion fact.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import stat
import subprocess
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_LEASE_FILE = 8 * 1024 * 1024


@dataclass(frozen=True)
class LaunchScope:
    universe_dir: Path
    owner: str
    service: str | None
    credential_dir: Path | None
    operation_id: str
    #: The executor's declared lease shape (``BaseProvider.native_launch_auth``):
    #: ``home_env``, ``home_dir``, optionally ``token_env`` and ``token_prefix``
    #: for a raw token leased as a file and exported inside the box only.
    auth: dict | None = None


_SCOPE: ContextVar[LaunchScope | None] = ContextVar("provider_launch_scope", default=None)


@contextlib.contextmanager
def launch_scope(universe_dir, *, owner, service, credential_dir, operation_id, auth=None):
    """Bind the launch's owner, custody service and one-run lease for this call."""
    scope = LaunchScope(Path(universe_dir), owner or "", service or None,
                        Path(credential_dir) if credential_dir is not None else None,
                        operation_id, dict(auth) if auth else None)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def current_launch_scope() -> LaunchScope | None:
    return _SCOPE.get()


def guest_job_dir(operation_id: str) -> str:
    """The box path the leased home lands under for ``operation_id``'s launch."""
    import hashlib

    return "/tmp/ta-agent-" + hashlib.sha256(operation_id.encode()).hexdigest()[:32]


class FamilyAnchorError(RuntimeError):
    """A provider process could not be started in its owner's box."""


def no_window_kwargs() -> dict:
    """Subprocess kwargs that suppress the console window on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _lease_files(credential_dir: Path) -> dict[str, bytes]:
    """Every regular file of the one-run snapshot, read no-follow and bounded."""
    files = {}
    for entry in sorted(os.scandir(credential_dir), key=lambda e: e.name):
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            raise FamilyAnchorError("launch snapshot holds a non-regular entry")
        fd = os.open(entry.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_LEASE_FILE:
                raise FamilyAnchorError("launch snapshot file exceeds its bound")
            data = os.read(fd, info.st_size + 1)
        finally:
            os.close(fd)
        if data:
            files[entry.name] = data
    if not files:
        raise FamilyAnchorError("launch snapshot is empty")
    return files


def _auth_spec(service: str, declared: dict, files: dict[str, bytes]) -> dict:
    spec = {"service": service, "home_env": declared["home_env"],
            "home_dir": declared.get("home_dir") or service,
            "files": sorted(files), "writeback": [], "env_from_file": {}}
    token_env, prefix = declared.get("token_env"), declared.get("token_prefix")
    auth = files.get("auth.json", b"")
    if token_env and prefix and auth.strip().startswith(prefix.encode()):
        spec["env_from_file"] = {token_env: "auth.json"}
    return spec


def _secret_env_keys(auth: dict) -> frozenset[str]:
    from tinyassets.providers.base import _PROVIDER_AUTH_OVERLAY_ENV_VARS

    names = {name for group in _PROVIDER_AUTH_OVERLAY_ENV_VARS.values() for name in group}
    names.add(auth["home_env"])
    names.update(auth["env_from_file"])
    return frozenset(names)


async def aspawn_owned(cmd, *, shell=False, env=None, universe_view=None, install_mounts=None,
                       require_confinement=True, nested_sandbox=False, **kwargs):
    """Start ``cmd`` in the session's admitted box and return its process.

    The owner, service and one-run lease come from the bound
    :func:`launch_scope`; the handle from the session binding. ``shell``,
    ``universe_view`` and ``nested_sandbox`` are refused: the box decides what
    exists inside it. ``install_mounts`` and subprocess kwargs are ignored; the
    box binds every shipped install tree and owns the pipes.
    """
    del install_mounts, require_confinement, kwargs
    from tinyassets.control_plane.binding import current_binding
    from tinyassets.control_plane.native_launch import launcher
    from tinyassets.exceptions import ProviderConfinementError
    from tinyassets.external_agents.home import box_custody

    if shell or universe_view is not None or nested_sandbox:
        raise ProviderConfinementError(ProviderConfinementError.MESSAGE
                                       + ": no shell, view or nested box")
    scope, binding = _SCOPE.get(), current_binding()
    if (scope is None or binding is None or not scope.service or scope.credential_dir is None
            or not scope.auth or not scope.auth.get("home_env")):
        raise ProviderConfinementError(ProviderConfinementError.MESSAGE
                                       + ": no admitted session binding")
    binding.validate(scope.owner, scope.universe_dir)
    custody = box_custody(scope.universe_dir, scope.owner, scope.service, binding)
    if custody is None:
        raise ProviderConfinementError(ProviderConfinementError.MESSAGE
                                       + ": no current credential custody")
    files = await asyncio.to_thread(_lease_files, scope.credential_dir)
    auth = _auth_spec(scope.service, scope.auth, files)
    from tinyassets.broker.ledger_queries import selected_git_connections

    connections = await asyncio.to_thread(
        selected_git_connections, scope.universe_dir.parent, principal=scope.owner,
        command_center=scope.universe_dir.name)
    launch = launcher(custody, binding=binding, operation_id=scope.operation_id,
                      connections=connections)
    secret = _secret_env_keys(auth)
    clean = {key: value for key, value in (env or {}).items()
             if key not in secret and isinstance(value, str)}
    clean[auth["home_env"]] = "/agent-home/" + auth["home_dir"]
    argv = [str(part) for part in cmd]
    try:
        proc = await launch.spawn(
            argv, clean, home="/agent-home", workdir="/agent-home/jobs/" + secrets.token_hex(16),
            egress=scope.universe_dir.name, auth_files=files, auth=auth,
        )
    except Exception as exc:
        raise FamilyAnchorError(f"provider launch refused: {type(exc).__name__}") from exc
    return OwnedBoxProcess(proc, launch)


class OwnedBoxProcess:
    """The box-side CLI process with the stdio shape every adapter reads.

    ``wait`` settles the execution with the host (its result and acknowledgement)
    exactly once; ``kill`` cancels it in the box. There is no local PID.
    """

    pid = None

    def __init__(self, process, launch):
        self._process, self._launch = process, launch
        self.stdin, self.stdout, self.stderr = process.stdin, process.stdout, process.stderr
        self._settling = None
        self._settled = False

    @property
    def returncode(self):
        return self._process.returncode

    @property
    def workdir(self):
        return self._process.workdir

    async def wait(self, timeout=None):
        del timeout
        code = await self._process.wait()
        if self._settling is None:
            self._settling = asyncio.ensure_future(self._settle())
        await asyncio.shield(self._settling)
        return code

    async def _settle(self):
        try:
            await self._launch.close(self._process)
        except Exception:  # noqa: BLE001 - a lost writeback never hides the exit
            logger.warning("provider execution result was not collected", exc_info=True)
        else:
            with contextlib.suppress(Exception):
                await self._launch.acknowledge(self._process)
        self._settled = True

    async def communicate(self, input=None):
        if input:
            self.stdin.write(input)
            await self.stdin.drain()
        self.stdin.close()
        output = await self.stdout.read()
        await self.wait()
        return output, b""

    async def cancel(self):
        await self._process.cancel()

    def kill(self):
        if self.returncode is None:
            with contextlib.suppress(Exception):
                self._process.provider.cancel(self._process.handle, self._process.ident)

    terminate = kill


def stderr_excerpt(stderr_text: str, limit: int = 400) -> str:
    """The TAIL of a CLI's stderr, bounded: its last lines say why it exited."""
    text = stderr_text.strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def disk_stop_note(proc) -> str:
    """Kept for the adapters' messages; the box reports no stop reason."""
    del proc
    return ""


def kill_owned_tree(proc) -> None:
    """Synchronously end ``proc``: a box execution is cancelled in its box."""
    if getattr(proc, "returncode", None) is None:
        with contextlib.suppress(ProcessLookupError, OSError, AttributeError):
            proc.kill()


async def akill_owned_tree(proc) -> None:
    """:func:`kill_owned_tree`, usable from an async cancellation path."""
    if isinstance(proc, OwnedBoxProcess):
        if proc.returncode is None:
            with contextlib.suppress(Exception):
                await proc.cancel()
        return
    kill_owned_tree(proc)


async def cell_heartbeat(proc) -> bool:
    """A box execution has no liveness beyond its output; never extend a wait."""
    del proc
    return False

"""Provider-neutral ownership of a spawned CLI process and its descendants.

A provider adapter that kills only the direct child leaves the work that child
started running unowned: the Windows ``.cmd`` shim's real CLI, the engine-MCP
server the CLI spawns, a bubblewrap payload. This module gives both adapters
one way to (a) spawn a CLI as an *owned family* and (b) end that family --
without ever signalling a process-group integer that could name a stranger.

Why the obvious shape is wrong
------------------------------
The tempting fix is ``start_new_session=True`` at spawn plus
``os.killpg(recorded_pgid, SIGKILL)`` at teardown. It is unsafe once the leader
has been reaped, and no refinement rescues it:

* **Linux v6.1 ``kernel/pid.c``**: ``__change_pid()`` (~315-331) calls
  ``free_pid()`` as soon as a pid has no remaining task-list attachments;
  ``free_pid()`` (~118-146) removes the numeric ID from the IDR at ~142 and
  only then queues ``delayed_put_pid``; ``put_pid()`` (~98-110) frees the
  ``struct pid`` *object* separately. **ID release and object release are
  decoupled.**
* Therefore holding a ``pidfd`` on the leader does **not** reserve the number --
  it keeps the object. ``pidfd_open`` + ``killpg`` is **not** race-free.
* Therefore an ``os.getpgid(pid)`` probe before ``killpg`` is **not** race-free
  either: it is a plain TOCTOU window, narrowed, never closed.
* ``pidfd_send_signal(..., PIDFD_SIGNAL_PROCESS_GROUP)`` landed in Linux 6.9.
  The deployment kernel reads ``6.1.0-52-amd64``. Unavailable.

What the same function *does* guarantee: ``__change_pid()`` frees a pid only
when **every** task list for it is empty, and a live process whose
``PIDTYPE_PGID`` is ``P`` is one such attachment. So **while a live member
remains in group P, P cannot be reallocated.**

The family anchor
-----------------
That guarantee is bought by keeping a live member in the group. The daemon is
multithreaded, so it must not ``fork`` (see the ``preexec_fn`` ban in
``node_sandbox.py``). Instead it spawns **one** process -- a *fresh,
single-threaded* Python interpreter running :data:`_ANCHOR_WRAPPER_SRC` -- in
its own session. Forking inside that fresh interpreter is not the banned
pattern: it is neither the daemon nor multithreaded. "Single-threaded" is
*enforced* by :data:`_WRAPPER_FLAGS` (``-I -S``), not assumed of the daemon's
environment: no inherited ``PYTHONPATH``, ``sitecustomize`` or user site hook
gets to run -- and so cannot start a thread -- before the ``fork``.

The wrapper forks a tiny **anchor**, waits for it to report established, then
``execvp``s the original argv **in the wrapper's own pid**. The CLI therefore
keeps the leader pid, the argv, the env, the cwd and the stdio the adapter
asked for -- ``exec`` changes none of them. The anchor drops the CLI's stdio
(so the daemon still sees EOF when the CLI exits) and blocks on a control pipe
whose write end the daemon holds.

Teardown is **a pipe write, not a signal**. The daemon writes ``K`` and closes
the control pipe; the anchor treats any read result -- a command byte *or* EOF
-- as "end the family" and calls ``os.killpg(os.getpgrp(), SIGKILL)``: its
**own** group, while it is itself alive and in it. No stale-ID lookup happens
anywhere, in either process. EOF also covers daemon death and a ``Process``
garbage-collected without teardown, via :mod:`weakref` finalization, so an
anchor or a control fd cannot leak per turn.

**Fail closed.** If the anchor cannot be established, the half-spawned family
is torn down and :class:`FamilyAnchorError` is raised. There is no degraded
mode that group-signals without an anchor, and a process that never went
through :func:`aspawn_owned` is only ever killed individually.

**Windows keeps exactly the behaviour it had.** There is no process group to
reserve and no anchor. Teardown stays a bounded best-effort ``taskkill /F /T``
tree walk, run only while we still hold a live child so it cannot reach a
recycled pid. It is explicitly **not** a Job Object: a descendant already
reparented, or spawned in the window between the walk and the kill, survives
it. The correct Windows fix is a Job Object with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and is a separate, larger slice.

Not fixed here, on purpose: a descendant that deliberately ``setsid``s out of
the group (only cgroup v2 ``cgroup.kill`` or a pid namespace reaches that), and
``node_sandbox``'s jail path, which already has the stronger pid-namespace
mechanism and is untouched.

Confinement
-----------
:func:`aspawn_owned` is also where a provider launch made for a universe is
jailed (:mod:`tinyassets.providers.provider_jail`). The jail is bubblewrap
with its own pid namespace and ``--die-with-parent``, and bwrap is what the
wrapper execs, so the anchor still owns the family: ending the group ends
bwrap, and the jail's namespace dies with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import shlex
import subprocess
import sys
import weakref
from weakref import WeakKeyDictionary

logger = logging.getLogger(__name__)

#: Ceiling on the Windows tree walk. Teardown runs on the deadline path, so an
#: unresponsive ``taskkill`` must not extend the turn it is ending.
_TREE_KILL_TIMEOUT_S = 5

#: How long the daemon waits for the anchor's readiness line. Generous: the
#: work in between is one ``fork`` and three tiny writes, so anything near this
#: bound is a real failure, not slowness. Exceeding it fails the spawn closed.
_ANCHOR_READY_TIMEOUT_S = 15.0

#: Families this module owns, keyed weakly so an entry cannot outlive the
#: ``Process`` it belongs to. Teardown consumes the entry exactly once.
_OWNED_FAMILIES: "WeakKeyDictionary[object, _Family]" = WeakKeyDictionary()


class FamilyAnchorError(RuntimeError):
    """The owned-family anchor could not be established.

    Raised only after the half-spawned family has been torn down, so nothing
    un-ownable is ever handed back to a caller.
    """


# --- the wrapper that runs in a fresh, single-threaded interpreter ----------
#
# Passed to ``python -c`` as one argv element -- never through a shell, so no
# interpolation of any kind happens to it or to the CLI argv that follows it.
# Uses only ``os``/``sys``/``signal`` primitives after the fork: no allocator-
# heavy work, no logging, no atexit, and ``os._exit`` on every exit path.
_ANCHOR_WRAPPER_SRC = r'''
import os
import signal
import sys


def _resolve(argv0):
    """The exec target, or None. Keeps "binary missing" a spawn-time error."""
    if os.sep in argv0 or (os.altsep and os.altsep in argv0):
        return argv0 if os.access(argv0, os.X_OK) else None
    import shutil
    return shutil.which(argv0)


def _report(ready_w, text):
    try:
        os.write(ready_w, text.encode("ascii", "replace") + b"\n")
    except OSError:
        pass


def _fail(ready_w, text, code):
    _report(ready_w, text)
    os._exit(code)


def _anchor(ctrl_r, ready_w, gate_r, gate_w, leader):
    """The live group member. Runs in the forked child and never returns."""
    try:
        os.close(gate_r)
        # Never hold the CLI's stdio: if this process kept fd 1 or 2, the
        # daemon would not see EOF when the CLI exits and every turn would
        # block on its own reaper.
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        if null > 2:
            os.close(null)
        pgid = os.getpgrp()
        if pgid != leader:
            # Not the group we were told to anchor. Signal NOTHING.
            os._exit(70)
        os.write(ready_w, b"ANCHOR " + str(os.getpid()).encode()
                 + b" " + str(pgid).encode() + b"\n")
        os.close(ready_w)
        # Only now may the wrapper exec the CLI: no CLI work before the family
        # is anchored.
        os.write(gate_w, b"A")
        os.close(gate_w)
    except BaseException:
        os._exit(70)
    # A command byte from the daemon, or EOF because the daemon closed the pipe
    # or died. Both mean the same thing.
    while True:
        try:
            os.read(ctrl_r, 1)
        except InterruptedError:
            continue
        except OSError:
            pass
        break
    # Our OWN group, while we are alive and in it: the id cannot be stale and
    # cannot name a stranger. No getpgid, no recorded integer.
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except OSError:
        pass
    os._exit(0)


def _main():
    ctrl_r = int(sys.argv[1])
    ready_w = int(sys.argv[2])
    argv = sys.argv[4:]  # sys.argv[3] is the literal "--" separator
    if not argv:
        _fail(ready_w, "ERR argv", 64)
    if _resolve(argv[0]) is None:
        _fail(ready_w, "ERR enoent", 127)
    leader = os.getpid()
    gate_r, gate_w = os.pipe()
    pid = -1
    try:
        pid = os.fork()
    except OSError as exc:
        _fail(ready_w, "ERR fork " + str(exc.errno), 71)
    if pid == 0:
        _anchor(ctrl_r, ready_w, gate_r, gate_w, leader)
    os.close(gate_w)
    try:
        ack = os.read(gate_r, 1)
    except OSError:
        ack = b""
    os.close(gate_r)
    # The CLI must inherit NO control handle.
    os.close(ctrl_r)
    os.close(ready_w)
    if ack != b"A":
        os._exit(71)
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        try:
            os.write(2, b"tinyassets-anchor: exec failed: "
                     + str(exc).encode("ascii", "replace") + b"\n")
        except OSError:
            pass
        os._exit(127)


_main()
'''


class _Family:
    """One owned process family and the single handle that ends it."""

    __slots__ = ("kind", "pgid", "anchor_pid", "_ctrl_w", "_ended")

    def __init__(self, kind: str, pgid: int, anchor_pid=None, ctrl_w=None):
        self.kind = kind
        self.pgid = pgid
        self.anchor_pid = anchor_pid
        self._ctrl_w = ctrl_w
        self._ended = False

    def end(self) -> None:
        """Tell the anchor to end the family. Idempotent, synchronous, safe at GC.

        Writing is best effort and closing is what actually guarantees delivery:
        the anchor acts on EOF exactly as it acts on the command byte, so a
        write that fails (anchor already gone, pipe already broken) changes
        nothing. Never raises -- it runs from ``finally`` blocks and from
        :mod:`weakref` finalization.
        """
        if self._ended:
            return
        self._ended = True
        fd, self._ctrl_w = self._ctrl_w, None
        if fd is None:
            return
        with contextlib.suppress(OSError):
            os.write(fd, b"K")
        with contextlib.suppress(OSError):
            os.close(fd)


def no_window_kwargs() -> dict:
    """Subprocess kwargs that suppress the console window on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def owned_spawn_kwargs() -> dict:
    """Spawn kwargs for the process this adapter will own.

    On POSIX, ``start_new_session`` makes the spawned process a session and
    group leader, so its pid *is* the group id and every descendant it starts
    lands in that group unless it deliberately leaves. Windows keeps exactly
    the kwargs it had: hidden console window, no new process group, because its
    teardown is a bounded tree walk rather than a group signal.
    """
    kwargs = no_window_kwargs()
    if os.name == "posix":
        kwargs["start_new_session"] = True
    return kwargs


def mark_owned(proc, family: "_Family | None" = None) -> None:
    """Record how ``proc``'s family is to be ended.

    With no ``family``, this records the **weakest** safe claim for the
    platform: a bounded ``taskkill`` tree walk on Windows, and direct-child-only
    on POSIX -- because on POSIX, group signalling authority comes from a live
    anchor and nothing else. That is the deliberate absence of a broad-signal
    fallback, not an oversight.

    Registration failures are non-fatal: an unregistered process is killed
    individually, which is the pre-existing behaviour.
    """
    if family is None:
        pid = getattr(proc, "pid", None)
        # ``isinstance(True, int)`` is True, so reject bools explicitly; a
        # Mock's auto-attribute is not an int at all and is rejected here.
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return
        kind = "windows-tree" if os.name != "posix" else "direct"
        family = _Family(kind=kind, pgid=pid)
    try:
        _OWNED_FAMILIES[proc] = family
    except TypeError:
        # Unhashable or non-weakref-able stand-in: stay unregistered, stay safe.
        if family.kind == "anchor":
            family.end()
        return
    if family.kind == "anchor":
        # A ``Process`` dropped without teardown -- a cancellation that never
        # reached a ``finally``, a legacy success path -- must still close the
        # control fd, because closing it IS the cleanup signal.
        with contextlib.suppress(TypeError):
            weakref.finalize(proc, family.end)


def owned_group_id(proc) -> int | None:
    """The group id recorded for ``proc`` at spawn, or ``None`` if unregistered."""
    family = _get_family(proc)
    return None if family is None else family.pgid


def anchor_pid(proc) -> int | None:
    """The live anchor's pid, or ``None`` when this family has no anchor."""
    family = _get_family(proc)
    return None if family is None else family.anchor_pid


def is_family_anchored(proc) -> bool:
    """True while ``proc``'s family is held by a live anchor."""
    family = _get_family(proc)
    return family is not None and family.kind == "anchor"


def _get_family(proc) -> "_Family | None":
    try:
        return _OWNED_FAMILIES.get(proc)
    except TypeError:
        return None


def _take_family(proc) -> "_Family | None":
    """Consume teardown authority before cleanup can be entered a second time."""
    try:
        return _OWNED_FAMILIES.pop(proc, None)
    except TypeError:
        return None


def _close_fd(fd) -> None:
    if fd is None or fd < 0:
        return
    with contextlib.suppress(OSError):
        os.close(fd)


def _kill_direct(proc) -> None:
    """Kill just this process, tolerating one that already exited or is a mock.

    Deliberately narrow: one process, never a group and never a tree. It does
    **not** rest on a claim that the pid is reserved -- holding a ``Process``
    object is not proof of a numeric reservation (the ``free_pid()`` note in
    this module's header is exactly why ID release and object release must not
    be conflated). It guards on ``returncode is None`` and otherwise relies on
    the runtime's own ``Process.kill`` handling of an already-exited child. The
    blast radius of being wrong is one signal to one pid, which is why nothing
    broader is ever sent from here.
    """
    with contextlib.suppress(Exception):  # noqa: BLE001 - already exited / stand-in
        if getattr(proc, "returncode", None) is None:
            proc.kill()


# --- spawning ---------------------------------------------------------------


def _shell_argv(cmd) -> list[str]:
    """What ``create_subprocess_shell`` would have exec'd on POSIX.

    ``/bin/sh -c <line>`` is literally what ``Popen(..., shell=True)`` runs, so
    routing it through the wrapper preserves the existing contract exactly. The
    line is the caller's own ``shlex.join`` output; this adds no interpolation.
    """
    return ["/bin/sh", "-c", shlex.join(cmd)]


async def _await_anchor_ready(ready_r: int, timeout: float) -> bytes:
    """Read the anchor's one-line status, bounded, without blocking the loop."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    buffer = bytearray()

    def _on_readable() -> None:
        try:
            chunk = os.read(ready_r, 256)
        except BlockingIOError:
            return
        except OSError as exc:  # pragma: no cover - pipe torn down under us
            if not future.done():
                future.set_exception(exc)
            return
        if not chunk:  # EOF before a status line: the wrapper died silently.
            if not future.done():
                future.set_result(bytes(buffer))
            return
        buffer.extend(chunk)
        if b"\n" in buffer and not future.done():
            future.set_result(bytes(buffer))

    os.set_blocking(ready_r, False)
    loop.add_reader(ready_r, _on_readable)
    try:
        return await asyncio.wait_for(future, timeout)
    finally:
        with contextlib.suppress(Exception):  # noqa: BLE001
            loop.remove_reader(ready_r)


#: Interpreter flags for the wrapper. ``-I`` (isolated) implies ``-E`` (ignore
#: ``PYTHON*`` env vars), ``-s`` (no user site-dir) and ``-P`` (do not prepend
#: cwd to ``sys.path``); ``-S`` skips :mod:`site` entirely so neither
#: ``sitecustomize`` nor ``usercustomize`` can execute. Together they are the
#: single-threaded-before-fork contract: an inherited ``PYTHONPATH``,
#: ``PYTHONSTARTUP`` or a site hook that starts a thread cannot run in this
#: interpreter, so "fresh interpreter" is enforced rather than assumed.
#:
#: They do **not** touch ``os.environ``, the cwd or the argv the adapter built:
#: ``-E``/``-s`` only change how *this* interpreter configures itself, and
#: ``execvp`` hands the CLI the environment and working directory unchanged.
_WRAPPER_FLAGS = ("-I", "-S")


def _wrapper_argv(ctrl_r: int, ready_w: int, argv: list[str]) -> list[str]:
    """The exact argv the anchored spawn execs. Pure, so it is directly testable."""
    return [
        sys.executable, *_WRAPPER_FLAGS, "-c", _ANCHOR_WRAPPER_SRC,
        str(ctrl_r), str(ready_w), "--", *argv,
    ]


class _FdBag:
    """Descriptors owned by one spawn attempt, each closed exactly once.

    The bug this exists to make unrepresentable: the old code closed
    ``ready_r`` after the handshake and then, on a bad status line, called a
    teardown helper that closed it again. A second close can land on a
    descriptor the runtime has since handed to something else.
    """

    __slots__ = ("_fds",)

    def __init__(self) -> None:
        self._fds: dict[str, int] = {}

    def add(self, name: str, fd: int) -> None:
        self._fds[name] = fd

    def close(self, name: str) -> None:
        """Close one descriptor if we still own it; a no-op afterwards."""
        _close_fd(self._fds.pop(name, None))

    def take(self, name: str):
        """Hand ownership out. This bag will never close it."""
        return self._fds.pop(name, None)

    def close_all(self) -> None:
        """Release everything still owned. Safe to call more than once."""
        while self._fds:
            _close_fd(self._fds.popitem()[1])


def _parse_anchor_ready(first_line: bytes, leader_pid: int) -> int:
    """The anchor's pid, or raise ``ValueError`` for anything not exactly right.

    Strict on purpose. A readiness line we cannot fully parse means we do not
    know that a live member is holding the group, and "we do not know" must
    fail closed -- never register a family with ``anchor_pid=None`` and act as
    though it were anchored.
    """
    fields = first_line.split()
    if len(fields) != 3 or fields[0] != b"ANCHOR":
        raise ValueError("status is not a three-field ANCHOR line")
    try:
        reported_anchor = int(fields[1])
        reported_pgid = int(fields[2])
    except ValueError:
        raise ValueError("anchor pid/pgid are not integers") from None
    if reported_anchor <= 0:
        raise ValueError("anchor pid is not positive")
    if reported_pgid != leader_pid:
        raise ValueError(
            f"anchor reports pgid {reported_pgid}, not the leader {leader_pid}"
        )
    if reported_anchor == leader_pid:
        # A fork always yields a distinct pid; equality means the line did not
        # come from the anchor we think it did.
        raise ValueError("anchor pid equals the leader pid")
    return reported_anchor


async def _aspawn_anchored(argv: list[str], *, extra_fds=(), **kwargs):
    """POSIX spawn: wrapper + anchor + control pipe, or nothing at all.

    ``extra_fds`` are inherited by the command as well (the jail's seccomp
    filter); the caller owns and closes them.
    """
    bag = _FdBag()
    try:
        ctrl_r, ctrl_w = os.pipe()
        bag.add("ctrl_r", ctrl_r)
        bag.add("ctrl_w", ctrl_w)
        ready_r, ready_w = os.pipe()
        bag.add("ready_r", ready_r)
        bag.add("ready_w", ready_w)
    except BaseException:
        # The second allocation can fail (EMFILE) with the first pair already
        # taken. Leaking it would be per-attempt and self-amplifying: every
        # failed spawn would make the next one likelier to fail.
        bag.close_all()
        raise
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *_wrapper_argv(ctrl_r, ready_w, argv),
            pass_fds=(ctrl_r, ready_w, *extra_fds),
            **owned_spawn_kwargs(),
            **kwargs,
        )
    finally:
        # The child's ends belong to the child. A copy left open here would
        # keep the anchor from ever seeing EOF, and would leak two fds a turn.
        bag.close("ctrl_r")
        bag.close("ready_w")
        if proc is None:
            # Nothing was spawned (missing interpreter, bad cwd): the caller's
            # own exception propagates, but no descriptor survives it.
            bag.close_all()

    def _abandon() -> None:
        # Closing the control pipe IS the kill: the anchor reaps its own group
        # on EOF. Synchronous on purpose -- this runs on the cancellation path,
        # where a further await can be cancelled again before it completes.
        bag.close_all()
        _kill_direct(proc)

    try:
        status = await _await_anchor_ready(ready_r, _ANCHOR_READY_TIMEOUT_S)
    except (TimeoutError, asyncio.TimeoutError):
        _abandon()
        raise FamilyAnchorError(
            "provider family anchor did not report ready within "
            f"{_ANCHOR_READY_TIMEOUT_S}s; refusing to expose an unowned CLI"
        ) from None
    except BaseException:
        # Caller cancellation before the handshake, or a torn pipe. No CLI work
        # has been exposed and the family dies with the control pipe.
        _abandon()
        raise
    bag.close("ready_r")

    first_line = status.split(b"\n", 1)[0]
    if first_line.split()[:2] == [b"ERR", b"enoent"]:
        _abandon()
        # Keep "the CLI is not installed" the same exception the direct spawn
        # raised, rather than a new wrapper-shaped failure.
        raise FileNotFoundError(
            errno.ENOENT, os.strerror(errno.ENOENT), argv[0],
        )
    try:
        recorded_anchor_pid = _parse_anchor_ready(first_line, proc.pid)
    except ValueError as exc:
        _abandon()
        raise FamilyAnchorError(
            "provider family anchor failed to establish "
            f"({exc}; status {first_line[:80]!r}); refusing to expose an "
            "unowned CLI"
        ) from None
    mark_owned(proc, _Family(
        kind="anchor", pgid=proc.pid,
        anchor_pid=recorded_anchor_pid, ctrl_w=bag.take("ctrl_w"),
    ))
    # Anything still held here (nothing, by construction) is ours to release.
    bag.close_all()
    return proc


async def aspawn_owned(
    cmd,
    *,
    shell: bool = False,
    universe_view=None,
    install_mounts=None,
    nested_sandbox=False,
    **kwargs,
):
    """Spawn ``cmd`` as an owned family and return the ``asyncio`` process.

    ``cmd`` is the argv list the adapter built. ``shell=True`` reproduces the
    old ``create_subprocess_shell(shlex.join(cmd))`` call exactly -- the
    Windows ``.cmd`` shim path -- on both platforms.

    **Confinement comes first.** When the call runs inside a
    :func:`~tinyassets.providers.provider_jail.provider_launch_scope` (the
    router binds one around every provider call), the argv is wrapped in the
    owning universe's bubblewrap jail before anything is spawned, or the launch
    is refused with :class:`~tinyassets.providers.provider_jail.ProviderConfinementError`.
    This is the one place every provider CLI passes through, so an adapter
    inherits the jail without code of its own. ``universe_view`` lets an
    adapter narrow what the universe looks like inside the jail;
    ``install_mounts`` is a callable naming install trees the generic command
    resolution cannot see (a wrapper script that execs a binary elsewhere).
    ``nested_sandbox`` declares that the CLI builds its own sandbox inside the
    jail, which selects the jail's permissive seccomp profile. All three are only
    read when a jail applies.

    POSIX goes through the wrapper/anchor handshake and **fails closed**: on
    any anchor failure the half-spawned family is torn down and
    :class:`FamilyAnchorError` is raised rather than a CLI this adapter could
    not end. Windows spawns exactly as before and registers the bounded
    tree-walk teardown.
    """
    from tinyassets.providers.provider_jail import confine_launch

    argv = _shell_argv(cmd) if shell else list(cmd)
    jailed = confine_launch(
        argv,
        cwd=kwargs.get("cwd"),
        env=kwargs.get("env"),
        view=universe_view,
        install_mounts=install_mounts,
        nested_sandbox=nested_sandbox,
    )
    if jailed is not None:
        # bwrap sets the child's working directory itself (--chdir); the host
        # side only needs a directory that exists.
        kwargs["cwd"] = "/"
        budget = None
        try:
            budget = _open_disk_budget(jailed.universe_dir)
            proc = await _aspawn_anchored(
                jailed.argv, extra_fds=jailed.pass_fds, **kwargs,
            )
        except BaseException:
            if budget is not None:
                budget.settle()
            raise
        finally:
            # The child holds its own copies (bwrap reads the seccomp filter
            # from them); ours are released whatever the spawn did.
            jailed.close()
        _watch_disk(proc, budget)
        return proc
    if os.name == "posix":
        return await _aspawn_anchored(argv, **kwargs)
    if shell:
        proc = await asyncio.create_subprocess_shell(
            shlex.join(cmd), **owned_spawn_kwargs(), **kwargs,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd, **owned_spawn_kwargs(), **kwargs,
        )
    mark_owned(proc)
    return proc


# --- disk budget ------------------------------------------------------------

#: Seconds between disk-budget polls of a running jailed provider process.
DISK_POLL_SECONDS = 0.5


def _open_disk_budget(universe_dir):
    """The launch's disk budget, or refuse it before anything is spawned."""
    from tinyassets import jail_disk
    from tinyassets.providers.provider_jail import ProviderConfinementError

    try:
        budget = jail_disk.open_budget(universe_dir)
    except jail_disk.DiskFloorRefused as below:
        raise ProviderConfinementError(
            f"{ProviderConfinementError.MESSAGE}: {below}; nothing was started"
        ) from None
    if budget.notice:
        logger.warning("jailed provider launch on a grace disk budget: %s", budget.notice)
    return budget


def _watch_disk(proc, budget) -> None:
    """Poll ``budget`` while ``proc`` runs; end its family on a breach.

    The tool jail has its own supervisor loop; a provider process has none, so
    this task is it. The breach is kept on the process (``disk_killed``) so the
    adapter's error can say why the CLI died. The task is held on the process,
    so it lives exactly as long as the launch does."""

    async def watch() -> None:
        waiter = asyncio.ensure_future(proc.wait())
        try:
            while not waiter.done():
                done, _ = await asyncio.wait({waiter}, timeout=DISK_POLL_SECONDS)
                if done:
                    break
                killed = await asyncio.to_thread(budget.breach)
                if killed:
                    proc.disk_killed = killed
                    logger.warning(
                        "jailed provider process stopped: %s (bound %d bytes)",
                        killed, budget.bound,
                    )
                    kill_owned_tree(proc)
                    await waiter
                    break
        finally:
            if not waiter.done():
                waiter.cancel()
            await asyncio.to_thread(budget.settle)

    proc.disk_killed = None
    proc.disk_watch = asyncio.get_running_loop().create_task(watch())


_DISK_STOP_NOTES = {
    "storage_limit": (
        "stopped: this run added more to its command center than the owner's "
        "cloud storage had room for"
    ),
    "disk_limit": "stopped: the shared disk was nearly full",
}


def disk_stop_note(proc) -> str:
    """`` (stopped: ...)`` when the disk budget ended ``proc``, else empty.

    For an adapter's exit error, so a CLI killed for writing too much does not
    read as a crash or a credential problem."""
    note = _DISK_STOP_NOTES.get(getattr(proc, "disk_killed", None) or "")
    return f" ({note})" if note else ""


# --- teardown ---------------------------------------------------------------


def _windows_tree_kill_argv(pid: int) -> list[str]:
    return ["taskkill", "/F", "/T", "/PID", str(pid)]


def kill_owned_tree(proc) -> None:
    """Synchronously end ``proc`` and, where owned, everything it started.

    POSIX: hand the anchor its command and close the control pipe -- the anchor
    signals its own group, so this never names a group id itself. Then kill the
    direct child as a narrow backstop, which is safe because we still hold its
    handle. Windows: a bounded best-effort tree walk, only while we still own a
    live child. An unregistered process, or a POSIX one that never got an
    anchor, is killed individually and never triggers group signalling.
    """
    family = _take_family(proc)
    if family is None:
        _kill_direct(proc)
        return
    if family.kind == "anchor":
        family.end()
        _kill_direct(proc)
        return
    if family.kind != "windows-tree":
        _kill_direct(proc)
        return
    if getattr(proc, "returncode", None) is not None:
        # Already reaped: the pid may now belong to someone else. Never walk it.
        return
    try:
        # Walk while the parent still exists. Killing it first severs the tree
        # before taskkill can find its children.
        subprocess.run(
            _windows_tree_kill_argv(family.pgid), capture_output=True, check=False,
            timeout=_TREE_KILL_TIMEOUT_S, **no_window_kwargs(),
        )
    except Exception:  # noqa: BLE001 - retain direct-child fallback
        logger.warning("provider tree cleanup could not complete")
    finally:
        _kill_direct(proc)


async def akill_owned_tree(proc) -> None:
    """Async :func:`kill_owned_tree`; the Windows tree walk never blocks the loop.

    The POSIX path is already a pipe write and a close, so it has nothing to
    await -- which also makes it usable from a cancellation path that cannot
    rely on reaching another suspension point.
    """
    family = _take_family(proc)
    if family is None:
        _kill_direct(proc)
        return
    if family.kind == "anchor":
        family.end()
        _kill_direct(proc)
        return
    if family.kind != "windows-tree":
        _kill_direct(proc)
        return
    if getattr(proc, "returncode", None) is not None:
        return
    killer = None
    try:
        killer = await asyncio.create_subprocess_exec(
            *_windows_tree_kill_argv(family.pgid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        await asyncio.wait_for(killer.wait(), timeout=_TREE_KILL_TIMEOUT_S)
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning(
            "provider tree cleanup for pid %s exceeded its bound", family.pgid,
        )
        with contextlib.suppress(Exception):  # noqa: BLE001
            killer.kill()
    except Exception:  # noqa: BLE001 - taskkill missing / already gone
        logger.warning("provider tree cleanup could not complete")
    finally:
        _kill_direct(proc)
        # Release the loop's transport reference, as
        # native_jsonrpc_discovery._close_metadata_process does; otherwise the
        # reaper leaks an unclosed transport on every deadline it handles.
        with contextlib.suppress(Exception):  # noqa: BLE001
            if killer is not None:
                killer._transport.close()

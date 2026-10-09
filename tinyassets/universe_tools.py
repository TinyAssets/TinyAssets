"""The command center agent's four tools: ``read``, ``write``, ``edit``, ``bash``.

Slice S1 of "the command center is the harness" (ADR-013; OpenSpec
change ``universe-harness-four-tools``). A command center IS its agent's harness and
project folder, so its agent works in that folder with the same four
primitives pi.dev gives an agent, and builds everything else from them.

Vendor-neutral by construction (Hard Rule 3): the PLATFORM runs these tools,
not a vendor CLI's built-ins. They are served over the per-universe engine MCP
route every adapter already uses (``claude -p``, ``codex exec``, the
OpenAI-compatible HTTP loop), so each adapter sees the same four definitions.

The tool jail
-------------
Every call -- reads included -- runs as a process inside bubblewrap, built by
the SAME :func:`tinyassets.providers.provider_jail.jail_argv` as a provider
launch, with a narrower view:

* the agent's OWN workspace in the owning command center, at ``/u``
  (harness W2, design #4172 §4.3: "the
  agent has its own workspace it fully owns"). ``/u`` is the universe's
  ``.agent-workspace/`` directory, bound read-write as a whole, so the agent
  can create, rename and delete anything at the top of its workspace like on
  its own computer. Platform state stays where it is, in the universe root,
  which is never bound. On top of the workspace, each VISIBLE root entry is
  bound at its own name: what the agent owns read-write (its brain files, its
  own ``wiki/`` and the harness directories ``skills/``, ``prompts/``,
  ``notes/`` ...), see :data:`AGENT_BRAIN_FILES`; every other visible entry
  read-only. A new name the agent creates lands in its workspace, which no
  daemon code trusts or reads as platform state;
* no hidden root entry at all -- the credential vault
  (``.credential-vault.json``, ``.credentials/``), ``.runtime/``, the consent,
  usage and receipt databases and their SQLite sidecars -- so the agent can
  neither read the owner's credentials nor forge the platform's authority
  state, and cannot create a new root entry the daemon would trust. Leaving
  them out, rather than mounting over each one, is what lets a jail start
  while the daemon has a database open: a ``-shm``/``-wal`` sidecar comes and
  goes with the connection, and a mask needs its mountpoint to still exist
  when bubblewrap reaches it (on the read-only root it cannot be created, so
  the jail refused to start). A visible entry that vanishes between the scan
  and the launch is skipped, for the same reason;
* system binaries read-only, a private ``/tmp``, ``/dev`` and pid-namespace
  ``/proc``; NO ``/app``, no install tree, no credential snapshot at all;
* NO network: no ``--share-net``, so the jail has its own empty network
  namespace (network arrives in S3, through an egress filter);
* an empty environment (``--clearenv``) plus a fixed ``PATH``/``HOME``;
* a seccomp filter refusing ``symlink``/``mknod`` (see :func:`seccomp_program`):
  the daemon reads this folder from OUTSIDE the jail and follows links, so a
  link planted towards another command center must never exist on disk. The
  same filter refuses io_uring, new user namespaces and the other kernel
  interfaces listed in :mod:`tinyassets.providers.jail_seccomp`.

Because the process sees only ``/u``, path policy is the jail's, not Python's:
a path outside the command center, or a symlink the agent planted towards another
command center, resolves inside the jail's own mount namespace and finds nothing.

Resource limits (per call, per command center)
----------------------------------------
Measured on the production container 2026-09-24 (kernel 6.1, uid 1001, no
capabilities, cgroup2 mounted READ-ONLY, so no per-universe cgroup can be
created): ``prlimit`` runs inside the jail, after the user namespace exists,
and sets ``RLIMIT_AS``, ``RLIMIT_NPROC``, ``RLIMIT_CPU``,
``RLIMIT_NOFILE`` and ``RLIMIT_CORE``. On kernels >= 5.14 ``RLIMIT_NPROC`` is
charged per user namespace, so it bounds THIS jail's processes, not the
daemon user's (measured: with 9 daemon processes and ``--nproc=12`` the jail
forked 10). The parent adds what an rlimit cannot: a wall clock, an output
cap enforced while reading, and a watch on the jail's whole process tree
(count and resident memory) and a free-space floor on the shared data
volume. The kernel exempts root from ``RLIMIT_NPROC``, so a ROOT-run jail
(a hosted CI runner's sudo fallback, a self-host running as root) runs inside
its own cgroup v2 with ``pids.max`` and ``memory.max`` instead, or is refused.
Concurrency is bounded per command center and across the host by lock-file slots,
so the sum of jails is bounded too.

Fail closed: no bubblewrap, no ``prlimit``, or a jail that exits before the
marker proving the limits were applied, and the call is refused with nothing
run. There is no unjailed or unlimited fallback.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from tinyassets import jail_disk
from tinyassets.addressed_agents import MAIN_AGENT
from tinyassets.providers import provider_jail
from tinyassets.providers.provider_jail import (
    PLATFORM_RUNTIME_DIR,
    JailMount,
    UniverseView,
    jail_argv,
)
from tinyassets.tool_images import (
    MAX_IMAGE_SOURCE_BYTES,
    ToolImage,
    bound_image,
    is_image_path,
)

__all__ = [
    "MASKED_DIRS",
    "MOUNT_POINT",
    "TOOL_NAMES",
    "ToolLimits",
    "ToolRun",
    "UniverseToolError",
    "bash",
    "edit_file",
    "harness_prompt",
    "read_file",
    "seccomp_program",
    "skill_index",
    "tool_jail_argv",
    "write_file",
]

#: The whole tool list. Nothing else is an agent tool definition.
TOOL_NAMES: tuple[str, ...] = ("read", "write", "edit", "bash")

#: Where the universe appears inside the tool jail.
MOUNT_POINT = "/u"

#: What the agent OWNS in its folder: the only paths bound read-write into the
#: tool jail. Everything else at the universe root is the platform's and is
#: either read-only (visible, e.g. ``soul.md``, ``config.yaml``) or absent
#: (every hidden root entry: the credential vault ``.credential-vault.json`` and
#: ``.credentials/``, ``.runtime/``, the consent / usage / receipt databases).
#: The root itself is read-only, so no new root entry -- hidden or not -- can
#: be created: the agent can neither read the platform's state nor plant a file
#: the daemon would later trust as its own.
#:
#: Brain files are bound only when they already exist (an empty brain file
#: would read as "learned"); the harness directories are created first.
AGENT_BRAIN_FILES: tuple[str, ...] = (
    "identity.md", "founder.md", "origin.md", "body.md", "orgchart.md",
    "projects.md", "goals.md", "index.md", "log.md", "voice.md", "AGENTS.md", "MEMORY.md",
)
AGENT_HARNESS_DIRS: tuple[str, ...] = (
    "skills", "prompts", "extensions", "workflows", "bin", "notes", "wiki",
)

#: The agent's own workspace inside the universe: the tool jail's ``/u``.
#: Hidden (a dot name), so it is never itself bound as a root entry, every
#: provider launch masks it, and it is platform-created without following a link.
WORKSPACE_DIR = provider_jail.AGENT_WORKSPACE_DIR

#: Kept for callers that name the platform-owned runtime directory.
MASKED_DIRS: tuple[str, ...] = (PLATFORM_RUNTIME_DIR,)

#: Environment inside the jail: fixed, secret-free, nothing inherited.
logger = logging.getLogger(__name__)

_JAIL_ENV: tuple[tuple[str, str], ...] = (
    ("PATH", "/usr/local/bin:/usr/bin:/bin"),
    ("HOME", "/tmp"),
    ("LANG", "C.UTF-8"),
    ("TERM", "dumb"),
)

#: Printed by the jailed wrapper AFTER prlimit applied every limit and before
#: the command runs. Output that does not start with it means nothing ran
#: under limits, and the call is refused.
_LIMITS_MARK = b"\x1eta-limits-applied\x1e"

_MiB = 1024 * 1024

#: Largest file ``write`` accepts and ``edit`` will rewrite.
MAX_WRITE_BYTES = 4 * _MiB
MAX_EDIT_BYTES = 1 * _MiB
#: ``read`` returns at most this many lines unless asked for a range.
DEFAULT_READ_LINES = 2000
#: The longest a ``bash`` call may ask to run.
MAX_BASH_SECONDS = 600.0

#: Jails running at once on the whole HOST. A host-safety floor, not an account
#: limit: four concurrent jails is what this box's memory and 1 vCPU can carry,
#: and exceeding it is an outage for every universe on it.
#:
#: There is no per-universe count. ``_PER_UNIVERSE_SLOTS = 2`` was a second,
#: account-shaped ceiling on top of it -- it told one universe it could not run a
#: third tool even on an otherwise idle host. An account has exactly two limits,
#: cloud bytes and concurrent agent seats (founder, 2026-09-30).
_HOST_SLOTS = 4

#: How often a waiting call re-tries for a free host slot. A busy host makes a
#: call WAIT; it does not refuse it. There used to be a 30-second deadline here
#: after which the call raised "every tool slot is busy" -- work over the
#: concurrency line waits, and is never refused.
_SLOT_POLL_SECONDS = 0.1

_POLL_SECONDS = 0.05
_KILL_GRACE_SECONDS = 5.0


class UniverseToolError(RuntimeError):
    """A tool call was refused before (or instead of) running anything."""

    failure_class = "universe_tool_refused"


@dataclass(frozen=True)
class ToolLimits:
    """The limits every tool jail runs under. Floor, not policy: on a shared
    host a fork bomb or a memory spike in one command center is an outage for the
    others (design section 4)."""

    #: ``RLIMIT_AS`` per process.
    memory_bytes: int = 512 * _MiB
    #: ``RLIMIT_NPROC``: per jail user namespace on kernel >= 5.14.
    processes: int = 64
    #: ``RLIMIT_CPU`` per process; the wall clock bounds the whole call.
    cpu_seconds: int = 120
    #: ``RLIMIT_NOFILE``.
    open_files: int = 256
    #: Wall clock for one call (``bash`` may ask for up to MAX_BASH_SECONDS).
    wall_seconds: float = 120.0
    #: Output kept per call; the jail is killed once it passes this.
    output_bytes: int = 64 * 1024
    #: Resident memory summed over the jail's process tree.
    tree_memory_bytes: int = 768 * _MiB
    #: Free space the shared data volume must keep: a call is refused below it,
    #: and a running jail is killed when its writes take the volume below it.
    min_free_disk_bytes: int = jail_disk.MIN_FREE_DISK_BYTES
    #: Free inodes the shared data volume must keep: a full inode table is a
    #: cross-user outage that free BYTES do not show (many tiny files).
    min_free_inodes: int = jail_disk.MIN_FREE_INODES
    #: ``nice`` increment for jail processes: they yield to the daemon's own
    #: work on the shared 1 vCPU box.
    nice_increment: int = 10
    #: The OOM killer picks a jail process first under memory pressure, never
    #: the daemon (raised in-jail to /proc/self/oom_score_adj; root only lowers).
    oom_score_adj: int = 1000

    def prlimit_args(self, *, cpu_seconds: int | None = None) -> list[str]:
        cpu = int(cpu_seconds if cpu_seconds is not None else self.cpu_seconds)
        return [
            f"--as={int(self.memory_bytes)}",
            f"--nproc={int(self.processes)}",
            # soft < hard: SIGXCPU names the limit; SIGKILL one second later
            # if the process ignores it.
            f"--cpu={max(1, cpu)}:{max(1, cpu) + 1}",
            f"--nofile={int(self.open_files)}",
            "--core=0",
        ]


DEFAULT_LIMITS = ToolLimits()


@dataclass(frozen=True)
class ToolRun:
    """What one jailed process did."""

    exit_code: int | None
    output: bytes
    #: ``timeout``, ``output_limit``, ``memory_limit``, ``process_limit``,
    #: ``disk_limit``, ``storage_limit``, ``activity_stopped`` or None.
    killed: str | None
    elapsed: float
    #: Seconds this call spent QUEUED for a host tool slot before it started.
    #: Reported in the result trailer: every tool here answers with text, and a
    #: wait the caller cannot see is indistinguishable from a hang.
    waited: float = 0.0
    #: Said before the tool's answer: the owner is out of storage (the call
    #: still runs so it can delete files -- see `jail_disk`).
    notice: str = ""
    #: Latest observed headroom in the owner's total storage.
    disk_bound: int = 0


# ── the jail ────────────────────────────────────────────────────────────────


def _system_binary(name: str) -> str:
    """A binary the jail can see (``/usr`` or ``/bin`` are bound), or refuse."""
    found = shutil.which(name, path="/usr/bin:/bin")
    if not found or not (found.startswith("/usr/") or found.startswith("/bin/")):
        raise UniverseToolError(
            f"{name} is not installed on this host, so the tool jail cannot apply "
            "its resource limits; nothing ran"
        )
    return found


def _workspace(root: Path) -> Path:
    """The universe's ``.agent-workspace/``, created if absent, never a link."""
    try:
        return provider_jail.ensure_agent_workspace(root)
    except provider_jail.ProviderConfinementError:
        raise UniverseToolError(
            f"the agent workspace {WORKSPACE_DIR}/ is not a plain directory; nothing ran"
        ) from None


def _promote_brain_files(root: Path, workspace: Path, *, agent_id: str) -> None:
    """A brain file the agent wrote while the root had none moves to the root.

    Brain files are bound only when they exist at the root (an empty one would
    read as "learned"), so writing an absent ``identity.md`` landed in the
    workspace, where the daemon's grounding never looks (gpt-6-astra on #4194).
    The root copy is agent-writable anyway, so moving a plain regular file
    there grants nothing new.
    """
    for name in AGENT_BRAIN_FILES:
        # identity.md is the main agent's own: another agent's call never
        # promotes it (harness §4.18).
        if name == "identity.md" and agent_id != MAIN_AGENT:
            continue
        source, target = workspace / name, root / name
        if os.path.lexists(target) or source.is_symlink() or not source.is_file():
            continue
        try:
            # link() never replaces: a root file created meanwhile is kept.
            os.link(source, target)
        except FileExistsError:
            continue
        source.unlink()


def _clear_link_mountpoint(workspace: Path, name: str) -> None:
    """A link left where a root entry is about to be bound is removed first.

    bubblewrap would follow it inside the jail when it creates the mountpoint.
    Removing the link itself never follows it.
    """
    candidate = workspace / name
    if candidate.is_symlink():
        candidate.unlink()


def _universe_view(
    root: Path, egress_socket: Path | None = None, *, agent_id: str,
    ta_socket: Path | None = None,
    extension_root: Path | None = None,
    promote_brain_files: bool = True,
) -> UniverseView:
    """The tool jail's view of ``root``: the agent's own workspace at ``/u``,
    read-write, with the visible root entries bound on top at their names
    (agent-owned read-write, the rest read-only) and hidden entries absent.

    Order is fixed: the workspace, then one bind per entry over it. Every
    entry bind is ``-try``: the daemon owns this folder concurrently, and an
    entry it removes after the scan is simply not in this call's view.
    """
    if not agent_id.strip():
        raise UniverseToolError("agent_id is required")
    for name in AGENT_HARNESS_DIRS:
        path = root / name
        if not os.path.lexists(path):
            path.mkdir(mode=0o755)
    workspace = _workspace(root)
    # The owner's tool cell passes False: its maintenance cell
    # (:func:`tinyassets.role_tool_files.maintain`) has already promoted, as
    # the owner, immediately before this view is built. Promoting again from
    # inside the cell would be a second writer for the same names.
    if promote_brain_files:
        _promote_brain_files(root, workspace, agent_id=agent_id)
    mounts = [JailMount("bind", MOUNT_POINT, workspace)]
    with os.scandir(root) as entries:
        listing = sorted(entries, key=lambda entry: entry.name)
    for entry in listing:
        # Hidden: platform state. Symlink: never bound (a planted link must not
        # be followed). Neither dir nor file: nothing the tools need.
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        is_dir = entry.is_dir(follow_symlinks=False)
        if not is_dir and not entry.is_file(follow_symlinks=False):
            continue
        owned = (
            entry.name in AGENT_HARNESS_DIRS if is_dir else entry.name in AGENT_BRAIN_FILES
        )
        if entry.name == "identity.md" and agent_id != MAIN_AGENT:
            owned = False
        op = "bind-try" if owned else "ro-bind-try"
        _clear_link_mountpoint(workspace, entry.name)
        mounts.append(JailMount(op, f"{MOUNT_POINT}/{entry.name}", root / entry.name))
    setenv = _JAIL_ENV
    if egress_socket is not None:
        # The jail still has no interface but loopback; this socket is its only
        # way out, to the checking proxy in the daemon (universe_egress).
        from tinyassets import universe_egress

        mounts.append(JailMount("bind", universe_egress.JAIL_SOCKET, egress_socket))
        setenv = _JAIL_ENV + universe_egress.PROXY_ENV
    if ta_socket is not None:
        from tinyassets.ta_capabilities import CLIENT_SOURCE, JAIL_CLIENT, JAIL_SOCKET

        mounts.extend((JailMount("bind", JAIL_SOCKET, ta_socket),
                       JailMount("ro-bind", JAIL_CLIENT, CLIENT_SOURCE)))
        setenv = tuple((key, "/ta/bin:" + value if key == "PATH" else value)
                       for key, value in setenv)
    if extension_root is not None:
        mounts.append(JailMount("ro-bind", "/ta/extensions", extension_root))
    return UniverseView(
        universe_dir=root,
        mounts=tuple(mounts),
        chdir=MOUNT_POINT,
        setenv=setenv,
    )


def tool_jail_argv(
    universe_dir: Path, inner: Sequence[str], *, agent_id: str, seccomp_fd: int | None = None,
    egress_socket: Path | None = None,
    ta_socket: Path | None = None,
    extension_root: Path | None = None,
    promote_brain_files: bool = True,
) -> list[str]:
    """The bubblewrap argv running ``inner`` in ``universe_dir``'s tool jail."""
    if not agent_id.strip():
        raise UniverseToolError("agent_id is required")
    try:
        root = Path(universe_dir).resolve(strict=True)
    except OSError:
        raise UniverseToolError("the command center folder does not exist") from None
    if not root.is_dir():
        raise UniverseToolError("the command center folder does not exist")
    bwrap = provider_jail.BWRAP_RESOLVER()
    view = _universe_view(root, egress_socket, agent_id=agent_id, ta_socket=ta_socket,
                          extension_root=extension_root,
                          promote_brain_files=promote_brain_files)
    # The egress socket lives in the daemon-owned sidecar folder, outside the
    # command center, so it has to be declared as the exact path this jail is
    # allowed to bind from there. A directory prefix is not a capability: the
    # validator used to admit anything resolving under that folder, which let a
    # swapped link turn one of the binds below into a writable handle on
    # daemon-owned state (provider_jail.UNIVERSE_SIDECARS_DIR).
    platform_sources = (
        frozenset({Path(egress_socket).resolve(strict=False)})
        if egress_socket is not None else frozenset()
    )
    if ta_socket is not None:
        from tinyassets.ta_capabilities import CLIENT_SOURCE

        platform_sources |= frozenset({ta_socket.resolve(), CLIENT_SOURCE.resolve()})
    if extension_root is not None:
        platform_sources |= frozenset({extension_root.resolve()})
    return jail_argv(
        list(inner), view, bwrap_path=bwrap, clearenv=True,
        seccomp_fd=seccomp_fd, platform_sources=platform_sources,
    )


#: Builds the jail argv. Substituted by tests (injection, never an env switch);
#: production never replaces it.
TOOL_JAIL_ARGV: Callable[..., list[str]] = tool_jail_argv


# The syscall filter is shared with the provider jail
# (:mod:`tinyassets.providers.jail_seccomp`): links and special files (the
# daemon reads this folder from OUTSIDE the jail and follows links, so a link
# planted towards another universe must never exist on disk), io_uring (the
# way around a syscall filter) and the kernel interfaces a cross-user privilege
# escalation on the shared kernel keeps using. The daemon-side safe reader
# (:mod:`tinyassets.universe_files`) is the belt to this braces: it never
# follows a link that already exists, whatever created it.


def seccomp_program() -> bytes:
    """The compiled cBPF filter bubblewrap loads with ``--seccomp``."""
    from tinyassets.providers.jail_seccomp import deny_program

    return deny_program()


def _seccomp_fd() -> int:
    """A readable descriptor holding :func:`seccomp_program`, for the child."""
    from tinyassets.providers.jail_seccomp import program_fd

    return program_fd()


_free_disk = jail_disk.free_bytes
_free_inodes = jail_disk.free_inodes


#: Set from the parent right after spawn: no ``preexec_fn`` (the daemon is
#: multithreaded), and the in-jail shell also raises its own as a backstop.
def _limited(inner: Sequence[str], limits: ToolLimits, cpu_seconds: int) -> list[str]:
    """``inner`` wrapped so it runs only after every rlimit is in place.

    The in-jail shell raises its own OOM score (so the killer takes a jail
    process, not the daemon) and ``exec``s the command under ``nice`` -- both
    best-effort, both without a ``preexec_fn`` -- after printing the marker
    that proves the limits were applied.
    """
    prlimit = _system_binary("prlimit")
    nice = shutil.which("nice", path="/usr/bin:/bin")
    launch = [nice, "-n", str(int(limits.nice_increment)), *inner] if nice else list(inner)
    script = (
        f'echo {int(limits.oom_score_adj)} > /proc/self/oom_score_adj 2>/dev/null; '
        'printf "%s" "$0"; exec "$@" 2>&1'
    )
    return [
        prlimit, *limits.prlimit_args(cpu_seconds=cpu_seconds), "--",
        "/bin/sh", "-c", script, _LIMITS_MARK.decode("ascii"), *launch,
    ]


def _slot_dir() -> Path:
    from tinyassets.storage import data_dir

    path = Path(data_dir()) / ".universe-tool-slots"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextlib.contextmanager
def _slot(
    universe_dir: Path,
    *,
    on_wait: Callable[[float], None] | None = None,
    waited: list[float] | None = None,
) -> Iterator[None]:
    """WAIT for one host slot, then run. Never refuses for being busy.

    Lock files, so the bound holds across the per-universe engine processes.

    ``on_wait`` is called once, with the seconds waited so far, the first time a
    call actually has to queue -- so a surface that has a user in front of it can
    say "waiting for a free slot" instead of going silent. ``waited`` (a
    one-element sink list) receives the total seconds queued, which the result
    trailer reports. Waiting is visible or it is indistinguishable from a hang.

    KNOWN GAP (Codex refute, 2026-09-30, P2): this wait is not interruptible.
    The engine tool wrappers reach it through ``asyncio.to_thread``, and
    cancelling that await does not stop the worker thread, so a cancelled request
    keeps its place in the queue until a slot frees. The waiter holds no lock and
    no jail (one pipe descriptor only), and the queue's depth is the transport's
    own thread pool rather than anything a command center chooses --
    ``docs/concerns/2026-09-30-a-cancelled-tool-call-keeps-waiting.md``.

    There is no deadline. A 30-second one used to turn a busy host into
    ``every tool slot for this host is busy``, which is a refusal wearing a
    timeout's clothes (founder, 2026-09-30: over the concurrency line, work
    WAITS).
    """
    import fcntl

    directory = _slot_dir()
    pool = [directory / f"host-{i}.lock" for i in range(_HOST_SLOTS)]
    started = time.monotonic()
    fd: int | None = None
    announced = False
    try:
        while True:
            fd = _try_lock_one(pool, fcntl)
            if fd is not None:
                if waited is not None:
                    waited.append(time.monotonic() - started if announced else 0.0)
                break
            if not announced:
                announced = True
                if on_wait is not None:
                    with contextlib.suppress(Exception):
                        on_wait(time.monotonic() - started)
                logger.info(
                    "universe_tools: every host tool slot is busy; waiting (command center %s)",
                    universe_dir.name,
                )
            time.sleep(_SLOT_POLL_SECONDS)
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _try_lock_one(paths: list[Path], fcntl) -> int | None:
    for path in paths:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            continue
        return fd
    return None


class _Drain(threading.Thread):
    """Read a pipe, keeping at most ``cap`` bytes; flag the moment it passes."""

    def __init__(self, stream, cap: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._cap = cap
        self._chunks: list[bytes] = []
        self._size = 0
        self.over = threading.Event()

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read1(65536)
                if not chunk:
                    return
                room = self._cap - self._size
                if room > 0:
                    self._chunks.append(chunk[:room])
                    self._size += min(len(chunk), room)
                if len(chunk) > room:
                    self.over.set()
                    return
        except (OSError, ValueError):
            return

    @property
    def data(self) -> bytes:
        return b"".join(self._chunks)


def _feed(stdin, payload: bytes) -> None:
    try:
        stdin.write(payload)
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            stdin.close()


def _kill(proc: subprocess.Popen) -> None:
    """Kill the jail: bwrap's group; ``--die-with-parent`` and the pid
    namespace take every process inside with it."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def _tree(pid: int) -> tuple[int, int]:
    from tinyassets.node_sandbox import read_process_tree

    return read_process_tree(pid)


def run_jailed(
    universe_dir: Path,
    inner: Sequence[str],
    *,
    agent_id: str,
    stdin: bytes | None = None,
    limits: ToolLimits = DEFAULT_LIMITS,
    wall_seconds: float | None = None,
    output_bytes: int | None = None,
    on_wait: Callable[[float], None] | None = None,
    egress_socket: Path | None = None,
    ta_socket: Path | None = None,
    stop: Callable[[], str | None] | None = None,
    extension_root: Path | None = None,
) -> ToolRun:
    """Run ``inner`` in the command center's tool jail under ``limits``.

    Nothing is spawned here. The request goes to the owner's tool cell
    (:func:`tinyassets.role_tools.run`), which keeps the daemon-side queueing,
    seed boundary and disk accounting and runs the nested jail as the owner.

    If every host slot is taken the call WAITS for one; it is not refused for the
    host being busy. ``on_wait`` is invoked once when that happens, so a caller
    with a user in front of it can surface a waiting state. ``stop`` is polled
    while the cell runs; once it names a reason the cell is killed
    (``activity_stopped``): an activity that yielded, paused or stopped.
    """
    if not agent_id.strip():
        raise UniverseToolError("agent_id is required")
    from tinyassets.role_tools import run

    return run(
        Path(universe_dir).resolve(), inner, agent_id=agent_id, stdin=stdin,
        limits=limits, wall_seconds=wall_seconds, output_bytes=output_bytes,
        on_wait=on_wait, egress_socket=egress_socket, ta_socket=ta_socket,
        stop=stop, extension_root=extension_root,
    )


#: Where a root-run jail creates its cgroup. Substituted by tests.
CGROUP_ROOT = Path("/sys/fs/cgroup")
_CGROUP_CONTROLLERS = ("memory", "pids")


def _refuse_root(detail: str) -> UniverseToolError:
    return UniverseToolError(
        "the tool jail would run as root here, where the kernel exempts root from "
        f"the process limit, and {detail}; nothing ran"
    )


@contextlib.contextmanager
def _root_cgroup(limits: ToolLimits, process_cap: int) -> Iterator[Path | None]:
    """A fresh cgroup bounding a ROOT-run jail, or ``None`` when not root.

    Unprivileged (production: uid 1001), ``RLIMIT_NPROC`` inside the jail's
    user namespace bounds its processes and this yields ``None``. Root is
    exempt from ``RLIMIT_NPROC``, and a root-run bwrap does not get a user
    namespace of its own, so a runaway fork would be bounded only by the tree
    watch -- too slow for an exponential fork bomb. A root-run jail therefore
    runs inside its own cgroup v2 with ``pids.max`` and ``memory.max``, or is
    refused.
    """
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        yield None
        return
    try:
        available = set((CGROUP_ROOT / "cgroup.controllers").read_text().split())
    except OSError:
        available = set()
    if not set(_CGROUP_CONTROLLERS) <= available:
        raise _refuse_root("there is no cgroup v2 with pids and memory controllers")
    path = CGROUP_ROOT / f"ta-universe-tool-{os.getpid()}-{time.monotonic_ns()}"
    try:
        subtree = CGROUP_ROOT / "cgroup.subtree_control"
        missing = set(_CGROUP_CONTROLLERS) - set(subtree.read_text().split())
        if missing:
            subtree.write_text(" ".join(f"+{name}" for name in sorted(missing)))
        path.mkdir()
    except OSError as exc:
        raise _refuse_root(f"its cgroup could not be created ({exc})") from None
    try:
        try:
            (path / "pids.max").write_text(str(int(process_cap)))
            (path / "memory.max").write_text(str(int(limits.tree_memory_bytes)))
        except OSError as exc:
            raise _refuse_root(f"its cgroup limits could not be set ({exc})") from None
        with contextlib.suppress(OSError):
            (path / "memory.swap.max").write_text("0")
        yield path
    finally:
        _remove_cgroup(path)


def _remove_cgroup(path: Path) -> None:
    """Kill whatever is left in the cgroup, wait for it to empty, remove it."""
    procs = path / "cgroup.procs"
    deadline = time.monotonic() + _KILL_GRACE_SECONDS
    while True:
        try:
            members = procs.read_text().split()
        except OSError:
            members = []
        if not members:
            break
        try:
            (path / "cgroup.kill").write_text("1")
        except OSError:
            for member in members:
                with contextlib.suppress(OSError, ValueError):
                    os.kill(int(member), signal.SIGKILL)
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    with contextlib.suppress(OSError):
        path.rmdir()


def _supervise(
    argv: list[str], root: Path, filter_fd: int, *, stdin: bytes | None,
    limits: ToolLimits, wall: float, cap: int, process_cap: int,
    budget: jail_disk.DiskBudget, stop: Callable[[], str | None] | None = None,
) -> ToolRun:
    """Start the jail and watch it until it ends or a limit kills it."""
    started = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 - argv is built above, never a shell string
        argv,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin"},
        cwd="/",
        close_fds=True,
        pass_fds=(filter_fd,),
        start_new_session=True,
    )
    # Belt to the in-jail shell's braces: raise the OOM score of the bwrap
    # parent so the killer prefers this whole tree over the daemon. Inherited by
    # every child; best-effort (a lowered score would need root).
    with contextlib.suppress(OSError, ValueError):
        Path(f"/proc/{proc.pid}/oom_score_adj").write_text(str(int(limits.oom_score_adj)))
    out = _Drain(proc.stdout, cap + len(_LIMITS_MARK))
    err = _Drain(proc.stderr, 16 * 1024)
    out.start()
    err.start()
    feeder = None
    if stdin is not None:
        feeder = threading.Thread(target=_feed, args=(proc.stdin, stdin), daemon=True)
        feeder.start()
    killed = None
    try:
        killed = _watch(proc, out, budget, limits=limits, wall=wall,
                        process_cap=process_cap, started=started, stop=stop)
    finally:
        try:
            proc.wait(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.wait(timeout=_KILL_GRACE_SECONDS)
        out.join(timeout=_KILL_GRACE_SECONDS)
        err.join(timeout=_KILL_GRACE_SECONDS)
        if feeder is not None:
            feeder.join(timeout=_KILL_GRACE_SECONDS)
    if killed is None and out.over.is_set():
        killed = "output_limit"
    if killed is None:
        final_breach = budget.breach(force=True)
        if final_breach:
            killed = final_breach.replace("_limit", "_exceeded")
    elapsed = time.monotonic() - started
    data = out.data
    if not data.startswith(_LIMITS_MARK):
        detail = err.data.decode("utf-8", "replace").strip()[-400:]
        raise UniverseToolError(
            "the tool jail did not start under its resource limits; nothing ran"
            + (f" ({detail})" if detail else "")
        )
    return ToolRun(
        exit_code=proc.returncode,
        output=data[len(_LIMITS_MARK):],
        killed=killed,
        elapsed=elapsed,
    )


def _watch(
    proc: subprocess.Popen, out: _Drain, budget: jail_disk.DiskBudget, *,
    limits: ToolLimits, wall: float, process_cap: int, started: float,
    stop: Callable[[], str | None] | None = None,
) -> str | None:
    """Poll the running jail; kill it and name the limit the moment one breaks."""
    next_tree = 0.0
    while proc.poll() is None:
        now = time.monotonic()
        killed = None
        if now - started > wall:
            killed = "timeout"
        elif out.over.is_set():
            killed = "output_limit"
        elif now >= next_tree:
            next_tree = now + 0.2
            count, rss = _tree(proc.pid)
            if count > process_cap:
                killed = "process_limit"
            elif rss > limits.tree_memory_bytes:
                killed = "memory_limit"
            elif stop is not None and stop() is not None:
                killed = "activity_stopped"
            else:
                killed = budget.breach()
        if killed:
            _kill(proc)
            return killed
        time.sleep(_POLL_SECONDS)
    return None


#: Runs one jailed call. Substituted by tests that need no real jail.
RUNNER: Callable[..., ToolRun] = run_jailed


# ── the four tools ──────────────────────────────────────────────────────────


def _jail_path(path: str) -> str:
    """The path as the jail sees it: relative paths are under ``/u``.

    No containment check here on purpose: the jail is the boundary, and a
    path outside ``/u`` simply does not exist inside it.
    """
    raw = (path or "").strip()
    if not raw:
        raise UniverseToolError("a path is required")
    if "\x00" in raw:
        raise UniverseToolError("a path may not contain a NUL byte")
    candidate = PurePosixPath(raw)
    if not candidate.is_absolute():
        candidate = PurePosixPath(MOUNT_POINT) / candidate
    return str(candidate)


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


_SIGXCPU = getattr(signal, "SIGXCPU", 24)
_SIGKILL = getattr(signal, "SIGKILL", 9)


def _waited_note(run: ToolRun) -> str:
    """The queued-for-a-slot note, or empty. Prepended to a tool's own answer.

    A busy host makes a call WAIT rather than refusing it, so the only way the
    caller learns that its 40-second read was 38 seconds of queueing is if the
    answer says so.
    """
    notice = f"{run.notice}\n" if run.notice else ""
    if run.waited < 1.0:
        return notice
    return notice + f"[waited {run.waited:.0f}s for a free tool slot on this host]\n"


def _trailer(run: ToolRun, limits: ToolLimits, wall: float) -> str:
    if run.killed == "timeout":
        return f"[killed: ran longer than {wall:g}s]"
    if run.killed == "activity_stopped":
        return "[killed: the activity stopped running (waiting on the owner, paused or stopped)]"
    if run.killed == "output_limit":
        return f"[killed: output passed {limits.output_bytes} bytes]"
    if run.killed == "memory_limit":
        return f"[killed: the jail's processes used more than {limits.tree_memory_bytes} bytes]"
    if run.killed == "process_limit":
        return f"[killed: more than {limits.processes} processes]"
    if run.killed == "disk_limit":
        return "[killed: the shared disk was nearly full]"
    if run.killed == "storage_exceeded":
        return "[finished: the owner's total cloud storage quota was exceeded; writes landed]"
    if run.killed == "disk_exceeded":
        return "[finished: the shared disk is nearly full; writes landed]"
    if run.killed == "storage_limit":
        return (
            "[killed: the owner's total cloud storage quota was exceeded]"
        )
    if run.exit_code == 128 + _SIGXCPU:
        return "[killed: cpu time limit]"
    if run.exit_code == 128 + _SIGKILL:
        return "[killed by the kernel: a cpu time or memory limit]"
    return f"[exit code {run.exit_code}]"


def read_file(
    universe_dir: Path, path: str, offset: int = 0, limit: int = 0,
    *, agent_id: str, limits: ToolLimits = DEFAULT_LIMITS,
) -> str | ToolImage:
    """Up to ``limit`` lines of a file from line ``offset`` (1-based), or, for an
    image path, the image itself (bounded by :mod:`tinyassets.tool_images`)."""
    if not agent_id.strip():
        raise UniverseToolError("agent_id is required")
    target = _jail_path(path)
    if is_image_path(target):
        return _read_image(universe_dir, target, limits, agent_id=agent_id)
    start = max(1, int(offset or 1))
    count = int(limit) if limit and int(limit) > 0 else DEFAULT_READ_LINES
    script = (
        '[ -e "$1" ] || { echo "no such file: $1"; exit 1; }; '
        'if [ -d "$1" ]; then ls -la -- "$1"; exit $?; fi; '
        'tail -n "+$2" -- "$1" | head -n "$3"'
    )
    run = RUNNER(
        universe_dir, ["/bin/sh", "-c", script, "sh", target, str(start), str(count)],
        agent_id=agent_id, limits=limits,
    )
    note = _waited_note(run)
    if run.killed == "output_limit":
        return (
            note + _text(run.output)
            + f"\n[truncated at {limits.output_bytes} bytes; read a smaller range "
            "with offset and limit]"
        )
    if run.killed or run.exit_code != 0:
        return (f"error: {note}"
                f"{_text(run.output).strip() or _trailer(run, limits, limits.wall_seconds)}")
    return note + _text(run.output)


def _read_image(
    universe_dir: Path, target: str, limits: ToolLimits, *, agent_id: str,
) -> str | ToolImage:
    """The whole file, read inside the same jail with a larger output cap for
    this one call, then bounded for the model outside it."""
    script = ('[ -f "$1" ] || { echo "no such file: $1"; exit 1; }; cat -- "$1"')
    image_limits = replace(limits, output_bytes=MAX_IMAGE_SOURCE_BYTES)
    run = RUNNER(
        universe_dir, ["/bin/sh", "-c", script, "sh", target],
        agent_id=agent_id, limits=image_limits,
    )
    note = _waited_note(run)
    if run.killed == "output_limit":
        return f"error: {note}{target} is over {MAX_IMAGE_SOURCE_BYTES} bytes; too large to show"
    if run.killed or run.exit_code != 0:
        return (f"error: {note}"
                f"{_text(run.output).strip() or _trailer(run, limits, limits.wall_seconds)}")
    shown = bound_image(run.output, target)
    if isinstance(shown, ToolImage) and note:
        return replace(shown, text=note + shown.text)
    return shown


def write_file(
    universe_dir: Path, path: str, content: str,
    *, agent_id: str, limits: ToolLimits = DEFAULT_LIMITS,
) -> str:
    """Create or replace a file, making parent directories."""
    from tinyassets.research_capability import research_refusal

    refusal = research_refusal("write")
    if refusal is not None:
        return refusal
    if not agent_id.strip():
        raise UniverseToolError("agent_id is required")
    target = _jail_path(path)
    payload = (content or "").encode("utf-8")
    if len(payload) > MAX_WRITE_BYTES:
        raise UniverseToolError(f"content is over the {MAX_WRITE_BYTES}-byte write limit")
    script = 'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"'
    run = RUNNER(
        universe_dir, ["/bin/sh", "-c", script, "sh", target], agent_id=agent_id,
        stdin=payload, limits=limits,
    )
    note = _waited_note(run)
    if run.killed or run.exit_code != 0:
        return (f"error: {note}"
                f"{_text(run.output).strip() or _trailer(run, limits, limits.wall_seconds)}")
    return note + f"wrote {len(payload)} bytes to {target}"


def edit_file(
    universe_dir: Path, path: str, old_text: str, new_text: str,
    *, agent_id: str, limits: ToolLimits = DEFAULT_LIMITS,
) -> str:
    """Replace the one exact occurrence of ``old_text`` with ``new_text``."""
    from tinyassets.research_capability import research_refusal

    refusal = research_refusal("edit")
    if refusal is not None:
        return refusal
    if not agent_id.strip():
        raise UniverseToolError("agent_id is required")
    target = _jail_path(path)
    if not old_text:
        raise UniverseToolError("old_text is required: the exact passage to replace")
    run = RUNNER(
        universe_dir,
        ["/bin/sh", "-c", '[ -f "$1" ] || { echo "no such file: $1"; exit 1; }; cat -- "$1"',
         "sh", target], agent_id=agent_id,
        limits=limits, output_bytes=MAX_EDIT_BYTES,
    )
    note = _waited_note(run)
    if run.killed == "output_limit":
        return f"error: {target} is over the {MAX_EDIT_BYTES}-byte edit limit; use bash"
    if run.killed or run.exit_code != 0:
        return (f"error: {note}"
                f"{_text(run.output).strip() or _trailer(run, limits, limits.wall_seconds)}")
    try:
        current = run.output.decode("utf-8")
    except UnicodeDecodeError:
        return f"error: {target} is not UTF-8 text; use bash"
    found = current.count(old_text)
    if found == 0:
        return f"error: old_text was not found in {target}"
    if found > 1:
        return (
            f"error: old_text matches {found} places in {target}; include more "
            "surrounding text so it matches exactly one"
        )
    written = write_file(
        universe_dir, target, current.replace(old_text, new_text, 1),
        agent_id=agent_id, limits=limits,
    )
    if written.startswith("error:"):
        return written
    return note + f"edited {target}"


def _egress_socket(universe_dir: Path) -> Path | None:
    """This universe's proxy socket, or ``None`` when network cannot be offered."""
    from tinyassets import universe_egress

    try:
        return universe_egress.ensure_proxy(universe_dir)
    except OSError:
        logger.warning("egress proxy unavailable for %s", universe_dir, exc_info=True)
        return None


def bash(
    universe_dir: Path, command: str, timeout: float = 0,
    *, agent_id: str, limits: ToolLimits = DEFAULT_LIMITS, ta_dispatch=None,
    stop: Callable[[], str | None] | None = None,
) -> str:
    """Run ``command`` with bash in ``/u``; stdout and stderr, then the outcome.

    ``stop`` (an activity's :func:`tinyassets.activity_fence.stop_check`) ends
    the command once the activity stops running.
    """
    from tinyassets.research_capability import research_refusal

    # D3a refuses all bash, stricter than a read-only mount: no shell or egress
    # is started during research, including when called below the MCP boundary.
    refusal = research_refusal("bash")
    if refusal is not None:
        return refusal
    if not agent_id.strip():
        raise UniverseToolError("agent_id is required")
    if not (command or "").strip():
        raise UniverseToolError("a command is required")
    wall = float(timeout) if timeout and float(timeout) > 0 else limits.wall_seconds
    wall = min(max(wall, 1.0), MAX_BASH_SECONDS)
    shell = _system_binary("bash")
    inner, egress = [shell, "-c", command], {}
    socket_path = _egress_socket(universe_dir)
    python = shutil.which("python3", path="/usr/bin:/bin")
    if socket_path is not None and python:
        from tinyassets import universe_egress

        inner = universe_egress.forwarder_argv(python, inner)
        egress = {"egress_socket": socket_path}
    if stop is not None:
        egress["stop"] = stop
    if ta_dispatch is None:
        run = RUNNER(universe_dir, inner, agent_id=agent_id, limits=limits,
                     wall_seconds=wall, **egress)
    else:
        from tinyassets.extension_git import for_launch
        from tinyassets.ta_capabilities import JailBridge

        ended = threading.Event()
        with JailBridge(_halting(ta_dispatch, stop, ended)) as bridge, for_launch(
            getattr(ta_dispatch, "extension_backend", None)
        ) as git_prefix:
            if bridge.extension_root is not None:
                egress["extension_root"] = bridge.extension_root
            if git_prefix:
                inner = [shell, "-c", git_prefix + command]
                if socket_path is not None and python:
                    inner = universe_egress.forwarder_argv(python, inner)
            try:
                run = RUNNER(universe_dir, inner, agent_id=agent_id, limits=limits,
                             wall_seconds=wall, ta_socket=bridge.path, **egress)
            finally:
                ended.set()
    body = _text(run.output)
    if body and not body.endswith("\n"):
        body += "\n"
    return _waited_note(run) + body + _trailer(run, limits, wall)


def _halting(ta_dispatch, stop: Callable[[], str | None] | None, ended: threading.Event):
    """``ta_dispatch``, except a request that stops the activity is never answered.

    The command is blocked in that ``ta`` call until it gets an answer, so
    withholding it until the supervisor's ``stop`` poll has killed the jail
    (``ended``) means nothing after an activity's own yield in the same command
    runs. Without ``stop`` (not an activity) it is ``ta_dispatch`` unchanged.
    """
    if stop is None:
        return ta_dispatch

    def dispatch(message):
        answer = ta_dispatch(message)
        reason = stop()
        if reason is None:
            return answer
        ended.wait(MAX_BASH_SECONDS + _KILL_GRACE_SECONDS)
        return {"error": reason}

    # The bridge mounts the launch's extensions from the dispatch it is given.
    dispatch.extension_backend = getattr(ta_dispatch, "extension_backend", None)
    return dispatch


# ── the skill index (progressive disclosure) ────────────────────────────────

SKILLS_DIR = "skills"
MAX_SKILLS = 64
_MAX_SKILL_FILE_BYTES = 256 * 1024
_MAX_DESCRIPTION_CHARS = 300
_MAX_FRONTMATTER_BYTES = 8 * 1024
_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DESCRIPTION_LINE = re.compile(r"^description:[ \t]*(.*)$", re.MULTILINE)


def _skill_description(raw: bytes) -> str:
    """The frontmatter ``description``, one line, or '' when there is none.

    A SKILL.md is a file the agent WRITES, so its frontmatter is untrusted and
    is never handed to a YAML loader: a 234-byte alias bomb expands to gigabytes
    and crash-loops the shared daemon. Instead this scans a bounded slice of the
    frontmatter for a single flat ``description:`` line, caps the length BEFORE
    building any string, and treats a quoted value literally (no YAML anchors,
    aliases, tags or block scalars are honoured). Nothing here can allocate more
    than a few hundred bytes.
    """
    text = raw[: 4 + _MAX_FRONTMATTER_BYTES].decode("utf-8", "replace").lstrip("﻿")
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    frontmatter = text[3:end] if end >= 0 else text[3 : 3 + _MAX_FRONTMATTER_BYTES]
    match = _DESCRIPTION_LINE.search(frontmatter)
    if match is None:
        return ""
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    description = " ".join(value.split())[:_MAX_DESCRIPTION_CHARS]
    return description


def skill_index(universe_dir: Path) -> list[tuple[str, str]]:
    """``(name, description)`` for each ``skills/<name>/SKILL.md``.

    Read by the daemon through the one safe reader
    (:mod:`tinyassets.universe_files`): the directory is listed and each file
    opened without following a link, each read is bounded, and a skill whose
    file is a link, is over its bound or fails to parse is left out -- never
    read into the prompt, never an error in the turn.
    """
    from tinyassets.universe_files import list_universe_dir, read_universe_file

    try:
        names = list_universe_dir(universe_dir, SKILLS_DIR)
    except (OSError, NotImplementedError):
        return []
    skills: list[tuple[str, str]] = []
    for name in names:
        if len(skills) >= MAX_SKILLS:
            break
        if not _SKILL_NAME.match(name):
            continue
        try:
            raw = read_universe_file(
                universe_dir, f"{SKILLS_DIR}/{name}/SKILL.md", max_bytes=_MAX_SKILL_FILE_BYTES,
            )
            description = _skill_description(raw)
        except (OSError, NotImplementedError, RecursionError, ValueError):
            continue
        if description:
            skills.append((name, description))
    return skills


_HARNESS_HEAD = (
    "/u is the workspace. Use read/write/edit/bash. In bash: `ta search <words>`, "
    "`ta describe <name>`, `ta call <name> --json '<args>'`. "
    "Read matching skills at skills/<name>/SKILL.md; owner files are editable.\nSkills:\n"
)


def _folder_section(universe_dir: Path) -> str:
    """Two levels of metadata through the same no-follow reader as skills."""
    from tinyassets.universe_files import list_universe_entries

    lines: list[str] = []
    remaining = 200

    def read(directory: str) -> list:
        nonlocal remaining
        entries = list_universe_entries(universe_dir, directory, limit=remaining)
        remaining -= len(entries)
        return entries

    def visit(directory: str, depth: int, entries: list, *, prefix: str = "") -> None:
        for name, info in entries:
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
                continue
            path = f"{directory}/{name}" if directory else name
            if prefix and not directory and not name.startswith("."):
                # The tool jail overlays visible root entries on the workspace.
                # lstat reads metadata only and never follows a planted link.
                try:
                    overlay = (universe_dir / name).lstat()
                except FileNotFoundError:
                    pass
                else:
                    if (not getattr(overlay, "st_reparse_tag", 0)
                            and (stat.S_ISDIR(overlay.st_mode)
                                 or stat.S_ISREG(overlay.st_mode))):
                        continue
            # Escape unusual names so a filename cannot inject extra prompt lines.
            shown = path.encode("unicode_escape").decode("ascii")
            if stat.S_ISDIR(info.st_mode):
                lines.append(f"- {shown}/")
                if depth < 2 and remaining:
                    visit(path, depth + 1, read(prefix + path), prefix=prefix)
            elif stat.S_ISREG(info.st_mode):
                lines.append(f"- {shown} ({info.st_size / 1024:.1f} KB)")

    try:
        for directory in ("notes", "prompts", "workflows"):
            if not remaining:
                break
            try:
                entries = read(directory)
            except FileNotFoundError:
                continue  # Optional top-level folders need not exist yet.
            visit(directory, 1, entries)
        if remaining:
            try:
                entries = read(WORKSPACE_DIR)
            except FileNotFoundError:
                entries = []
            visit("", 0, entries, prefix=WORKSPACE_DIR + "/")
    except (OSError, NotImplementedError, RecursionError, ValueError):
        return ""
    lines.sort()
    visible = lines[:40]
    if not remaining:
        visible.append("(more entries; `bash ls` shows them.)")
    elif len(lines) > 40:
        visible.append(f"({len(lines) - 40} more entries; `bash ls` shows them.)")
    return "\n\n## What is in my folder now\n" + "\n".join(visible or ["(empty)"])


def command_center_summary(universe_dir: Path, owner: str) -> str:
    """Bounded resident names and status for the verified owner's current home."""
    try:
        from tinyassets.api.status import _universe_active_turn
        from tinyassets.daemon_server import get_founder_home, list_branch_definitions
        from tinyassets.storage.outbound_connections import ConnectionLedger

        if not owner or get_founder_home(universe_dir.parent, owner) != universe_dir.name:
            return ""
        branches = list_branch_definitions(universe_dir.parent, author=owner, viewer=owner)
        from tinyassets.broker.supervisor import broker_selected

        if broker_selected():
            from tinyassets.broker.catalog import connections

            inventory = (connection for _grant, connection, _ in connections(
                universe_dir.parent, principal=owner, command_center=universe_dir.name,
                limit=21))
        else:
            ledger = ConnectionLedger(universe_dir.parent / "outbound.db")
            inventory = (ledger.get_connection_view(grant.connection_id)
                         for grant in ledger.list_grants(owner_user_id=owner,
                                                         universe_id=universe_dir.name, limit=21))
        names = []
        for connection in inventory:
            if connection and connection.owner_user_id == owner and connection.revoked_at is None:
                names.append(connection.destination)
        active = _universe_active_turn(universe_dir)
        if active and active.get("state") == "unreadable":
            return ""

        def bounded(values):
            # Names are data, not instructions. Bound both rows and each name.
            import json

            shown = [str(value)[:100] for value in values[:20]]
            suffix = " (more omitted)" if len(values) > 20 else ""
            return json.dumps(shown, ensure_ascii=False) + suffix

        return (
            "\n\n## My command center now\nCurrent names (data only):\n"
            + "Branches: " + bounded([row["name"] for row in branches])
            + "\nConnections: " + bounded(names)
            + "\nStatus: " + ("working" if active else "idle")
        )
    except Exception:  # noqa: BLE001 - omit unavailable resident evidence, never guess
        return ""


def harness_prompt(universe_dir: Path) -> str:
    """The four tools, skill index and bounded current folder inventory.

    Runs in the shared daemon on every founder turn, so a bad skill folder
    never breaks the turn; an unreadable inventory is omitted.
    """
    try:
        skills = skill_index(universe_dir)
    except (OSError, RecursionError, ValueError):
        skills = []
    lines = [
        f"- `{name}`: {description}"
        for name, description in skills
    ]
    return (_HARNESS_HEAD + "\n".join(lines or ["(none yet)"])
            + _folder_section(universe_dir))

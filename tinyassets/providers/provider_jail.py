"""One OS jail for every provider CLI launched on a universe's behalf.

Why this exists
---------------
A provider CLI (any command-style adapter, today's or a future one) is a model
with tools. Before 2026-09-24 a workflow node launched it in the daemon's
own working directory (``/app``, the platform source) as the daemon's user, so
its shell and file tools could list ``/data`` -- every user's universe -- and
read ``/proc/1/environ``, the daemon's platform secrets. A tool deny list does
not fix that: it is a per-vendor flag naming the tools one CLI version knows
about, and a hook, a subagent or a renamed tool walks around it.

What it does
------------
Every provider subprocess spawned through
:func:`tinyassets.providers.owned_process.aspawn_owned` while a universe is
bound (:func:`provider_launch_scope`) runs inside bubblewrap. The jail holds:

* the owning universe's own directory, read-write at its own path, so every
  path the provider environment already points at (its home, temp and
  credential directories) resolves unchanged;
* over it, an empty ``tmpfs`` on every hidden directory at the universe root
  except ``.runtime``: a CLI's own project settings directory is never a
  loading mechanism (the harness is vendor-neutral files the platform
  assembles), so a hook the universe's agent wrote there never runs beside the
  launch credential;
* over it, an empty ``tmpfs`` on ``.runtime/provider-launch-credentials``, with
  ONLY this launch's own credential snapshot bound back -- a concurrent launch's
  snapshot for another provider is not readable;
* the provider's own install tree, read-only, plus a fixed list of system
  paths (``/usr``, ``/bin``, ``/lib*``, CA certificates, name resolution);
* a private ``/tmp``, ``/dev`` and a ``/proc`` of its own pid namespace;
* NO network interface of its own: an empty network namespace, with the
  universe's checking egress proxy (:mod:`tinyassets.universe_egress`) as its
  only way out, the same floor the universe tool jail has. ``HTTP(S)_PROXY``
  point the CLI at an in-jail forwarder; a second, pinned relay reaches the
  universe's OWN engine MCP server on the daemon's loopback and nothing else
  there. Before 2026-10-01 a provider shared the container network: the
  daemon's loopback ports, other universes' engine ports, the cloud metadata
  address, the host and the log sidecar (concern
  ``2026-10-01-provider-jail-has-unfiltered-host-network``);
* the shared seccomp filter (:mod:`tinyassets.providers.jail_seccomp`) and
  in-jail rlimits on processes, open files, file size and core dumps.

Nothing else. Not ``/data`` or another universe, not ``/app``, not the daemon's
``/proc``, not the host credential homes. Everything the CLI starts -- a hook,
an MCP stdio server, a shell tool -- is a descendant inside the same
namespaces.

The key is the OWNING UNIVERSE, not the vendor. The router binds it around every
provider call (``provider_launch_scope``), and the shared spawn point reads it,
so a provider inherits the jail by spawning through ``aspawn_owned`` and needs
no code of its own. An adapter MAY narrow what the universe looks like inside
the jail (:class:`UniverseView`: a chat turn may see an empty workspace), but
every bind it asks for must come from inside the owning universe, so it can
never widen the jail.

Fail closed
-----------
* No bubblewrap, or a host where the probe cannot create a namespace (including
  every non-Linux host): :class:`ProviderConfinementError` before anything is
  spawned. There is no unconfined fallback.
* A provider launch the router made with NO owning universe (a host-authority
  call such as a leaderboard selector run) is refused the same way. Such a call
  ran published, possibly foreign, graph content with the host's credentials
  and every tool; there is no universe to confine it to.
* A process spawned outside any scope (not a provider call made through the
  router: the tests of this module's process-family machinery) is unchanged.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from tinyassets import jail_disk
from tinyassets.exceptions import ProviderAuthorityHeldError

__all__ = [
    "BWRAP_RESOLVER",
    "ConfinedLaunch",
    "JailMount",
    "ProviderConfinementError",
    "UniverseView",
    "confine_launch",
    "default_view",
    "jail_argv",
    "hidden_root_masks",
    "launch_is_confined",
    "provider_launch_scope",
]


class ProviderConfinementError(ProviderAuthorityHeldError):
    """A provider launch was refused because it could not be jailed.

    Raised BEFORE any process exists, so it is never evidence about the
    provider or its credential: the router re-raises it without a cooldown and
    releases what it reserved. A subclass of :class:`ProviderAuthorityHeldError`
    because that is the contract every caller already honours for "this call
    may not run here": it is never folded into a fallback response.
    """

    failure_class = "provider_confinement_unavailable"
    #: Every raise starts with this, so a stored error string stays classifiable.
    MESSAGE = "provider launch refused: it cannot be confined to its command center"


@dataclass(frozen=True, slots=True)
class _LaunchScope:
    universe_dir: Path | None
    credential_dir: Path | None
    #: ``(actor_id, graph_id)`` whose engine MCP route this call may reach.
    engine_route: tuple[str, str] | None = None


_SCOPE: ContextVar[_LaunchScope | None] = ContextVar(
    "tinyassets_provider_launch_scope", default=None,
)


@contextlib.contextmanager
def provider_launch_scope(
    universe_dir: str | Path | None,
    *,
    credential_dir: str | Path | None = None,
    engine_route: tuple[str, str] | None = None,
) -> Iterator[None]:
    """Bind the universe that owns every provider process launched inside.

    ``universe_dir=None`` is a binding too: it says "this provider call has no
    owning universe", and any process it tries to launch is refused. The router
    enters this around each ``provider.complete``; a context variable carries it
    to the spawn point through ``await`` and into tasks the call creates.
    ``engine_route`` names the owner and universe whose engine MCP server the
    jail may reach through its pinned loopback relay; ``None`` reaches none.
    """
    scope = _LaunchScope(
        universe_dir=None if universe_dir is None else Path(universe_dir),
        credential_dir=None if credential_dir is None else Path(credential_dir),
        engine_route=engine_route,
    )
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def launch_is_confined() -> bool:
    """Whether a provider process launched right now would be OS-jailed.

    True exactly when the active scope names an owning universe -- the same
    condition under which :func:`confine_launch` builds a jail (a scope with no
    universe is refused, not jailed). An adapter reads this to drop its OWN,
    nested sandbox when ours is the boundary: a second sandbox inside this one
    only adds attack surface, and a nested bubblewrap is what would force this
    jail's seccomp filter to keep user namespaces and symlinks open
    (``tinyassets.providers.jail_seccomp``).
    """
    scope = _SCOPE.get()
    return scope is not None and scope.universe_dir is not None


@dataclass(frozen=True, slots=True)
class JailMount:
    """One bubblewrap mount operation: ``bind``, ``ro-bind``, ``tmpfs``,
    ``bind-try`` / ``ro-bind-try`` (skipped when the source is gone by the
    time the jail starts) or ``remount-ro`` (``dest`` itself read-only; the
    mounts already under it keep their own flags; no source)."""

    op: str
    dest: str
    source: Path | None = None


@dataclass(frozen=True, slots=True)
class UniverseView:
    """What the owning universe looks like inside the jail.

    Every ``bind``/``ro-bind`` source must resolve inside ``universe_dir``; the
    jail refuses the launch otherwise. ``setenv`` overrides the provider env
    inside the jail only (e.g. a credential home mounted at a fixed path).
    """

    universe_dir: Path
    mounts: tuple[JailMount, ...]
    chdir: str | None = None
    setenv: tuple[tuple[str, str], ...] = ()


#: Read-only system paths every CLI may need to execute and reach the network.
#: Public by construction: binaries, libraries, CA bundles, resolver config and
#: the account database (``/etc/passwd`` carries no secret; ``/etc/shadow`` is
#: not here). Missing paths are skipped.
_SYSTEM_RO_PATHS: tuple[str, ...] = (
    "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32",
    "/etc/alternatives", "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts",
    "/etc/nsswitch.conf", "/etc/host.conf", "/etc/gai.conf", "/etc/passwd",
    "/etc/group", "/etc/localtime", "/etc/ld.so.cache",
)

#: Roots a view may never mount over, and install mounts may never sit under
#: or above.
_RESERVED_DESTS: tuple[str, ...] = (
    "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc",
    "/proc", "/dev",
)

#: Where every universe keeps its per-launch credential snapshots.
_LAUNCH_CREDENTIALS = Path(".runtime") / "provider-launch-credentials"

#: The platform-owned directory in every universe. Never masked wholesale by
#: :func:`hidden_root_masks`: a launch needs its provider home and its own
#: credential snapshot from under it.
PLATFORM_RUNTIME_DIR = _LAUNCH_CREDENTIALS.parts[0]

#: Masks a hidden root FILE: a read-only bind of ``/dev/null`` over it, so the
#: provider reads it empty, cannot write through the bind and cannot replace the
#: file (or plant a link in its place) while the mount holds it. A tmpfs can
#: only mask a directory, so files need this instead.
_NULL_MASK = Path("/dev/null")


def _refuse(detail: str) -> ProviderConfinementError:
    return ProviderConfinementError(f"{ProviderConfinementError.MESSAGE}: {detail}")


def _resolve_bwrap() -> str:
    """The bubblewrap path this host proved it can jail with, or refuse."""
    from tinyassets.providers.base import get_sandbox_status

    status = get_sandbox_status() or {}
    if not status.get("bwrap_available"):
        raise _refuse(f"no OS sandbox on this host ({status.get('reason') or 'bwrap unavailable'})")
    path = status.get("bwrap_path") or shutil.which("bwrap")
    if not path:
        raise _refuse("bwrap passed the probe but is not on PATH")
    return str(path)


#: Resolves the bubblewrap binary for a jailed launch. Substituted by tests
#: (injection, not an env switch); production never replaces it.
BWRAP_RESOLVER: Callable[[], str] = _resolve_bwrap


def _within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _overlaps(a: Path, b: Path) -> bool:
    return _within(a, b) or _within(b, a)


def _covered(path: str, roots: Iterable[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)


#: The universe agent's own workspace, inside the universe: its tool jail's
#: ``/u`` (harness W2). Hidden, so every provider launch masks it.
AGENT_WORKSPACE_DIR = ".agent-workspace"


def ensure_agent_workspace(universe_dir: Path) -> Path:
    """The universe's agent workspace, created if absent, never a link.

    Created BEFORE any launch is built, provider or tool: a provider launch
    then always finds it present and masks it, so a process in a workflow's
    jail can never create the name first (as a link to another universe)
    for the tool jail to bind as ``/u`` (gpt-6-astra on #4194).
    """
    path = Path(universe_dir) / AGENT_WORKSPACE_DIR
    try:
        path.mkdir(mode=0o755)
    except FileExistsError:
        pass
    if path.is_symlink() or not path.is_dir():
        raise _refuse(f"the universe's {AGENT_WORKSPACE_DIR} is not a plain directory")
    return path


def default_view(
    universe_dir: Path,
    *,
    credential_dir: Path | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> UniverseView:
    """The universe read-write at its own path, other launch snapshots masked."""
    root = universe_dir.resolve(strict=False)
    if root.is_dir():
        ensure_agent_workspace(root)
        # Reserve the credential materialization name BEFORE taking the hidden
        # root inventory. Otherwise a provider can create it on first launch
        # and persist arbitrary bytes in this account-exempt directory.
        from tinyassets.credential_vault import CREDENTIAL_ARTIFACT_DIR

        for name in (CREDENTIAL_ARTIFACT_DIR, ".workspace-staging", PLATFORM_RUNTIME_DIR):
            directory = root / name
            if directory.is_symlink():
                raise _refuse("platform runtime is not a plain directory")
            directory.mkdir(mode=0o700, exist_ok=True)
            if not directory.is_dir():
                raise _refuse("platform runtime is not a plain directory")
    mounts = [JailMount("bind", str(root), root)]
    runtime = root / PLATFORM_RUNTIME_DIR
    if runtime.is_dir():
        # CLI homes/caches are disposable per launch. The rest of runtime stays
        # writable for legacy CLI homes and native sessions. All on-disk runtime
        # is charged, including any cache content hidden under this tmpfs.
        child = runtime / "provider-child"
        if child.is_symlink():
            raise _refuse("provider runtime home is not a plain directory")
        child.mkdir(mode=0o700, exist_ok=True)
        if child.is_dir():
            mounts.append(JailMount("tmpfs", str(child)))
            for value in sorted(set((env or {}).values())):
                path = Path(value)
                if path.is_relative_to(child) and path != child:
                    mounts.append(JailMount("dir", str(path)))
    launch_root = root / _LAUNCH_CREDENTIALS
    if launch_root.is_dir():
        mounts.append(JailMount("tmpfs", str(launch_root)))
    mounts.extend(hidden_root_masks(root))
    if credential_dir is not None:
        own = credential_dir.resolve(strict=False)
        # A launch snapshot is a directory UNDER the platform runtime dir, never
        # the universe root itself: a rebind of the root after the masks would
        # re-expose every hidden entry the masks just hid (gpt-6-astra refute,
        # 2026-10-01). So the rebind is accepted only for a strict descendant of
        # ``.runtime``.
        if own.is_dir() and own != launch_root and _within(own, launch_root):
            # Bound back read-write: the CLI writes its lock / session files
            # beside the credential exactly as it did before the jail.
            mounts.append(JailMount("bind", str(own), own))
    chdir = str(root)
    if cwd:
        resolved_cwd = Path(cwd).resolve(strict=False)
        if _within(resolved_cwd, root):
            chdir = str(resolved_cwd)
    return UniverseView(universe_dir=root, mounts=tuple(mounts), chdir=chdir)


#: Host runtime directories every jailed child gets a disposable value for.
#: Operating-system names only: the jail knows no vendor, so an executor's own
#: auth directory variables arrive as ``auth_env_names``
#: (``scripts/check_channel_agnostic.py``).
_DISPOSABLE_RUNTIME_ENV: tuple[str, ...] = (
    "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
    "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
)


def metadata_view(
    universe_dir: Path, snapshot_dir: Path, env: Mapping[str, str],
    auth_env_names: Sequence[str] = (),
) -> UniverseView:
    """Only this owned launch snapshot, with disposable homes inside the jail.

    Custody is established by native_discovery before this call. Path checks
    here prevent a missing, redirected or broad snapshot from becoming a bind.
    Ordinary universe content and sibling launch snapshots are never mounted.

    ``auth_env_names`` are the executor's auth DIRECTORY variables, supplied by
    the provider layer because this module is channel-agnostic and must not name
    a vendor. Each is given a disposable path unless it already names the exact
    snapshot bound below, so an inherited host auth directory cannot reach the
    child whichever executor declared it.
    """
    snapshot = Path(os.path.abspath(snapshot_dir))
    try:
        root = Path(universe_dir).resolve(strict=True)
        resolved_snapshot = snapshot.resolve(strict=True)
    except (OSError, RuntimeError):
        # pathlib uses RuntimeError for symlink loops on supported Python
        # versions. Never expose the private path carried by that exception.
        raise _refuse("metadata requires its exact launch snapshot") from None
    launch_root = root / _LAUNCH_CREDENTIALS
    if (not root.is_dir() or snapshot.parent != launch_root
            or resolved_snapshot != snapshot or not snapshot.is_dir()):
        raise _refuse("metadata requires its exact launch snapshot")
    # HOME and scratch directories contain no persistent owner state. Preserve
    # an auth directory only when it names the exact snapshot mounted below.
    private_env = {"HOME": "/tmp", "USERPROFILE": "/tmp",
                   "TMPDIR": "/tmp", "TMP": "/tmp", "TEMP": "/tmp"}
    for name in (*_DISPOSABLE_RUNTIME_ENV, *auth_env_names):
        private_env[name] = str(snapshot) if env.get(name) == str(snapshot) else f"/tmp/{name}"
    return UniverseView(
        universe_dir=root, mounts=(JailMount("bind", str(snapshot), snapshot),),
        chdir=str(snapshot), setenv=tuple(private_env.items()),
    )


def hidden_root_masks(universe_dir: Path) -> list[JailMount]:
    """Masks over every hidden root entry except ``.runtime``, or refuse.

    The universe's harness is vendor-neutral, visible files the platform
    assembles for every adapter (``tinyassets.universe_tools``). A CLI started
    with the universe as its working directory would also load its OWN project
    settings directory from there -- hooks and permissions the universe's agent
    can now write with its own tools -- beside the owner's launch credential.

    Hidden root entries are also where the daemon keeps per-universe PLATFORM
    state that the provider must neither read nor forge: the credential vault
    (``.credential-vault.json``, ``.credentials/``), the consent, usage and run
    databases (``.runs.db`` and its ``-wal``/``-shm`` sidecars) and so on. The
    daemon reads and WRITES those from OUTSIDE the jail, so a provider that
    could replace one with a link (``.runs.db -> /data/<other>/.runs.db``) would
    steer the daemon's own ``sqlite3.connect`` into another universe.

    So every hidden root entry except ``.runtime`` is masked: a directory with
    an empty ``tmpfs``, a file with a read-only ``/dev/null`` bind
    (:data:`_NULL_MASK`). Either mask holds the name for the life of the jail,
    so the provider can neither read the entry nor swap it for a link. A hidden
    entry that is already a symlink cannot be masked by mounting over it (the
    mount would follow the link), so the launch is refused.
    """
    masks: list[JailMount] = []
    try:
        entries = sorted(os.scandir(universe_dir), key=lambda entry: entry.name)
    except OSError:
        return masks
    for entry in entries:
        if not entry.name.startswith(".") or entry.name == PLATFORM_RUNTIME_DIR:
            continue
        dest = str(Path(universe_dir) / entry.name)
        if entry.is_symlink():
            raise _refuse(f"the command center's {entry.name} is a link; it cannot be masked")
        if entry.is_dir(follow_symlinks=False):
            masks.append(JailMount("tmpfs", dest))
        else:
            masks.append(JailMount("ro-bind", dest, _NULL_MASK))
    return masks


#: Daemon-owned files that belong to one universe but must not live inside
#: it (its egress proxy socket): ``<data root>/.universe-sidecars/<universe>``.
#:
#: A jail binds exactly two things from here and nothing else: the egress proxy
#: socket, and the engine relay socket when a launch has one. Both are
#: constructed by :func:`_network` for that launch and passed to
#: :func:`jail_argv` as ``platform_sources``, which :func:`_validated_view`
#: admits as an EXACT set.
#:
#: This comment used to say "No jail binds that directory, so nothing a
#: universe runs can replace them". That was false, and a design was approved on
#: it: the validator allowed any source resolving under this folder, so a
#: provider could rename a directory the tool jail was about to bind read-write
#: and leave a link here in its place, landing a writable handle on daemon-owned
#: state. Corrected 2026-10-03 along with the rule itself. A directory prefix is
#: not a capability; the exact paths are.
UNIVERSE_SIDECARS_DIR = ".universe-sidecars"


def _validated_view(
    view: UniverseView, *, platform_sources: frozenset[Path] = frozenset()
) -> UniverseView:
    """``view`` with every bind source resolved ONCE and checked, or refuse.

    The argv binds the resolved path it was checked as, never a second
    resolution of the original name.

    ``platform_sources`` are the resolved paths THIS MODULE just constructed
    for the launch -- the egress proxy socket and, when there is one, the
    engine relay socket. They are the only sources outside the command center
    a view may bind. Everything else must resolve inside the command center.

    It is an exact set, not a directory prefix, and that distinction is the
    whole point: the sidecar folder used to be allowed wholesale, so a view
    whose source resolved anywhere under it was accepted. A provider could
    rename a directory the tool jail was about to bind read-write and leave a
    link to the sidecar folder in its place; the resolution landed inside the
    allowed prefix, and the command center got a writable handle on platform
    state -- including the consent database that decides what it may do.
    """
    root = view.universe_dir.resolve(strict=False)
    checked: list[JailMount] = []
    for mount in view.mounts:
        if mount.op not in ("bind", "ro-bind", "bind-try", "ro-bind-try", "tmpfs",
                            "remount-ro", "dir"):
            raise _refuse(f"unknown mount operation {mount.op!r}")
        dest = mount.dest
        if not dest.startswith("/") or dest.rstrip("/") == "" or _covered(dest, _RESERVED_DESTS):
            raise _refuse(f"a view may not mount at {dest!r}")
        if mount.op in ("tmpfs", "remount-ro", "dir"):
            checked.append(mount)
            continue
        if mount.source is None:
            raise _refuse("a bind needs a source")
        # A read-only /dev/null is the file mask (see _NULL_MASK): not a path
        # inside the universe, but a device that reveals and carries nothing.
        if mount.op == "ro-bind" and mount.source == _NULL_MASK:
            checked.append(mount)
            continue
        try:
            source = mount.source.resolve(strict=not mount.op.endswith("-try"))
        except OSError:
            raise _refuse("a bind source does not exist") from None
        if not (_within(source, root) or source in platform_sources):
            raise _refuse("a view may only bind paths inside its own command center")
        checked.append(JailMount(mount.op, dest, source))
    for name, _value in view.setenv:
        if not name or "=" in name:
            raise _refuse("invalid jail environment name")
    return UniverseView(
        universe_dir=view.universe_dir, mounts=tuple(checked),
        chdir=view.chdir, setenv=view.setenv,
    )


def _command_install_paths(argv0: str, env: Mapping[str, str] | None) -> list[Path]:
    """Where the command lives: its own directory and its resolved package tree."""
    located = argv0 if "/" in argv0 else shutil.which(argv0, path=(env or {}).get("PATH"))
    if not located:
        return []
    wrapper = Path(os.path.abspath(located))
    paths = [wrapper.parent]
    try:
        real = wrapper.resolve(strict=True)
    except OSError:
        return paths
    tree = real.parent
    for ancestor in real.parents:
        if ancestor.name == "node_modules":
            tree = ancestor.parent
            break
    paths.append(tree)
    return paths


def _forbidden_install_roots(view: UniverseView) -> list[Path]:
    """Trees an install mount must never reach: every universe and the source."""
    import tinyassets
    from tinyassets.storage import data_dir

    roots = [
        Path(tinyassets.__file__).resolve().parent.parent,
        view.universe_dir.resolve(strict=False).parent,
    ]
    try:
        roots.append(Path(data_dir()).resolve(strict=False))
    except Exception:  # noqa: BLE001 - an unresolvable data dir adds no root
        pass
    return roots


def _install_binds(
    paths: Iterable[Path], view: UniverseView, already: list[str],
) -> list[str]:
    forbidden = _forbidden_install_roots(view)
    argv: list[str] = []
    for raw in paths:
        try:
            path = Path(raw).resolve(strict=True)
        except OSError:
            continue
        text = str(path)
        if text == "/" or _covered(text, already):
            continue
        if any(_overlaps(path, root) for root in forbidden):
            raise _refuse("a provider install path overlaps command center data or platform source")
        if _covered(text, ("/etc", "/proc", "/dev")):
            raise _refuse("a provider install path sits under a reserved system root")
        argv.extend(("--ro-bind", text, text))
        already.append(text)
    return argv


def _ca_file_binds(
    env: Mapping[str, str] | None, view: UniverseView, already: list[str],
) -> list[str]:
    """A CA bundle the provider env names, read-only, when not already visible.

    Public certificate files only; one that would sit in a universe or the
    source tree is left out rather than exposing that tree.
    """
    # The one list of CA-file variables a provider child keeps.
    from tinyassets.providers.base import _PROVIDER_CHILD_CA_FILE_ENV_VARS

    forbidden = _forbidden_install_roots(view)
    argv: list[str] = []
    for name in _PROVIDER_CHILD_CA_FILE_ENV_VARS:
        value = (env or {}).get(name)
        if not value or not os.path.isabs(value) or not os.path.isfile(value):
            continue
        path = Path(value).resolve(strict=False)
        text = str(path)
        if _covered(text, already) or _covered(text, ("/proc", "/dev")):
            continue
        if any(_within(path, root) for root in forbidden):
            continue
        argv.extend(("--ro-bind", text, text))
        already.append(text)
    return argv


def jail_argv(
    argv: Sequence[str],
    view: UniverseView,
    *,
    bwrap_path: str,
    install_paths: Iterable[Path] = (),
    env: Mapping[str, str] | None = None,
    clearenv: bool = False,
    seccomp_fd: int | None = None,
    platform_sources: frozenset[Path] = frozenset(),
    tmp_bytes: int = jail_disk.TMP_BYTES,
) -> list[str]:
    """The bubblewrap argv that runs ``argv`` inside ``view``. Pure of policy.

    Order matters and is fixed: namespaces, a private ``/tmp``, the system
    paths, the provider install tree, then the universe view (so a view mount
    under ``/tmp`` lands on the private tmpfs, and a mask lands on the bind it
    masks), then the environment overrides and the working directory.

    The jail always has its own empty network namespace (loopback only,
    nothing listening); a caller gives it a way out only by binding the egress
    socket, as :func:`confine_launch` does. ``clearenv=True`` starts
    the jailed process from an empty environment plus ``view.setenv``.
    ``seccomp_fd`` is an inherited descriptor holding a compiled seccomp filter
    for the jailed process.

    Every tmpfs is sized: the private ``/tmp`` to ``tmp_bytes`` and each view
    tmpfs to ``jail_disk.MASK_TMPFS_BYTES``. A tmpfs is RAM, and an unsized one
    defaults to half of it -- on a shared box that is one jail's scratch space
    competing with every user's daemon memory.
    """
    view = _validated_view(view, platform_sources=platform_sources)
    out: list[str] = [
        bwrap_path,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
    ]
    if clearenv:
        out.append("--clearenv")
    if seccomp_fd is not None:
        out.extend(("--seccomp", str(int(seccomp_fd))))
    out.extend((
        "--dev", "/dev",
        "--proc", "/proc",
        "--size", str(int(tmp_bytes)), "--tmpfs", "/tmp",
    ))
    bound: list[str] = []
    for system_path in _SYSTEM_RO_PATHS:
        if os.path.lexists(system_path):
            out.extend(("--ro-bind", system_path, system_path))
            bound.append(system_path)
    out.extend(_install_binds(install_paths, view, bound))
    out.extend(_ca_file_binds(env, view, bound))
    for mount in view.mounts:
        if mount.op == "tmpfs":
            # CLI homes do real cache work, unlike empty authority masks. Give
            # them the same bounded scratch capacity as the private /tmp.
            cache_home = str(view.universe_dir / PLATFORM_RUNTIME_DIR / "provider-child")
            size = tmp_bytes if mount.dest == cache_home else jail_disk.MASK_TMPFS_BYTES
            out.extend(("--size", str(size), "--tmpfs", mount.dest))
        elif mount.op in ("remount-ro", "dir"):
            out.extend((f"--{mount.op}", mount.dest))
        else:
            # Resolved and checked by _validated_view; a ``-try`` source that
            # is gone by launch is skipped by bubblewrap.
            out.extend((f"--{mount.op}", str(mount.source), mount.dest))
    for name, value in view.setenv:
        out.extend(("--setenv", name, value))
    out.extend(("--chdir", view.chdir or str(view.universe_dir)))
    out.append("--")
    out.extend(argv)
    return out


@dataclass(frozen=True, slots=True)
class ConfinedLaunch:
    """A jailed argv and the descriptors the spawn must hand to it.

    ``pass_fds`` holds the seccomp filter bubblewrap reads at start. The
    spawner passes them to the child and then closes its own copies
    (:meth:`close`), whether or not the spawn succeeded.
    """

    argv: list[str]
    pass_fds: tuple[int, ...] = ()
    #: The command center directory the jail confines it to: the spawn point
    #: opens the launch's disk budget there (`tinyassets.jail_disk`).
    universe_dir: Path | None = None

    def close(self) -> None:
        for fd in self.pass_fds:
            with contextlib.suppress(OSError):
                os.close(fd)


#: Limits applied inside the jail by ``prlimit``, after the jail's user
#: namespace exists, so ``RLIMIT_NPROC`` counts THIS jail's tasks (kernel >=
#: 5.14), not the daemon user's. Each is only ever lowered, never raised past
#: the daemon's own hard limit. There is deliberately no ``RLIMIT_AS``: a
#: JavaScript CLI reserves far more address space than it uses, so any cap that
#: means something kills it at start. There is no ``RLIMIT_CPU`` either: a turn
#: runs until it is finished, and a long agentic turn mostly waits on the
#: network.
PROVIDER_LIMITS: tuple[tuple[str, str, int], ...] = (
    ("--nproc", "RLIMIT_NPROC", 512),
    ("--nofile", "RLIMIT_NOFILE", 8192),
    ("--core", "RLIMIT_CORE", 0),
)


def _limit_args() -> list[str]:
    import resource

    args = []
    for flag, name, value in PROVIDER_LIMITS:
        _soft, hard = resource.getrlimit(getattr(resource, name))
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        args.append(f"{flag}={value}")
    return args


def _system_binary(name: str) -> str:
    found = shutil.which(name, path="/usr/bin:/bin")
    if not found:
        raise _refuse(f"{name} is not installed on this host, so the jail cannot apply its limits")
    return found


def _forwarder_python() -> tuple[str, list[Path]]:
    """A Python the jail can run the network forwarder with, and what to bind."""
    import sys

    system = shutil.which("python3", path="/usr/bin:/bin")
    if system:
        return system, []
    real = Path(os.path.realpath(sys.executable))
    if real.is_file():
        return str(real), [real.parent.parent]
    raise _refuse("no Python is available to run the jail's network forwarder")


def _network(
    view: UniverseView, scope: _LaunchScope | None,
) -> tuple[list[JailMount], int | None]:
    """The egress socket (and the engine relay) this launch binds, or refuse.

    There is no unfiltered fallback: a host where the proxy cannot start runs
    no provider at all.
    """
    from tinyassets import universe_egress

    try:
        egress = universe_egress.ensure_proxy(view.universe_dir)
    except OSError as exc:
        raise _refuse(f"the universe's egress proxy could not start ({exc})") from None
    if egress is None:
        raise _refuse("this host cannot run the universe's egress proxy")
    mounts = [JailMount("bind", universe_egress.JAIL_SOCKET, egress)]
    engine_port = None
    if scope is not None and scope.engine_route is not None:
        actor_id, graph_id = scope.engine_route
        try:
            relay = universe_egress.ensure_engine_relay(
                view.universe_dir, actor_id=actor_id, graph_id=graph_id,
            )
        except OSError as exc:
            raise _refuse(f"the universe's engine relay could not start ({exc})") from None
        if relay is not None:
            socket_path, engine_port = relay
            mounts.append(JailMount("bind", universe_egress.JAIL_ENGINE_SOCKET, socket_path))
    return mounts, engine_port


def confine_launch(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    view: UniverseView | None = None,
    install_mounts: Callable[[], Iterable[Path]] | None = None,
    nested_sandbox: bool = False,
) -> ConfinedLaunch | None:
    """The jailed launch, ``None`` when no jail applies, or refuse.

    Called by the shared spawn point for EVERY provider process. The decision
    reads only the bound scope and the adapter's optional view -- never the
    vendor, the config or the command. Inside the jail the command runs under
    ``prlimit``, behind the egress forwarder, with the seccomp filter loaded.

    ``nested_sandbox=True`` is the adapter declaring that its CLI builds its
    own sandbox inside this one (a served codex turn keeps ``--sandbox
    workspace-write`` for its ``apply_patch`` helper). That launch gets the
    filter profile keeping new user namespaces and symlinks open; every other
    launch gets the full deny profile (:mod:`tinyassets.providers.jail_seccomp`).
    """
    scope = _SCOPE.get()
    if scope is None and view is None:
        return None
    if scope is not None and scope.universe_dir is None:
        raise _refuse(
            "this provider call has no owning command center, so there is no "
            "directory to confine it to; it will not run on the host"
        )
    if view is None:
        view = default_view(
            scope.universe_dir,
            credential_dir=scope.credential_dir,
            cwd=None if cwd is None else os.fspath(cwd),
            env=env,
        )
    elif scope is not None and (
        view.universe_dir.resolve(strict=False)
        != scope.universe_dir.resolve(strict=False)
    ):
        raise _refuse("the adapter's view names a different command center than its call")
    if not view.universe_dir.resolve(strict=False).is_dir():
        raise _refuse("the owning command center directory does not exist")
    bwrap_path = BWRAP_RESOLVER()
    install_paths = [*_command_install_paths(str(argv[0]), env)] if argv else []
    if install_mounts is not None:
        install_paths.extend(install_mounts())
    from tinyassets import universe_egress
    from tinyassets.providers.jail_seccomp import program_fd

    prlimit = _system_binary("prlimit")
    python, python_paths = _forwarder_python()
    install_paths.extend(python_paths)
    net_mounts, engine_port = _network(view, scope)
    # The ONLY sources outside the command center a view may bind: the sockets
    # this module just constructed for this launch. An exact set, resolved the
    # same way the validator resolves a source, so a link that merely lands
    # under the sidecar folder is not one of them.
    platform_sources = frozenset(
        mount.source.resolve(strict=False) for mount in net_mounts
        if mount.source is not None
    )
    # The proxy environment goes LAST, so nothing the provider env carried (an
    # inherited HTTPS_PROXY or NO_PROXY) can point around the forwarder.
    view = UniverseView(
        universe_dir=view.universe_dir,
        mounts=(*view.mounts, *net_mounts),
        chdir=view.chdir,
        setenv=(*view.setenv, *universe_egress.PROXY_ENV),
    )
    inner = [
        prlimit, *_limit_args(), "--",
        *universe_egress.forwarder_argv(python, list(argv), engine_port=engine_port),
    ]
    # The full deny profile (no new user namespaces, no symlinks) unless the
    # adapter declared a nested sandbox: a non-served codex call runs its
    # commands directly here with its own sandbox off, and claude has none, so
    # neither can plant a link the daemon would follow out of the universe. A
    # served codex turn keeps its own sandbox (apply_patch needs it); its link
    # residual is the daemon-side link-refusing reader/writer's (#4254).
    filter_fd = program_fd(nested_sandbox=nested_sandbox)
    seed_fd = None
    try:
        from tinyassets.starter_seeds import open_seed_boundary

        _, seed_fd = open_seed_boundary(view.universe_dir)
        jailed = jail_argv(
            inner, view, bwrap_path=bwrap_path, install_paths=install_paths, env=env,
            seccomp_fd=filter_fd, platform_sources=platform_sources,
        )
        jailed[1:1] = ["--sync-fd", str(seed_fd)]
    except BaseException:
        os.close(filter_fd)
        if seed_fd is not None:
            os.close(seed_fd)
        raise
    return ConfinedLaunch(jailed, (filter_fd, seed_fd), view.universe_dir.resolve(strict=False))

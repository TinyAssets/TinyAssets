"""The launch scope every provider process needs, and the shared jail pieces.

A provider CLI is a model with tools, so it never runs as the daemon. The
router binds the OWNING universe, its sealed launch snapshot and, for a served
turn, the owner's engine route around every provider call
(:func:`provider_launch_scope`); the shared spawn point
(:func:`tinyassets.providers.owned_process.aspawn_owned`) reads that scope and
starts the CLI in the owner's provider cell, or refuses with
:class:`ProviderConfinementError` before anything runs. The cell sees the
snapshot, the shipped install trees, the owner's egress proxy and its engine
relay, and nothing of ``/data``, ``/app`` state or another owner.

The key is the owning universe, not the vendor: an adapter names no mount,
working directory or sandbox of its own.

The rest of this module (:class:`UniverseView`, :func:`jail_argv`,
:func:`hidden_root_masks`, :func:`default_view`, :func:`metadata_view`) is the
bubblewrap view the universe tool jail builds on.
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
    "JailMount",
    "ProviderConfinementError",
    "UniverseView",
    "default_view",
    "jail_argv",
    "hidden_root_masks",
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
    from tinyassets.role_tools import prepare

    prepare(universe_dir, agent_id='workspace-preparation')
    return Path(universe_dir) / AGENT_WORKSPACE_DIR


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
    engine relay socket. They are the only sidecar sources a view may bind.

    It is an exact set, not a directory prefix, and that distinction is the
    whole point: the sidecar folder used to be allowed wholesale, so a view
    whose source resolved anywhere under it was accepted. A provider could
    rename a directory the tool jail was about to bind read-write and leave a
    link to the sidecar folder in its place; the resolution landed inside the
    allowed prefix, and the command center got a writable handle on platform
    state -- including the consent database that decides what it may do.

    Another owner's tree needs no check here: the jail runs as its owner in
    that owner's cell, and the kernel refuses the bind (design section 5).
    """
    sidecars = view.universe_dir.resolve(strict=False).parent / UNIVERSE_SIDECARS_DIR
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
        if (_overlaps(source, sidecars)
                and source not in platform_sources):
            raise _refuse("a view may not bind platform sidecar state")
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

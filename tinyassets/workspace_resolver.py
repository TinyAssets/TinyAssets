"""Staging and command construction for the provisioning resolver (design D3).

This module decides **what** the resolver will run and **which bytes** it will
run against; it never runs anything. No process is started, no network address
is opened, and no ambient configuration is read -- the caller executes the argv
this returns, inside the resolver jail, with the environment this returns. Doing
it here keeps the part that can be tested exhaustively away from the part that
needs a jail and a network.

Staging is where the digest earns its keep. An admitted plan carries the exact
text that was validated; :func:`stage_python_plan` and :func:`stage_node_plan`
write that text, **read it back**, and refuse unless the sha256 of the bytes now
on disk equals the plan's digest. Anything between admission and the installer
-- a truncated write, a full disk, a symlinked staging path, a second writer --
becomes a loud failure instead of an install of something nobody admitted.

Every argv element is checked before it is returned. A path that begins with a
dash is refused rather than emitted, because ``pip`` and ``npm`` would read it
as an option: a cache directory called ``--index-url`` is not a directory, it is
an argument injection. Only the fixed flags these builders themselves emit may
begin with a dash (:data:`FIXED_FLAGS`); everything else must be an absolute,
NUL-free path.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from tinyassets.workspace_provision import NodePlan, PythonPlan

__all__ = [
    "FIXED_FLAGS",
    "NULL_DEVICE",
    "REGISTRY_PROXY_URL",
    "PYTHON_MANIFEST_NAME",
    "ResolverError",
    "ProvisionManifests",
    "StagedManifest",
    "StagedNodeManifests",
    "npm_fetch_argv",
    "npm_offline_install_argv",
    "pip_download_argv",
    "pip_offline_install_argv",
    "resolver_environment",
    "read_provision_manifests",
    "stage_node_plan",
    "stage_python_plan",
]

#: The admitted requirement text, under a name no manifest in a repository uses,
#: so nothing in a checkout can be mistaken for it.
PYTHON_MANIFEST_NAME: Final[str] = "requirements.canonical.txt"
NODE_MANIFEST_NAME: Final[str] = "package.json"
NODE_LOCKFILE_NAME: Final[str] = "package-lock.json"

DEFAULT_INDEX_URL: Final[str] = "https://pypi.org/simple"
NPM_REGISTRY_URL: Final[str] = "https://registry.npmjs.org"
# This listener exists ONLY inside the resolver's private network namespace.
# No workflow input or ambient proxy may replace it. Offline argv omits it.
REGISTRY_PROXY_URL: Final[str] = "http://127.0.0.1:3128"

#: ``npm`` is told to read its user config from the null device rather than the
#: home directory: the resolver jail has a home, and a config file appearing in
#: it must not be able to add a registry.
NULL_DEVICE: Final[str] = "NUL" if os.name == "nt" else "/dev/null"

#: Every dash-leading token these builders emit. An argv element starting with a
#: dash and not in this set is an injected option wearing a path's clothes.
FIXED_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "-I",
        "-m",
        "-r",
        "--isolated",
        "--disable-pip-version-check",
        "--no-input",
        "--only-binary=:all:",
        "--require-hashes",
        "--index-url",
        "--dest",
        "--no-index",
        "--find-links",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--cache",
        "--userconfig",
        "--registry",
        "--prefix",
        "--offline",
        "--proxy",
        "--https-proxy",
        "--noproxy=",
    }
)

_MAX_PATH_CHARS: Final[int] = 4096


class ResolverError(RuntimeError):
    """A staging or command-construction invariant failed.

    Not a user-facing refusal: by the time this module runs, the manifest was
    already admitted. This is the platform failing to hand the installer what
    the plan says, which is a bug or an interference, and either way not
    something to continue past.
    """


@dataclass(frozen=True, slots=True)
class ProvisionManifests:
    """All declared inputs admitted together, before any resolver can run."""

    python: PythonPlan | None
    node: NodePlan | None


def read_provision_manifests(
    repo_fd: int | None = None, *, python_path: str | None = None, node: bool = False,
    reader: Callable[[str, int], bytes] | None = None,
) -> ProvisionManifests:
    """Extract only bounded regular manifests from the checkout.

    ``reader(path, bound)`` reads one regular file beneath the checkout (the
    admitted owner box's file API); ``repo_fd`` is the held-descriptor form for
    a local checkout. This reads no credentials and starts no process or
    network operation. The returned canonical plans, not the original checkout
    files, feed staging. A failure never returns a partial plan. The caller
    must check provisioning consent before invoking this reader.
    """
    from tinyassets import workspace_fs
    from tinyassets.workspace_provision import (
        ProvisionRefused,
        admit_manifest_bytes,
        admit_node,
        admit_requirements,
    )

    if type(node) is not bool:
        raise ValueError("node provisioning must be boolean")
    if python_path is not None and (not isinstance(python_path, str) or not python_path):
        raise ValueError("python provisioning must name a manifest")

    if (reader is None) == (repo_fd is None):
        raise ValueError("provisioning manifests need exactly one reader")

    def read(path: str, bound: int, *, lockfile: bool = False) -> str:
        try:
            if reader is not None:
                data = reader(path, bound)
            else:
                data = workspace_fs.read_regular_file_beneath(repo_fd, path, max_bytes=bound)
        except FileNotFoundError:
            reason = "missing_lockfile" if lockfile else "not_regular_file"
            raise ProvisionRefused(reason, "required manifest is unavailable") from None
        except OSError:
            # Reader exceptions can contain private absolute paths. They never
            # become evidence or a guessed successful/empty manifest.
            raise ProvisionRefused(
                "not_regular_file", "manifest is not a bounded regular file beneath the checkout"
            ) from None
        return admit_manifest_bytes(data, max_bytes=bound)

    python_plan = None
    node_plan = None
    if python_path is not None:
        python_plan = admit_requirements(read(python_path, 256 * 1024))
    if node:
        package = read(NODE_MANIFEST_NAME, 4 * 1024 * 1024)
        lock = read(NODE_LOCKFILE_NAME, 4 * 1024 * 1024, lockfile=True)
        node_plan = admit_node(package, lock)
    return ProvisionManifests(python=python_plan, node=node_plan)


@dataclass(frozen=True)
class StagedManifest:
    """The admitted Python text on disk, and the digest the bytes there hash to."""

    path: Path
    digest: str


@dataclass(frozen=True)
class StagedNodeManifests:
    """The admitted Node manifests on disk, and the digest binding the pair."""

    directory: Path
    package_json: Path
    lockfile: Path
    digest: str


# --------------------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------------------


def _staging_directory(staging_dir: Path | str) -> Path:
    directory = Path(staging_dir)
    if not directory.is_absolute():
        raise ResolverError(f"staging directory must be absolute: {directory!s}")
    resolved = Path(os.path.realpath(str(directory)))
    if not resolved.is_dir():
        raise ResolverError(f"staging directory does not exist: {directory!s}")
    return resolved


def _staged_path(directory: Path, name: str) -> Path:
    target = Path(os.path.realpath(str(directory / name)))
    root = str(directory)
    if str(target) != root and not str(target).startswith(root + os.sep):
        raise ResolverError(f"staged file escapes the staging directory: {name}")
    return target


def _write_exact(target: Path, data: bytes) -> bytes:
    """Create *target* exclusively, write *data*, and read back what landed.

    Exclusive creation: staging is fresh per job, so a file already there is
    either a second writer or something planted, and both are refusals. The
    read-back is what makes the digest check mean "the installer will see these
    bytes" rather than "we intended these bytes".
    """
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        handle = os.open(str(target), flags, 0o600)
    except FileExistsError as exc:
        raise ResolverError(f"staged file already exists: {target.name}") from exc
    except OSError as exc:
        raise ResolverError(f"cannot stage {target.name}: {exc}") from exc
    try:
        written = 0
        while written < len(data):
            written += os.write(handle, data[written:])
    finally:
        os.close(handle)
    return target.read_bytes()


def stage_python_plan(plan: Any, staging_dir: Path | str) -> StagedManifest:
    """Write the admitted requirement text, or refuse.

    The digest is recomputed from the bytes on disk and compared with the plan's:
    the offline install is bound to what admission validated, and that binding is
    only real if the file the installer reads is the file that was hashed.
    """
    directory = _staging_directory(staging_dir)
    target = _staged_path(directory, PYTHON_MANIFEST_NAME)
    landed = _write_exact(target, plan.normalized_text.encode("utf-8"))
    digest = hashlib.sha256(landed).hexdigest()
    if digest != plan.digest:
        raise ResolverError(
            f"staged requirements digest {digest} does not match the admitted plan "
            f"{plan.digest}: the installer would read something that was not admitted"
        )
    return StagedManifest(path=target, digest=digest)


def stage_node_plan(plan: Any, staging_dir: Path | str) -> StagedNodeManifests:
    """Write the admitted package.json and lockfile, or refuse."""
    directory = _staging_directory(staging_dir)
    manifest_path = _staged_path(directory, NODE_MANIFEST_NAME)
    lockfile_path = _staged_path(directory, NODE_LOCKFILE_NAME)
    manifest_bytes = _write_exact(
        manifest_path, plan.normalized_package_json.encode("utf-8")
    )
    lockfile_bytes = _write_exact(
        lockfile_path, plan.normalized_lockfile.encode("utf-8")
    )
    digest = hashlib.sha256(manifest_bytes + b"\x00" + lockfile_bytes).hexdigest()
    if digest != plan.digest:
        raise ResolverError(
            f"staged node digest {digest} does not match the admitted plan "
            f"{plan.digest}: the installer would read something that was not admitted"
        )
    return StagedNodeManifests(
        directory=directory,
        package_json=manifest_path,
        lockfile=lockfile_path,
        digest=digest,
    )


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def _check_path(value: Path | str, label: str) -> str:
    """An absolute, NUL-free path that cannot be read as an option."""
    text = str(value)
    if not text:
        raise ResolverError(f"{label} is empty")
    if chr(0) in text:
        raise ResolverError(f"{label} contains a NUL byte")
    if len(text) > _MAX_PATH_CHARS:
        raise ResolverError(f"{label} is longer than {_MAX_PATH_CHARS} characters")
    if text.startswith("-"):
        raise ResolverError(
            f"{label} begins with a dash and would be read as an option: {text!r}"
        )
    if not Path(text).is_absolute():
        raise ResolverError(f"{label} must be absolute: {text!r}")
    return text


def _check_program(value: Path | str, label: str) -> str:
    """An executable: an absolute path, or a bare name for the jail's PATH.

    A bare name is admitted because that is how ``npm`` and the interpreter are
    normally invoked and :func:`resolver_environment` is what decides the PATH
    it resolves against. What is never admitted is a name carrying a directory
    separator without being absolute (``../npm``) or a dash-leading name.
    """
    text = str(value)
    if not text:
        raise ResolverError(f"{label} is empty")
    if chr(0) in text:
        raise ResolverError(f"{label} contains a NUL byte")
    if text.startswith("-"):
        raise ResolverError(
            f"{label} begins with a dash and would be read as an option: {text!r}"
        )
    candidate = Path(text)
    if candidate.is_absolute():
        return _check_path(text, label)
    if "/" in text or "\\" in text:
        raise ResolverError(
            f"{label} must be absolute or a bare program name, got {text!r}"
        )
    return text


def _check_url(value: str, label: str, *, expected: str) -> str:
    if value != expected:
        raise ResolverError(f"{label} must be exactly {expected!r}, got {value!r}")
    return value


def _admitted_argv(argv: list[str]) -> list[str]:
    """The last gate: nothing dash-leading that this module did not choose."""
    for element in argv:
        if not isinstance(element, str) or not element:
            raise ResolverError(f"argv holds an empty element: {argv!r}")
        if chr(0) in element:
            raise ResolverError("argv holds a NUL byte")
        if element.startswith("-") and element not in FIXED_FLAGS:
            raise ResolverError(
                f"argv element {element!r} begins with a dash and is not one of the "
                "flags these builders emit"
            )
    return argv


def pip_download_argv(
    staged: StagedManifest,
    cache_dir: Path | str,
    *,
    python: Path | str,
    index_url: str = DEFAULT_INDEX_URL,
) -> list[str]:
    """Fetch every wheel the plan pins, into *cache_dir*, and nothing else.

    ``--only-binary=:all:`` means no sdist is downloaded and therefore no build
    backend ever executes. ``--require-hashes`` is emitted **without**
    ``--no-deps``: pip requires a hash for every file it downloads, so the plan
    must pin the whole closure. That is pip's rule, not ours, and it is the
    reason a plan naming only its direct dependencies fails here rather than
    quietly fetching an unpinned transitive one.
    """
    return _admitted_argv(
        [
            _check_program(python, "python"),
            "-I",
            "-m",
            "pip",
            "download",
            "--isolated",
            "--disable-pip-version-check",
            "--no-input",
            "--only-binary=:all:",
            "--require-hashes",
            "--proxy",
            REGISTRY_PROXY_URL,
            "--index-url",
            _check_url(index_url, "index url", expected=DEFAULT_INDEX_URL),
            "--dest",
            _check_path(cache_dir, "cache directory"),
            "-r",
            _check_path(staged.path, "staged requirements"),
        ]
    )


def pip_offline_install_argv(
    staged: StagedManifest,
    cache_dir: Path | str,
    venv_python: Path | str,
) -> list[str]:
    """Install from the cache with no index at all: the network is not reachable."""
    return _admitted_argv(
        [
            _check_program(venv_python, "venv python"),
            "-I",
            "-m",
            "pip",
            "install",
            "--isolated",
            "--disable-pip-version-check",
            "--no-input",
            "--no-index",
            "--find-links",
            _check_path(cache_dir, "cache directory"),
            "--require-hashes",
            "--only-binary=:all:",
            "-r",
            _check_path(staged.path, "staged requirements"),
        ]
    )


def _npm_argv(
    staged: StagedNodeManifests,
    cache_dir: Path | str,
    *,
    npm: Path | str,
    offline: bool,
) -> list[str]:
    argv = [
        _check_program(npm, "npm"),
        "ci",
        *([] if offline else ["--ignore-scripts"]),
        "--no-audit",
        "--no-fund",
        "--cache",
        _check_path(cache_dir, "cache directory"),
        "--userconfig",
        NULL_DEVICE,
        "--registry",
        _check_url(NPM_REGISTRY_URL, "registry url", expected=NPM_REGISTRY_URL),
        "--prefix",
        _check_path(staged.directory, "staged directory"),
    ]
    if offline:
        argv.append("--offline")
    else:
        argv.extend((
            "--proxy", REGISTRY_PROXY_URL,
            "--https-proxy", REGISTRY_PROXY_URL,
            "--noproxy=",
        ))
    return _admitted_argv(argv)


def npm_fetch_argv(
    staged: StagedNodeManifests,
    cache_dir: Path | str,
    *,
    npm: Path | str = "npm",
) -> list[str]:
    """Populate the cache from the registry, running no lifecycle script."""
    return _npm_argv(staged, cache_dir, npm=npm, offline=False)


def npm_offline_install_argv(
    staged: StagedNodeManifests,
    cache_dir: Path | str,
    *,
    npm: Path | str = "npm",
) -> list[str]:
    """Install offline with dependency scripts allowed, after broker revocation.

    The caller must stage the canonical, root-script-free manifests at the
    installation prefix. The network-less jail, not ignore-scripts, isolates
    dependency code; no proxy is available during this phase.
    """
    return _npm_argv(staged, cache_dir, npm=npm, offline=True)


def resolver_environment(home: Path | str, path: str) -> dict[str, str]:
    """The resolver jail's environment, built from empty.

    Nothing is inherited and nothing is read from this process: the returned
    mapping is the whole of what the child sees. Pip's isolated mode still reads
    its config-file selector; the null device suppresses global and site files
    too. Its version-check/no-input environment settings are ignored in isolated
    mode, so both pip builders enforce those settings with explicit flags.
    The npm setting disables its update notice.
    """
    return {
        "HOME": _check_path(home, "home"),
        "PATH": _check_search_path(path),
        "LANG": "C.UTF-8",
        # pip compares this exact string to os.devnull (lowercase on Windows).
        # Do not substitute npm's differently spelled NULL_DEVICE constant.
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
    }


def _check_search_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ResolverError("PATH must be a non-empty string")
    if chr(0) in path:
        raise ResolverError("PATH contains a NUL byte")
    for entry in path.split(os.pathsep):
        if not entry:
            raise ResolverError(f"PATH holds an empty entry: {path!r}")
        if not Path(entry).is_absolute():
            raise ResolverError(f"PATH entry must be absolute: {entry!r}")
    return path

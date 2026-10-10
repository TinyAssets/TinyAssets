"""One workspace git operation, performed INSIDE the owner's cell.

The cell runs as the owner, in the owner's command center, with an empty
network namespace and one unix socket to the command center's checking egress
proxy. It never holds a credential: the daemon opened an ephemeral route on
that proxy (``git_egress``) and handed the cell the URL REWRITES as git
options, so the only process that ever sees the token is the broker.

Everything a workspace operation does to a repository happens here -- the
clone, the bundle, its verification, the population of the lease, the push --
because every one of those writes into the owner's tree or parses
attacker-shaped pack data, and neither belongs to the daemon.

What crosses back is a sha, a byte count, a ref name and a fixed error class.
The request that comes in carries no credential, no credential reference and
no host path: the lease is named RELATIVE to the mounted command center, and
the cell joins it through its own descriptors.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from tinyassets.workspace_git import (
    GitResult,
    WorkspaceGitError,
    create_bundle,
    populate_workspace_from_bundle,
    run_git_in_cell,
    scrub_text,
    unbundle_into_fresh_repo,
    verify_bundle,
)

__all__ = [
    "MAX_BUNDLE_BYTES",
    "MAX_REQUEST_BYTES",
    "REMOTE_OPS",
    "CONTENT_DIR",
    "EXPORT_DIR",
    "EXPORT_REF",
    "perform",
    "validate",
]

#: The operations the cell answers. Anything else is refused without git.
REMOTE_OPS = frozenset({"checkout", "push", "ls_remote", "create"})
#: Ops that need the egress route: everything that names a remote.
ROUTED_OPS = frozenset({"checkout", "push", "ls_remote"})

#: The request bound. It is a fixed set of short fields plus the rewrites.
MAX_REQUEST_BYTES = 16384
#: The bundle bound (the per-lease disk bound is the real limit; this is the
#: parser bound on a file that is attacker-influenced input).
MAX_BUNDLE_BYTES = 512 * 1024 * 1024

#: The one directory inside a lease that becomes ``/workspace`` for a node.
CONTENT_DIR = "repo"
#: Where the jail writes an export bundle, relative to the content directory.
EXPORT_DIR = ".tiny-export"
EXPORT_REF = "refs/tiny/export"
#: The cell's working directory for one operation, inside the lease it is
#: operating on and NOT inside the lease's content directory.
#:
#: Why not the cell's own ``/tmp``: that is a tmpfs, and a clone plus its
#: bundle is twice the repository -- up to the lease bound -- so a big checkout
#: would be paid for in the host's RAM. Here the bytes land on disk inside the
#: lease whose reservation already covers them, under the name a wipe removes.
#: Only the lease's ``repo`` is ever mounted into a jail or a node, so nothing
#: user code can reach sees this, and a killed cell's leftovers are removed by
#: the next operation rather than blocking it.
CELL_DIR = ".tiny-cell"

_DEFAULT_TIMEOUT_S = 900.0
_LS_REMOTE_TIMEOUT_S = 120.0
_MAX_TIMEOUT_S = 1500.0

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Names inside the cell's private scratch. No caller ever names one.
_SRC_DIR = "src.git"
_OUT_BUNDLE = "out.bundle"
_IN_BUNDLE = "in.bundle"
_HOME_DIR = "home"
_VERIFY_DIR = "verify"
_IMPORT_DIR = "import"
_IMPORT_VERIFY_DIR = "import-verify"


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #


def _bad(message: str) -> WorkspaceGitError:
    return WorkspaceGitError("bad_argument", message)


def _text(request: dict[str, Any], field: str, pattern: re.Pattern[str]) -> str:
    value = request.get(field)
    if type(value) is not str or not pattern.match(value) or ".." in value:
        raise _bad(f"request.{field} is missing or not a permitted value")
    return value


def _parts(request: dict[str, Any], field: str) -> tuple[str, ...]:
    value = request.get(field)
    if (type(value) is not list or not value or len(value) > 8
            or any(type(part) is not str or not _NAME_RE.match(part) for part in value)):
        raise _bad(f"request.{field} must be 1-8 plain directory names")
    return tuple(value)


def _fields(op: str) -> set[str]:
    """The EXACT field set each op carries. An extra field is a refusal."""
    common = {"op", "timeout_s", "options"}
    if op == "create":
        return common | {"storage", "lease_parent", "lease_name"}
    if op == "checkout":
        return common | {"storage", "lease_parent", "lease_name", "host", "repo", "ref",
                         "checkout_ref"}
    if op == "push":
        return common | {"lease_parent", "lease_name", "host", "repo", "remote_ref",
                         "commit_sha", "reconcile_only", "max_bundle_bytes"}
    return common | {"host", "repo", "remote_ref"}


def validate(request: Any) -> dict[str, Any]:
    """Return the request iff every field is one this cell will act on."""
    if not isinstance(request, dict):
        raise _bad("the cell request must be a mapping")
    op = request.get("op")
    if op not in REMOTE_OPS:
        raise _bad("the cell request names no known workspace operation")
    if set(request) != _fields(op):
        raise _bad(f"the {op} request does not carry exactly its own fields")
    options = request.get("options")
    if (type(options) is not list or len(options) > 64
            or any(type(item) is not str or "\0" in item or "\n" in item
                   for item in options)):
        raise _bad("request.options must be bounded git option strings")
    timeout = request.get("timeout_s")
    if type(timeout) not in (int, float) or not 0 < timeout <= _MAX_TIMEOUT_S:
        raise _bad("request.timeout_s is outside the cell's bound")
    if op in ROUTED_OPS:
        _text(request, "host", _HOST_RE)
        _text(request, "repo", _REPO_RE)
        if not options:
            raise _bad("a remote operation needs its egress route options")
    if op in ("checkout", "create"):
        if request["storage"] not in ("scratch", "universe"):
            raise _bad("request.storage must be 'scratch' or 'universe'")
        _parts(request, "lease_parent")
        _text(request, "lease_name", _NAME_RE)
    if op == "checkout":
        _text(request, "ref", _REF_RE)
        _text(request, "checkout_ref", _REF_RE)
    if op == "push":
        _parts(request, "lease_parent")
        _text(request, "lease_name", _NAME_RE)
        _text(request, "remote_ref", _REF_RE)
        if not _SHA_RE.match(str(request.get("commit_sha") or "")):
            raise _bad("request.commit_sha must be 40 lowercase hex characters")
        if type(request["reconcile_only"]) is not bool:
            raise _bad("request.reconcile_only must be a boolean")
        bound = request["max_bundle_bytes"]
        if type(bound) is not int or not 0 < bound <= MAX_BUNDLE_BYTES:
            raise _bad("request.max_bundle_bytes is outside the cell's bound")
    if op == "ls_remote":
        _text(request, "remote_ref", _REF_RE)
    return request


# --------------------------------------------------------------------------- #
# The lease, through descriptors only
# --------------------------------------------------------------------------- #


def _fs():
    from tinyassets import workspace_fs

    return workspace_fs


def _walk(root_fd: int, parts: tuple[str, ...]) -> int:
    """Open ``parts`` beneath ``root_fd``, following no link and creating nothing.

    The daemon prepared these directories and labelled them for this owner
    (``workspace_owner_pool``); a missing one is a refusal, never a mkdir, so
    the cell can never create a pool directory the daemon has not labelled.
    """
    fs = _fs()
    current = os.dup(root_fd)
    try:
        for part in parts:
            child = fs.open_subdir_nofollow(current, part)
            os.close(current)
            current = child
    except BaseException:
        os.close(current)
        raise
    return current


def _make_lease(root_fd: int, request: dict[str, Any]) -> tuple[int, int, str]:
    """``(lease_fd, content_fd, lease relative path)`` -- created by the OWNER.

    A scratch lease name is unguessable because its parent has two writers (the
    daemon and this owner). A permanent generation is a small integer under a
    repository directory, where the rule would protect nothing, so it takes the
    fixed-name helper instead -- the same split the daemon side used to make.
    """
    fs = _fs()
    parent_fd = _walk(root_fd, request["lease_parent"])
    lease_fd = content_fd = None
    try:
        name = request["lease_name"]
        if request["storage"] == "scratch":
            lease_fd = fs.create_cell_lease_dir(parent_fd, name)
        else:
            lease_fd = fs.create_cell_subdir(parent_fd, name)
        content_fd = fs.create_cell_subdir(lease_fd, CONTENT_DIR)
    except BaseException:
        for fd in (content_fd, lease_fd):
            if fd is not None:
                os.close(fd)
        raise
    finally:
        os.close(parent_fd)
    relative = "/".join((*request["lease_parent"], request["lease_name"]))
    return lease_fd, content_fd, relative


def _lease_scratch(lease_fd: int, lease_path: Path) -> Path:
    """A fresh, empty, owner-owned working directory inside the lease.

    A previous cell that the mapper killed may have left one behind; it is
    removed through the lease's own descriptor (no path is re-resolved, no link
    is followed) rather than refused, so one lost operation cannot wedge the
    workspace for good.
    """
    fs = _fs()
    fs._remove_beneath(lease_fd, CELL_DIR)
    os.close(fs.create_cell_subdir(lease_fd, CELL_DIR))
    return Path(lease_path, CELL_DIR)


# --------------------------------------------------------------------------- #
# Git, always spawned by this cell
# --------------------------------------------------------------------------- #


class _Git:
    """Every git this cell runs: its own homes, its own scratch, its options."""

    def __init__(self, scratch: Path, *, options: list[str], git_binary: str, path: str,
                 timeout_s: float, runner: Callable[..., GitResult] | None = None) -> None:
        #: The cell's own private scratch. Big operations move off it (see
        #: :data:`CELL_DIR`); a route-only probe needs nothing but a git HOME.
        self.fallback = scratch
        self.scratch = scratch
        self.options = list(options)
        self.git_binary = git_binary
        self.path = path
        self.timeout_s = timeout_s
        self.runner = run_git_in_cell if runner is None else runner

    def working_in(self, scratch: Path) -> None:
        """Move to the lease's own on-disk working directory (see CELL_DIR)."""
        self.scratch = scratch

    def home(self, suffix: str) -> Path:
        home = self.scratch / f"{_HOME_DIR}-{suffix}"
        home.mkdir(parents=True, exist_ok=True)
        return home

    def directory(self, name: str) -> Path:
        target = self.scratch / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    def routed(self, argv: list[str], *, cwd: Path, timeout_s: float | None = None) -> GitResult:
        """A git that reaches the remote: through the route options, nothing else."""
        return self.runner(
            argv, cwd=cwd, home_dir=self.home("routed"), path=self.path,
            options=self.options, git_binary=self.git_binary,
            timeout_s=self.timeout_s if timeout_s is None else timeout_s,
        )

    def local(self, argv: list[str], *, cwd: Path) -> GitResult:
        """A git with no route at all: no option of this request reaches it."""
        return self.runner(
            argv, cwd=cwd, home_dir=self.home("local"), path=self.path,
            git_binary=self.git_binary, timeout_s=self.timeout_s,
        )


def _url(request: dict[str, Any]) -> str:
    """The canonical https URL. Built, never a stored remote or user text.

    The route rewrites this exact spelling (``git_egress`` yields
    ``url.<route>.insteadOf=https://<host>/<repo>.git``), so a URL this
    function did not build reaches nothing: the cell has no network.
    """
    return f"https://{request['host']}/{request['repo']}.git"


def _refused(result: GitResult, what: str, fallback: str = "transport") -> WorkspaceGitError:
    code = result.stderr_class if result.stderr_class != "other" else fallback
    return WorkspaceGitError(code, f"{what}: {result.stderr_scrubbed}")


def _symref_head(git: _Git, request: dict[str, Any]) -> str:
    """The ref the remote reports as HEAD, e.g. ``refs/heads/main``."""
    listed = git.routed(
        ["ls-remote", "--symref", _url(request), "HEAD"],
        cwd=git.home("routed"), timeout_s=_LS_REMOTE_TIMEOUT_S,
    )
    if not listed.ok:
        raise _refused(listed, "could not read the remote HEAD")
    for line in listed.stdout_tail.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "ref:" and parts[2] == "HEAD":
            return parts[1]
    raise WorkspaceGitError("verification", "the remote reported no HEAD")


def _observed(git: _Git, request: dict[str, Any], remote_ref: str) -> str:
    """What sha the remote holds at ``remote_ref`` now ("" when absent)."""
    listed = git.routed(
        ["ls-remote", _url(request), remote_ref],
        cwd=git.home("routed"), timeout_s=_LS_REMOTE_TIMEOUT_S,
    )
    if not listed.ok:
        return ""
    for line in listed.stdout_tail.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == remote_ref:
            return parts[0]
    return ""


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


def _create(request: dict[str, Any], *, root_fd: int, **_unused) -> dict[str, Any]:
    """An empty owner workspace: the lease and its content directory, nothing else."""
    lease_fd, content_fd, relative = _make_lease(root_fd, request)
    os.close(content_fd)
    os.close(lease_fd)
    return {"ok": True, "lease": relative, "bytes": 0, "content": CONTENT_DIR}


def _checkout(
    request: dict[str, Any], *, root_fd: int, root_path: Path, git: _Git,
) -> dict[str, Any]:
    """Clone through the route, bundle it, and populate the owner's lease."""
    lease_fd, content_fd, relative = _make_lease(root_fd, request)
    lease_path = Path(root_path, *request["lease_parent"], request["lease_name"])
    try:
        git.working_in(_lease_scratch(lease_fd, lease_path))
        clone = git.routed(
            ["clone", "--bare", "--single-branch", "--no-recurse-submodules",
             "--branch", request["ref"], _url(request), _SRC_DIR],
            cwd=git.scratch,
        )
        if not clone.ok:
            raise _refused(clone, "clone failed")
        source = git.scratch / _SRC_DIR
        resolved = git.routed(["rev-parse", "HEAD"], cwd=source,
                              timeout_s=_LS_REMOTE_TIMEOUT_S)
        if not resolved.ok:
            raise WorkspaceGitError("verification", "could not resolve the cloned head")
        sha = resolved.stdout_tail.strip()

        # Bundling and population are credential-free AND route-free: the
        # options that reach the remote are not passed to either.
        bundle = git.scratch / _OUT_BUNDLE
        create_bundle(
            source, sha, bundle, home_dir=git.home("bundle"), path=git.path,
            scratch_dir=git.directory(_VERIFY_DIR), timeout_s=git.timeout_s,
            git_binary=git.git_binary, runner=git.runner,
        )
        # The clone holds the remote and is gone before anything else runs.
        shutil.rmtree(source, ignore_errors=True)
        measured = bundle.stat().st_size
        populate_workspace_from_bundle(
            bundle, lease_path / CONTENT_DIR, EXPORT_REF, request["checkout_ref"],
            home_dir=git.home("populate"), path=git.path, timeout_s=git.timeout_s,
            git_binary=git.git_binary, dest_fd=content_fd, runner=git.runner,
        )
        return {"ok": True, "resolved_sha": sha, "bytes": measured,
                "ref_name": EXPORT_REF, "lease": relative, "content": CONTENT_DIR}
    finally:
        # The bundle and every git home held this operation's material. They
        # go before the lease is published, success or not.
        _fs()._remove_beneath(lease_fd, CELL_DIR)
        os.close(content_fd)
        os.close(lease_fd)


def _push(
    request: dict[str, Any], *, root_fd: int, root_path: Path, git: _Git,
) -> dict[str, Any]:
    """Verify the jail-made bundle credential-free, then push exactly one sha."""
    commit_sha = request["commit_sha"]
    remote_ref = request["remote_ref"]
    parts = (*request["lease_parent"], request["lease_name"])
    lease_fd = _walk(root_fd, parts)
    try:
        git.working_in(_lease_scratch(lease_fd, Path(root_path, *parts)))
        destination = git.scratch / _IN_BUNDLE
        # The name is DERIVED from the sha the packet named, so no request
        # field can point this read at another file in the lease.
        relative = f"{CONTENT_DIR}/{EXPORT_DIR}/{commit_sha}.bundle"
        try:
            _fs().copy_regular_file_beneath(
                lease_fd, relative, destination,
                max_bytes=int(request["max_bundle_bytes"]),
            )
        except (OSError, ValueError) as exc:
            raise WorkspaceGitError(
                "verification", f"the export bundle could not be read: {type(exc).__name__}",
            ) from None

        # 1. Credential-free, route-free verification FIRST: a crafted pack is
        #    parser input, so nothing that could reach a remote is in scope.
        refs = verify_bundle(
            destination, max_bytes=int(request["max_bundle_bytes"]),
            scratch_dir=git.directory(_IMPORT_VERIFY_DIR), home_dir=git.home("verify"),
            path=git.path, timeout_s=git.timeout_s, git_binary=git.git_binary,
            runner=git.runner,
        )
        if EXPORT_REF not in refs:
            raise WorkspaceGitError(
                "verification", "the bundle does not carry the expected export ref")
        import_dir = git.scratch / _IMPORT_DIR
        imported = unbundle_into_fresh_repo(
            destination, import_dir, ref_name=EXPORT_REF, home_dir=git.home("import"),
            path=git.path, timeout_s=git.timeout_s, git_binary=git.git_binary,
            runner=git.runner,
        )
        if imported != commit_sha:
            raise WorkspaceGitError(
                "verification", "the bundle's commit is not the one the packet named")

        # 2. Only now is a remote reachable at all.
        head_ref = _symref_head(git, request)
        if remote_ref == head_ref:
            raise WorkspaceGitError(
                "protected", "the remote's default branch is never a push target")
        # Exact sha to an exact ref, fast-forward only. No refspec leading '+',
        # no --force, no --delete: the branch policy is in the argv itself.
        pushed = git.routed(["push", _url(request), f"{commit_sha}:{remote_ref}"], cwd=import_dir)
        if not pushed.ok:
            raise WorkspaceGitError("transport" if pushed.stderr_class == "other"
                                    else pushed.stderr_class,
                                    f"push refused: {pushed.stderr_scrubbed}")
        return {"ok": True, "resolved_sha": commit_sha, "remote_ref": remote_ref,
                "bytes": destination.stat().st_size, "head_ref": head_ref}
    finally:
        _fs()._remove_beneath(lease_fd, CELL_DIR)
        os.close(lease_fd)


def _reconcile(request: dict[str, Any], *, git: _Git, **_unused) -> dict[str, Any]:
    """Resolve an ambiguous push: what does the remote actually hold now?

    Crash safety. The same sha already at the ref is success (a repeated
    non-force push of the same sha is not a failure); anything else is a
    refusal that names what was observed.
    """
    commit_sha = request["commit_sha"]
    remote_ref = request["remote_ref"]
    observed = _observed(git, request, remote_ref)
    if observed == commit_sha:
        return {"ok": True, "resolved_sha": commit_sha, "remote_ref": remote_ref,
                "reconciled": True, "bytes": 0}
    return {"ok": False,
            "error": "the push outcome was lost and the remote does not hold this commit",
            "stderr_class": "non_fast_forward", "observed_sha": observed,
            "remote_ref": remote_ref, "reconciled": True}


def _ls_remote(request: dict[str, Any], *, git: _Git, **_unused) -> dict[str, Any]:
    head_ref = _symref_head(git, request)
    observed = _observed(git, request, request["remote_ref"]) if request["remote_ref"] else ""
    return {"ok": True, "head_ref": head_ref, "observed_sha": observed, "bytes": 0}


def _safe_error(exc: BaseException) -> str:
    """A message that cannot carry a secret, whatever raised it."""
    if isinstance(exc, WorkspaceGitError):
        return scrub_text(str(exc))
    return scrub_text(f"{type(exc).__name__}: {exc}")


def perform(
    request: Any,
    *,
    root_fd: int,
    root_path: str | os.PathLike[str],
    scratch: str | os.PathLike[str],
    git_binary: str = "git",
    path: str = "/usr/bin:/bin",
    runner: Callable[..., GitResult] | None = None,
) -> dict[str, Any]:
    """Run one validated operation and return a secret-free answer.

    ``root_fd``/``root_path`` are the mounted command center -- the descriptor
    for every directory step, the path for the two readbacks git can only do by
    name. ``scratch`` is the cell's private tmpfs. Never raises: a refusal is
    an answer with a fixed class, which is what the daemon's contract carries.
    """
    try:
        request = validate(request)
    except WorkspaceGitError as exc:
        return {"ok": False, "error": _safe_error(exc), "stderr_class": exc.code}
    git = _Git(Path(scratch), options=request["options"], git_binary=git_binary,
               path=path, timeout_s=float(request["timeout_s"]), runner=runner)
    common = {"root_fd": root_fd, "root_path": Path(root_path), "git": git}
    try:
        if request["op"] == "push" and request["reconcile_only"]:
            return _reconcile(request, **common)
        return {"create": _create, "checkout": _checkout, "push": _push,
                "ls_remote": _ls_remote}[request["op"]](request, **common)
    except WorkspaceGitError as exc:
        if request["op"] == "push" and exc.code == "timeout":
            # Crash safety: the send may have landed. Ask the remote rather
            # than reporting a failure that already succeeded. The push's
            # working directory in the lease is already gone, so the probe
            # runs from the cell's own scratch.
            try:
                git.working_in(git.fallback)
                return _reconcile(request, **common)
            except Exception as reconcile_exc:  # noqa: BLE001 - report the original
                return {"ok": False, "error": _safe_error(exc), "stderr_class": exc.code,
                        "reconcile_error": _safe_error(reconcile_exc)}
        return {"ok": False, "error": _safe_error(exc), "stderr_class": exc.code}
    except Exception as exc:  # noqa: BLE001 - loud, but never leaky
        return {"ok": False, "error": _safe_error(exc), "stderr_class": "other"}

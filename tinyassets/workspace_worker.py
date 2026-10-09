"""The credential-blind git worker's operations; its daemon-child spawn is retired.

``execute_workspace_operation`` now refuses (see its docstring). What follows
describes the operations it used to run.

The token exists only inside this child. The parent passes a credential
REFERENCE (``vault://http/<key>``), never a secret; the child resolves it with
the vault resolver, hands it to git one request at a time through an in-memory
broker, and answers with a sha, a byte count and a fixed error class. Nothing
that crosses back carries the token or a host path: paths in the response are
names relative to the staging directory the parent created, so the parent joins
them itself and the child never tells it where anything is.

Design D1 of the ``workspace-node`` change. The staging directory is
worker-private and is never a workspace: no credentialed git here ever opens a
directory user code can write.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from tinyassets.workspace_git import (
    CredentialBroker,
    GitResult,
    GitTransport,
    WorkspaceGitError,
    create_bundle,
    run_git,
    scrub_text,
    unbundle_into_fresh_repo,
    verify_bundle,
)

__all__ = [
    "MAX_BUNDLE_BYTES",
    "reconcile_push_intents",
    "WORKSPACE_OPS",
    "execute_workspace_operation",
]

#: Operations the child answers. Anything else is refused without spawning git.
WORKSPACE_OPS = frozenset({"checkout", "push", "ls_remote"})

#: The bundle bound (D4's per-lease disk bound is the real limit; this is the
#: parser bound on a file that is attacker-influenced input).
MAX_BUNDLE_BYTES = 512 * 1024 * 1024

#: Names inside the worker-private staging dir. The parent knows these names,
#: never absolute paths from the child.
_SRC_DIR = "src.git"
_OUT_BUNDLE = "out.bundle"
_HOME_DIR = "home"
_VERIFY_DIR = "verify"
_IMPORT_DIR = "import"
_IMPORT_VERIFY_DIR = "import-verify"

_DEFAULT_TIMEOUT_S = 900.0
_LS_REMOTE_TIMEOUT_S = 120.0
_EXPORT_REF = "refs/tiny/export"


# --------------------------------------------------------------------------- #
# Child helpers
# --------------------------------------------------------------------------- #


def _safe_error(exc: BaseException) -> str:
    """A message that cannot carry the token, whatever raised.

    ``WorkspaceGitError`` is already scrubbed at construction; anything else is
    an exception this module did not shape, so only its class name and a
    scrubbed message cross back.
    """
    if isinstance(exc, WorkspaceGitError):
        return scrub_text(str(exc))
    return scrub_text(f"{type(exc).__name__}: {exc}")


def _error_code(exc: BaseException) -> str:
    return exc.code if isinstance(exc, WorkspaceGitError) else "other"


def _canonical_url(host: str, owner_repo: str) -> str:
    """The canonical https URL. Never a stored remote, never user text."""
    return f"https://{host}/{owner_repo}.git"


def _minimal_path(git_binary: str) -> str:
    """The PATH the git child searches: the directory holding git, only."""
    resolved = shutil.which(git_binary) if not Path(git_binary).is_absolute() else git_binary
    if not resolved:
        raise WorkspaceGitError("transport", "git is not on PATH in the worker")
    return str(Path(resolved).parent)


def _curl_version_text(request: dict[str, Any], home: Path, path: str) -> str:
    """Text to ask :func:`libcurl_supports_multi_resolve` about.

    An injected ``resolver_text`` wins (the parent may know better). Otherwise
    ask ``curl -V``, whose output carries ``libcurl/X.Y.Z``. NOT
    ``git version --build-options``: on git 2.43 that carries no libcurl token
    at all, so reading it would silently answer "no multi-resolve" forever.

    This is not a git invocation, so it does not go through ``run_git``; it
    still runs from an environment built from empty and never inherits.
    """
    injected = request.get("resolver_text")
    if isinstance(injected, str) and injected.strip():
        return injected
    # The library first (the production container has no curl binary), then
    # `curl -V` from an environment built from empty; fail loud with neither.
    from tinyassets.workspace_git import libcurl_version_text

    def _run_curl(argv: list[str], **kwargs: Any) -> Any:
        return subprocess.run(
            argv,
            cwd=str(home),
            env={"PATH": path, "HOME": str(home), "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )

    return libcurl_version_text(
        which=lambda name: shutil.which(name, path=path), run=_run_curl,
    )


class _GitSession:
    """One credentialed git session: broker, pinned address, forced options.

    Everything that needs the token lives here and dies with ``close()``.
    """

    def __init__(
        self,
        *,
        staging: Path,
        host: str,
        owner_repo: str,
        secret: str,
        username: str,
        git_binary: str,
        request: dict[str, Any],
        resolver=None,
        classifier=None,
        launcher=None,
        broker_factory=None,
    ) -> None:
        self.git_binary = git_binary
        # None means run_git's own launcher, which kills the process GROUP
        # on a timeout. A test injects its own.
        self.launcher = launcher
        home = staging / _HOME_DIR
        home.mkdir(parents=True, exist_ok=True)
        self.home = home
        self.path = _minimal_path(git_binary)

        # ONE validated transport. The URL git is handed, the broker's binding
        # and the resolve rule all come from it, so there is no second place
        # the host or the path can be spelled differently.
        self.transport = GitTransport.build(
            owner_repo,
            host=host,
            curl_version_text=_curl_version_text(request, self.home, self.path),
            resolver=resolver,
            classifier=classifier,
        )
        self.host = self.transport.host
        self.url = self.transport.url
        self.addresses = self.transport.addresses

        protocol, broker_host, broker_path = self.transport.broker_binding()
        make_broker = broker_factory if broker_factory is not None else CredentialBroker
        self.broker = make_broker(protocol, broker_host, broker_path, username, secret)
        # The broker picks its own SHORT directory. It used to go under the
        # staging path -- `<data>/.workspace-staging/<run>/<node>/broker` --
        # and a unix socket address is 108 bytes, so in production that
        # refused the checkout outright (144 bytes, measured in the deployed
        # container 2026-08-31). Where the socket lives is the broker's
        # business, not the caller's depth.
        helper = self.broker.serve()
        self.options = self.transport.forced_options(helper)

    def run(self, argv: list[str], *, cwd: Path, timeout_s: float) -> GitResult:
        return run_git(
            argv,
            cwd=cwd,
            home_dir=self.home,
            path=self.path,
            options=self.options,
            timeout_s=timeout_s,
            launcher=self.launcher,
            git_binary=self.git_binary,
        )

    def close(self) -> None:
        self.broker.close()

    def __enter__(self) -> _GitSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _credential_free_home(staging: Path, git_binary: str, suffix: str) -> tuple[Path, str]:
    """An environment for the git operations that must NOT have a credential."""
    home = staging / f"{_HOME_DIR}-{suffix}"
    home.mkdir(parents=True, exist_ok=True)
    return home, _minimal_path(git_binary)


def _require(request: dict[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceGitError("bad_argument", f"request.{field} is required")
    return value.strip()


def _resolve_secret(request: dict[str, Any]) -> str:
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver

    universe_dir = _require(request, "universe_dir")
    credential_ref = _require(request, "credential_ref")
    resolver = _GeneralVaultCredentialResolver(universe_dir=universe_dir)
    try:
        secret = resolver(credential_ref)
    except Exception as exc:  # the vault's own messages never carry the secret
        raise WorkspaceGitError(
            "auth", f"credential could not be resolved: {type(exc).__name__}"
        ) from None
    if not isinstance(secret, str) or not secret.strip():
        raise WorkspaceGitError("auth", "credential reference resolved to nothing")
    return secret


def _staging_dir(request: dict[str, Any]) -> Path:
    staging = Path(_require(request, "staging_dir"))
    if not staging.is_dir():
        raise WorkspaceGitError("bad_argument", "staging_dir must be an existing directory")
    return staging


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


def _handle_checkout(
    request: dict[str, Any],
    *,
    git_binary: str,
    launcher=None,
    broker_factory=None,
    **injected,
) -> dict[str, Any]:
    """Clone into private staging, bundle it, delete the clone.

    The bundle is the ONLY thing that leaves staging, and
    :func:`create_bundle` has already proved it prerequisite-free.
    """
    staging = _staging_dir(request)
    host = _require(request, "host")
    owner_repo = _require(request, "owner_repo")
    ref = _require(request, "ref")
    timeout_s = float(request.get("timeout_s") or _DEFAULT_TIMEOUT_S)
    secret = _resolve_secret(request)

    with _GitSession(
        staging=staging,
        host=host,
        owner_repo=owner_repo,
        secret=secret,
        username=str(request.get("username") or "x-access-token"),
        git_binary=git_binary,
        request=request,
        launcher=launcher,
        broker_factory=broker_factory,
        **injected,
    ) as session:
        clone = session.run(
            [
                "clone",
                "--bare",
                "--single-branch",
                "--no-recurse-submodules",
                "--branch",
                ref,
                session.url,
                _SRC_DIR,
            ],
            cwd=staging,
            timeout_s=timeout_s,
        )
        if not clone.ok:
            raise WorkspaceGitError(
                clone.stderr_class if clone.stderr_class != "other" else "transport",
                f"clone failed: {clone.stderr_scrubbed}",
            )
        src = staging / _SRC_DIR
        resolved = session.run(["rev-parse", "HEAD"], cwd=src, timeout_s=_LS_REMOTE_TIMEOUT_S)
        if not resolved.ok:
            raise WorkspaceGitError("verification", "could not resolve the cloned head")
        sha = resolved.stdout_tail.strip()

    # Bundling is credential-free: the session is closed above, so the broker
    # is torn down before any further git runs.
    home, git_path = _credential_free_home(staging, git_binary, "bundle")
    verify_dir = staging / _VERIFY_DIR
    verify_dir.mkdir(parents=True, exist_ok=True)
    bundle = staging / _OUT_BUNDLE
    create_bundle(
        src,
        sha,
        bundle,
        home_dir=home,
        path=git_path,
        scratch_dir=verify_dir,
        timeout_s=timeout_s,
        launcher=launcher,
        git_binary=git_binary,
    )
    # Staging's clone holds the remote and is deleted before anything else runs.
    shutil.rmtree(src, ignore_errors=True)
    return {
        "ok": True,
        "resolved_sha": sha,
        "bytes": bundle.stat().st_size,
        "bundle_name": _OUT_BUNDLE,
        "ref_name": _EXPORT_REF,
    }


def _read_symref_head(session: _GitSession) -> str:
    """The ref the remote reports as HEAD, e.g. ``refs/heads/main``."""
    listed = session.run(
        ["ls-remote", "--symref", session.url, "HEAD"],
        cwd=session.home,
        timeout_s=_LS_REMOTE_TIMEOUT_S,
    )
    if not listed.ok:
        raise WorkspaceGitError(
            listed.stderr_class if listed.stderr_class != "other" else "transport",
            f"could not read the remote HEAD: {listed.stderr_scrubbed}",
        )
    for line in listed.stdout_tail.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "ref:" and parts[2] == "HEAD":
            return parts[1]
    raise WorkspaceGitError("verification", "the remote reported no HEAD")


def _observed_ref(session: _GitSession, remote_ref: str) -> str:
    """What sha the remote currently has at ``remote_ref`` ("" when absent)."""
    listed = session.run(
        ["ls-remote", session.url, remote_ref],
        cwd=session.home,
        timeout_s=_LS_REMOTE_TIMEOUT_S,
    )
    if not listed.ok:
        return ""
    for line in listed.stdout_tail.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == remote_ref:
            return parts[0]
    return ""


def _handle_push(
    request: dict[str, Any],
    *,
    git_binary: str,
    launcher=None,
    broker_factory=None,
    **injected,
) -> dict[str, Any]:
    """Verify a jail-made bundle credential-free, then push from fresh staging."""
    staging = _staging_dir(request)
    host = _require(request, "host")
    owner_repo = _require(request, "owner_repo")
    remote_ref = _require(request, "remote_ref")
    commit_sha = _require(request, "commit_sha")
    bundle_path = Path(_require(request, "bundle_path"))
    timeout_s = float(request.get("timeout_s") or _DEFAULT_TIMEOUT_S)

    # 1. Credential-free verification FIRST. A crafted pack is parser input, so
    #    it is parsed with no token in the process and no network reachable.
    home, git_path = _credential_free_home(staging, git_binary, "verify")
    verify_dir = staging / _IMPORT_VERIFY_DIR
    verify_dir.mkdir(parents=True, exist_ok=True)
    refs = verify_bundle(
        bundle_path,
        max_bytes=int(request.get("max_bundle_bytes") or MAX_BUNDLE_BYTES),
        scratch_dir=verify_dir,
        home_dir=home,
        path=git_path,
        timeout_s=timeout_s,
        launcher=launcher,
        git_binary=git_binary,
    )
    ref_name = str(request.get("ref_name") or _EXPORT_REF)
    if ref_name not in refs:
        raise WorkspaceGitError(
            "verification", "the bundle does not carry the expected export ref"
        )
    import_dir = staging / _IMPORT_DIR
    imported_sha = unbundle_into_fresh_repo(
        bundle_path,
        import_dir,
        ref_name=ref_name,
        home_dir=home,
        path=git_path,
        timeout_s=timeout_s,
        launcher=launcher,
        git_binary=git_binary,
    )
    if imported_sha != commit_sha:
        raise WorkspaceGitError(
            "verification", "the bundle's commit is not the one the packet named"
        )

    # 2. Only now does a credential exist in this process.
    secret = _resolve_secret(request)
    with _GitSession(
        staging=staging,
        host=host,
        owner_repo=owner_repo,
        secret=secret,
        username=str(request.get("username") or "x-access-token"),
        git_binary=git_binary,
        request=request,
        launcher=launcher,
        broker_factory=broker_factory,
        **injected,
    ) as session:
        head_ref = _read_symref_head(session)
        if remote_ref == head_ref:
            raise WorkspaceGitError(
                "protected", "the remote's default branch is never a push target"
            )
        # Exact sha to an exact ref, fast-forward only. No refspec leading '+',
        # no --force, no --delete: the branch policy is in the argv itself.
        pushed = session.run(
            ["push", session.url, f"{commit_sha}:{remote_ref}"],
            cwd=import_dir,
            timeout_s=timeout_s,
        )
        if pushed.ok:
            return {
                "ok": True,
                "resolved_sha": commit_sha,
                "remote_ref": remote_ref,
                "bytes": bundle_path.stat().st_size,
                "head_ref": head_ref,
            }
        raise WorkspaceGitError(pushed.stderr_class, f"push refused: {pushed.stderr_scrubbed}")


def _reconcile_push(
    request: dict[str, Any],
    *,
    git_binary: str,
    launcher=None,
    broker_factory=None,
    **injected,
) -> dict[str, Any]:
    """Resolve an ambiguous push: what does the remote actually hold now?

    D1's crash-safety rule. The same sha already at the ref is success (a
    repeated non-force push of the same sha is not a failure); anything else is
    a refusal that names what was observed.
    """
    staging = _staging_dir(request)
    host = _require(request, "host")
    owner_repo = _require(request, "owner_repo")
    remote_ref = _require(request, "remote_ref")
    commit_sha = _require(request, "commit_sha")
    secret = _resolve_secret(request)
    with _GitSession(
        staging=staging,
        host=host,
        owner_repo=owner_repo,
        secret=secret,
        username=str(request.get("username") or "x-access-token"),
        git_binary=git_binary,
        request=request,
        launcher=launcher,
        broker_factory=broker_factory,
        **injected,
    ) as session:
        observed = _observed_ref(session, remote_ref)
    if observed == commit_sha:
        return {
            "ok": True,
            "resolved_sha": commit_sha,
            "remote_ref": remote_ref,
            "reconciled": True,
            "bytes": 0,
        }
    return {
        "ok": False,
        "error": "the push outcome was lost and the remote does not hold this commit",
        "stderr_class": "non_fast_forward",
        "observed_sha": observed,
        "remote_ref": remote_ref,
        "reconciled": True,
    }


def _handle_ls_remote(
    request: dict[str, Any],
    *,
    git_binary: str,
    launcher=None,
    broker_factory=None,
    **injected,
) -> dict[str, Any]:
    staging = _staging_dir(request)
    host = _require(request, "host")
    owner_repo = _require(request, "owner_repo")
    remote_ref = str(request.get("remote_ref") or "").strip()
    secret = _resolve_secret(request)
    with _GitSession(
        staging=staging,
        host=host,
        owner_repo=owner_repo,
        secret=secret,
        username=str(request.get("username") or "x-access-token"),
        git_binary=git_binary,
        request=request,
        launcher=launcher,
        broker_factory=broker_factory,
        **injected,
    ) as session:
        head_ref = _read_symref_head(session)
        observed = _observed_ref(session, remote_ref) if remote_ref else ""
    return {"ok": True, "head_ref": head_ref, "observed_sha": observed, "bytes": 0}


_HANDLERS = {
    "checkout": _handle_checkout,
    "push": _handle_push,
    "ls_remote": _handle_ls_remote,
}


def handle_request(request: dict[str, Any], **injected) -> dict[str, Any]:
    """Execute one request in-process and return a secret-free response.

    Separated from the transport so the whole worker is testable without
    spawning: the child is this function plus a pipe.
    """
    if not isinstance(request, dict):
        return {"ok": False, "error": "request must be a mapping", "stderr_class": "bad_argument"}
    op = str(request.get("op") or "").strip()
    if op not in WORKSPACE_OPS:
        return {
            "ok": False,
            "error": f"unknown workspace worker op: {op or '(none)'}",
            "stderr_class": "bad_argument",
        }
    git_binary = str(request.get("git_binary") or "git")
    try:
        if op == "push" and bool(request.get("reconcile_only")):
            return _reconcile_push(request, git_binary=git_binary, **injected)
        return _HANDLERS[op](request, git_binary=git_binary, **injected)
    except WorkspaceGitError as exc:
        if op == "push" and exc.code == "timeout":
            # D1 crash safety: the send may have landed. Ask the remote rather
            # than reporting a failure that already succeeded.
            try:
                return _reconcile_push(request, git_binary=git_binary, **injected)
            except Exception as reconcile_exc:  # noqa: BLE001 - report the original
                return {
                    "ok": False,
                    "error": _safe_error(exc),
                    "stderr_class": exc.code,
                    "reconcile_error": _safe_error(reconcile_exc),
                }
        return {"ok": False, "error": _safe_error(exc), "stderr_class": exc.code}
    except Exception as exc:  # noqa: BLE001 - Hard Rule 8: loud, but never leaky
        return {"ok": False, "error": _safe_error(exc), "stderr_class": _error_code(exc)}


# --------------------------------------------------------------------------- #
# Transport: the spawned child and the parent side
# --------------------------------------------------------------------------- #


def reconcile_push_intents(base_path: Any, **kwargs: Any) -> list[tuple[str, str]]:
    """Settle every push whose outcome was lost. Exposed HERE because this is
    where the caller already is: ``runs.py``'s startup reconciliation calls
    ``workspace_worker.reconcile_push_intents(base_path)`` and the worker is
    what asks the remote."""
    from tinyassets.workspace_intents import reconcile_push_intents as _reconcile

    kwargs.setdefault("execute", execute_workspace_operation)
    return _reconcile(base_path, **kwargs)


#: The refusal every credentialed workspace git operation now receives.
RETIRED_ANSWER = {
    "ok": False,
    "error": ("credentialed workspace git is retired: its worker held the token as a "
              "daemon child; use authenticated git from bash in the agent's own cell"),
    "stderr_class": "auth",
}


def execute_workspace_operation(
    request: dict[str, Any],
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    startup_timeout_s: float = 30.0,
    spawn: Any = None,
) -> dict[str, Any]:
    """Refuse: no daemon-uid child resolves a token or runs git any more.

    The worker was a ``multiprocessing`` child of the daemon that read the vault
    and ran git with the token and the container network. No owner cell holds a
    credential, so this class is retired rather than left unconfined
    (per-role-uid-split design, section 1). ``spawn`` stays injectable for the
    callers' own tests.
    """
    del timeout_s, startup_timeout_s
    if spawn is not None:
        return spawn(request)
    return dict(RETIRED_ANSWER)

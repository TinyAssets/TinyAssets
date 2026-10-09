"""Credential-blind git layer for the workspace effect sink.

No credentialed git ever opens a workspace. No host-side git opens a
workspace's ``.git`` after the workspace is published to user code. A
host-side, credential-free initializer may populate a fresh, unpublished
directory from a verified bundle.

That boundary is what the pieces here exist to hold. The token never reaches
argv, an environment or a file: it lives in this process and is handed to git
one request at a time by an in-memory broker bound to exactly one
``(protocol, host, path)``. Every child runs from an environment built from
empty with system and global git configuration disabled, hooks and fsmonitor
off, redirects refused, every protocol but HTTPS refused, and the remote
address pinned in the transport to addresses the outbound driver's classifier
validated as public unicast. Bundles are the only way objects cross between a
workspace and staging, and they must be prerequisite-free so that importing
one into an empty repository is a complete transfer. Nothing returned from
here carries the secret: output is bounded, scrubbed of every registered
secret plus generic credential patterns, reduced to one fixed error class, and
the same scrubbing runs over every exception message.

This module reads no environment variables. Callers pass the environment, the
minimal ``PATH``, the resolver, the classifier and the process launcher in;
tests substitute them by parameter, never by an environment switch.
"""

from __future__ import annotations

import contextlib
import contextvars
import ipaddress
import logging
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "CredentialBroker",
    "GitResult",
    "GitTransport",
    "WorkspaceGitError",
    "canonical_credential_path",
    "classify_stderr",
    "create_bundle",
    "forced_git_options",
    "git_environment",
    "kill_git",
    "libcurl_supports_multi_resolve",
    "libcurl_version_text",
    "pin_address",
    "populate_workspace_from_bundle",
    "run_git",
    "run_git_in_cell",
    "scrub_text",
    "unbundle_into_fresh_repo",
    "verify_bundle",
]

# ---------------------------------------------------------------------------
# Platform constants. The production host is Linux; the suite also runs on
# Windows, where the null device is spelled differently and there is no
# ``/bin/false``. ``NUL`` is not an executable, so an askpass pointing at it
# fails closed exactly like ``/bin/false`` -- and terminal prompts are off.
# ---------------------------------------------------------------------------

_IS_WINDOWS = os.name == "nt"
NULL_DEVICE = "NUL" if _IS_WINDOWS else "/dev/null"
FALSE_BINARY = "NUL" if _IS_WINDOWS else "/bin/false"
# SIGKILL does not exist on Windows, where the process-group path is unreachable
# anyway. Resolved once here so referencing it cannot raise at kill time.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)

#: Every code ``WorkspaceGitError.code`` may carry.
ERROR_CODES: frozenset[str] = frozenset(
    {
        "auth",
        "not_found",
        "non_fast_forward",
        "protected",
        "transport",
        "verification",
        "other",
        "address_refused",
        "bad_argument",
        "timeout",
    }
)

#: The classes :func:`classify_stderr` may return.
STDERR_CLASSES: tuple[str, ...] = (
    "auth",
    "not_found",
    "non_fast_forward",
    "protected",
    "transport",
    "verification",
    "other",
)

_MAX_CAPTURED_BYTES = 64 * 1024
_REDACTED = "[redacted]"
_MIN_SECRET_LENGTH = 8
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_REF_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


# ---------------------------------------------------------------------------
# Secret registry and scrubbing
# ---------------------------------------------------------------------------

_SECRET_LOCK = threading.Lock()
_SECRETS: Counter[str] = Counter()


def _register_secret(secret: str) -> None:
    with _SECRET_LOCK:
        _SECRETS[secret] += 1


def _unregister_secret(secret: str) -> None:
    with _SECRET_LOCK:
        if _SECRETS[secret] <= 1:
            del _SECRETS[secret]
        else:
            _SECRETS[secret] -= 1


def _registered_secrets() -> list[str]:
    with _SECRET_LOCK:
        return list(_SECRETS)


_SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # userinfo in an https URL: https://user:token@host/...
    (re.compile(r"https://[^/@\s]+@"), f"https://{_REDACTED}@"),
    # an Authorization header value, however it is spelled
    (re.compile(r"(?i)(authorization\s*:\s*)([^\r\n]+)"), r"\1" + _REDACTED),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), _REDACTED),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), _REDACTED),
)


def scrub_text(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Remove every registered secret and every generic credential pattern.

    Exact-secret removal runs first (longest first, so a secret that contains
    another is not left half-redacted), then the generic patterns catch tokens
    this process never held.
    """
    if not text:
        return text
    secrets = {s for s in (*_registered_secrets(), *extra_secrets) if s}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, _REDACTED)
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class WorkspaceGitError(Exception):
    """A refusal or failure in the credential-blind git layer.

    ``code`` is one of :data:`ERROR_CODES`. The message is scrubbed at
    construction, so neither ``str(exc)`` nor ``exc.args`` can carry a secret.
    """

    def __init__(self, code: str, message: str = "") -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown workspace git error code: {code!r}")
        scrubbed = scrub_text(message)
        self.code = code
        self.message = scrubbed
        super().__init__(f"{code}: {scrubbed}" if scrubbed else code)


# ---------------------------------------------------------------------------
# Credential broker
# ---------------------------------------------------------------------------

# The transport half: a helper git runs as ``credential.helper=!<python>
# <script> <socket>``. It holds no secret of its own -- it forwards the request
# git wrote on stdin to the broker's unix socket and copies the answer back.
_HELPER_SCRIPT = '''\
"""git credential helper: forward the request to the broker on a unix socket.

Invoked by git as ``<python> <this script> <socket path> <operation>``. It
holds no credential; the broker decides whether to answer at all.
"""

import socket
import sys


def main() -> int:
    if len(sys.argv) < 3:
        return 1
    sock_path = sys.argv[1]
    operation = sys.argv[2]
    payload = sys.stdin.read()
    request = "operation=" + operation + "\\n" + payload
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(15.0)
        client.connect(sock_path)
        client.sendall(request.encode("utf-8"))
        client.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            block = client.recv(65536)
            if not block:
                break
            chunks.append(block)
    sys.stdout.write(b"".join(chunks).decode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

# ``sun_path`` is 108 bytes on Linux and 104 on macOS; refuse loudly rather
# than bind a silently truncated path.
_MAX_SOCKET_PATH_BYTES = 100

#: Where the broker puts its socket when the caller does not choose one.
#: SHORT by design and deliberately not ``tempfile.gettempdir()``: ``sun_path``
#: is 108 bytes total, and honouring ``TMPDIR`` would put the limit back in the
#: hands of whatever the environment happens to say. The directory is created
#: 0700 and removed on ``close``.
_BROKER_SOCKET_ROOT = "/tmp"
_BROKER_DIR_PREFIX = "tabk-"


def broker_socket_dir(root: str | os.PathLike[str] = _BROKER_SOCKET_ROOT) -> Path:
    """A fresh, private, SHORT directory for one broker's socket.

    ``/tmp/tabk-<8 hex>``: about 34 bytes with the socket name on the end,
    against a 108-byte limit. The name is unguessable because the directory is
    0700 in a world-writable root -- the mode is the guard, the entropy stops
    another process from having created it first.
    """
    parent = Path(root)
    if not parent.is_dir():
        raise WorkspaceGitError(
            "transport", f"no directory at {parent} to put the broker socket in"
        )
    for _attempt in range(8):
        candidate = parent / f"{_BROKER_DIR_PREFIX}{secrets.token_hex(4)}"
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError as exc:
            raise WorkspaceGitError(
                "transport", f"could not create the broker socket directory: {exc}"
            ) from None
        return candidate
    raise WorkspaceGitError(
        "transport", "could not create a private directory for the broker socket"
    )


def _remove_broker_dir(directory: Path) -> None:
    """Remove a directory :func:`broker_socket_dir` made. Never raises.

    Only the two files the broker writes, then the directory: a recursive
    delete of a path in a world-writable root is not a thing to do on a
    teardown path, and anything else in there means something is wrong that a
    silent rmtree would erase.
    """
    for name in ("credential.sock", "credential_helper.py"):
        try:
            (directory / name).unlink()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        logging.getLogger(__name__).warning(
            "broker socket directory %s could not be removed", directory
        )


def canonical_credential_path(repo: str) -> str:
    """The wire path git asks for, derived ONCE from the repository identity.

    Measured against git 2.43/2.53: for ``https://github.com/owner/name.git``
    git's credential request carries ``path=owner/name.git``, and for a URL
    without the suffix it carries ``path=owner/name`` -- two DIFFERENT strings.
    Since this layer always builds the ``.git`` URL, that is the one spelling
    the broker binds to.
    """
    value = str(repo or "").strip().strip("/")
    if not value:
        raise WorkspaceGitError("bad_argument", "a credential path needs a repository")
    if not value.endswith(".git"):
        value = f"{value}.git"
    return value


def _wire_path(raw: str) -> str:
    """A request path as git spells it: no leading slash, nothing else touched.

    Deliberately NOT symmetric normalisation. Stripping ``.git`` on both sides
    made a grant for ``owner/name.git`` answer a request for ``owner/name``,
    which is a different resource on some hosts and a different grant on all of
    them.
    """
    return str(raw or "").strip().lstrip("/")


def _split_host_port(raw: str) -> tuple[str, str | None]:
    value = raw.strip()
    if value.count(":") == 1:
        name, _, port = value.partition(":")
        return name, port
    return value, None


class CredentialBroker:
    """An in-memory git credential helper bound to one repository.

    :meth:`answer` is the whole protocol and is pure -- it takes the framed
    request text and returns the response text, so the decision logic is
    testable in-process on any platform. :meth:`serve` is the transport and is
    POSIX-only by design: the token must never cross a loopback TCP socket any
    local process could connect to.
    """

    def __init__(
        self,
        protocol: str,
        host: str,
        path: str,
        username: str,
        secret: str,
    ) -> None:
        for label, value in (
            ("protocol", protocol),
            ("host", host),
            ("path", path),
            ("username", username),
            ("secret", secret),
        ):
            if not isinstance(value, str) or not value:
                raise WorkspaceGitError(
                    "bad_argument", f"credential {label} must be a non-empty str"
                )
            if "\n" in value or "\r" in value or "\x00" in value:
                raise WorkspaceGitError(
                    "bad_argument", f"credential {label} has a forbidden character"
                )
        if protocol.lower() != "https":
            raise WorkspaceGitError("bad_argument", "only https credentials are brokered")
        if len(secret) < _MIN_SECRET_LENGTH:
            raise WorkspaceGitError("bad_argument", "credential secret is implausibly short")

        self._protocol = protocol.lower()
        self._host = host.lower()
        # Bound to the canonical wire path, derived once from the identity.
        self._path = canonical_credential_path(path)
        if not self._path:
            raise WorkspaceGitError("bad_argument", "credential path must name a repository")
        self._username = username
        self._secret = secret
        self._closed = False
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._socket_path: Path | None = None
        self._script_path: Path | None = None
        #: Set only when serve() made the directory itself; close() removes it.
        self._owned_directory: Path | None = None
        self._helper_command: str | None = None
        _register_secret(secret)

    # -- protocol ---------------------------------------------------------

    def answer(self, request_text: str) -> str | None:
        """Answer one framed credential request.

        The first line is ``operation=<get|store|erase>``; the rest are git's
        ``key=value`` request lines. ``get`` for this exact repository returns
        the credential; ``store`` and ``erase`` are ignored (empty response);
        anything else -- another host, another path, another username, an
        unknown operation, or any request after :meth:`close` -- returns
        ``None``, which git reads as "this helper has nothing", and the
        operation then fails to authenticate. That is the desired outcome.
        """
        if self._closed:
            return None
        fields: dict[str, str] = {}
        for line in request_text.splitlines():
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in fields:
                fields[key] = value.strip()
        operation = fields.get("operation", "")
        if operation in ("store", "erase"):
            return ""
        if operation != "get":
            return None
        if fields.get("protocol", "").lower() != self._protocol:
            return None
        host, port = _split_host_port(fields.get("host", ""))
        if host.lower() != self._host:
            return None
        if port is not None and port != "443":
            return None
        if "path" not in fields:
            # useHttpPath is forced on; a request without a path cannot be
            # matched to one repository, so it is refused.
            return None
        if _wire_path(fields["path"]) != self._path:
            return None
        requested_user = fields.get("username")
        if requested_user is not None and requested_user and requested_user != self._username:
            return None
        return f"username={self._username}\npassword={self._secret}\n"

    # -- transport --------------------------------------------------------

    def serve(
        self,
        *,
        socket_dir: str | os.PathLike[str] | None = None,
        python_executable: str | None = None,
    ) -> str:
        """Start the unix-socket transport and return the ``credential.helper``.

        Returns the value to pass to :func:`forced_git_options`, of the form
        ``!<python> <helper script> <socket path>``.

        The broker chooses its own short directory (:func:`broker_socket_dir`)
        and removes it on ``close``. ``socket_dir`` is an OVERRIDE for tests,
        not the normal path: a unix socket's address is 108 bytes, and putting
        it inside the caller's directory made the limit depend on how deeply
        that caller happened to be nested. In production the staging path is
        ``<data>/.workspace-staging/<run>/<node>/``, which is close enough that
        a longer run id refused every checkout with a message about
        ``sun_path`` -- found by the droplet oracle, 2026-08-31.
        """
        if self._closed:
            raise WorkspaceGitError("bad_argument", "broker is closed")
        if _IS_WINDOWS:
            raise WorkspaceGitError(
                "transport",
                "the credential broker transport requires a POSIX unix domain socket",
            )
        if self._thread is not None:
            raise WorkspaceGitError("bad_argument", "broker is already serving")
        owned_directory = socket_dir is None
        directory = Path(socket_dir) if socket_dir is not None else broker_socket_dir()
        if not directory.is_dir():
            raise WorkspaceGitError(
                "bad_argument", "broker socket_dir must be an existing directory"
            )
        os.chmod(directory, 0o700)
        socket_path = directory / "credential.sock"
        script_path = directory / "credential_helper.py"
        if socket_path.exists() or script_path.exists():
            raise WorkspaceGitError("bad_argument", "broker socket_dir is not empty")
        if len(str(socket_path).encode("utf-8")) > _MAX_SOCKET_PATH_BYTES:
            # Only reachable through an override now; the chosen root is short
            # by construction. Still checked: bind would fail with a truncated
            # address rather than an error naming the cause.
            if owned_directory:
                _remove_broker_dir(directory)
            raise WorkspaceGitError(
                "bad_argument",
                f"broker socket path is {len(str(socket_path).encode())} bytes, over the "
                f"{_MAX_SOCKET_PATH_BYTES} a unix socket address allows; pass a shorter "
                "socket_dir or let the broker choose its own",
            )

        script_path.write_text(_HELPER_SCRIPT, encoding="utf-8")
        os.chmod(script_path, 0o500)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(socket_path))
            os.chmod(socket_path, 0o600)
            server.listen(8)
            server.settimeout(0.2)
        except OSError as exc:
            server.close()
            raise WorkspaceGitError(
                "transport", f"broker could not bind its socket: {exc}"
            ) from None

        self._server = server
        self._socket_path = socket_path
        self._script_path = script_path
        self._owned_directory = directory if owned_directory else None
        interpreter = python_executable or sys.executable
        self._helper_command = "!" + " ".join(
            shlex.quote(part) for part in (interpreter, str(script_path), str(socket_path))
        )
        self._thread = threading.Thread(
            target=self._accept_loop,
            name="workspace-git-credential-broker",
            daemon=True,
        )
        self._thread.start()
        return self._helper_command

    @property
    def helper_command(self) -> str:
        """The ``credential.helper`` value; only available while serving."""
        if self._closed or self._helper_command is None:
            raise WorkspaceGitError("bad_argument", "broker is not serving")
        return self._helper_command

    def _accept_loop(self) -> None:
        server = self._server
        if server is None:
            return
        while not self._closed:
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                try:
                    self._handle(connection)
                except OSError:
                    continue

    def _handle(self, connection: socket.socket) -> None:
        connection.settimeout(15.0)
        chunks: list[bytes] = []
        total = 0
        while True:
            block = connection.recv(4096)
            if not block:
                break
            total += len(block)
            if total > _MAX_CAPTURED_BYTES:
                return
            chunks.append(block)
        request = b"".join(chunks).decode("utf-8", "replace")
        response = self.answer(request)
        if response is None:
            return
        connection.sendall(response.encode("utf-8"))

    # -- teardown ---------------------------------------------------------

    def close(self) -> None:
        """Tear down the transport and drop the secret. Idempotent."""
        if self._closed:
            return
        self._closed = True
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        for path in (self._socket_path, self._script_path):
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass
        # A directory the broker made is the broker's to remove; one the caller
        # passed is the caller's, and deleting it would be a surprise.
        owned, self._owned_directory = getattr(self, "_owned_directory", None), None
        if owned is not None:
            _remove_broker_dir(owned)
        self._socket_path = None
        self._script_path = None
        self._helper_command = None
        _unregister_secret(self._secret)
        self._secret = ""
        self._username = ""

    def __enter__(self) -> CredentialBroker:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Environment and forced options
# ---------------------------------------------------------------------------

#: Exactly the keys :func:`git_environment` returns.
GIT_ENVIRONMENT_KEYS: frozenset[str] = frozenset(
    {
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GIT_ASKPASS",
        "HOME",
        "PATH",
        "LANG",
    }
)


def git_environment(home_dir: str | os.PathLike[str], *, path: str) -> dict[str, str]:
    """Build the git child's environment from empty.

    ``home_dir`` must be an existing empty directory (an inherited ``HOME``
    would hand the child a user's git config), and ``path`` is the minimal
    ``PATH`` the caller wants the child to search. No ``GIT_TRACE*`` is ever
    set: trace output prints the request headers.
    """
    if not isinstance(path, str) or not path:
        raise WorkspaceGitError("bad_argument", "git_environment needs a non-empty PATH")
    if "\n" in path or "\x00" in path:
        raise WorkspaceGitError("bad_argument", "PATH has a forbidden character")
    home = Path(home_dir)
    if not home.is_dir():
        raise WorkspaceGitError(
            "bad_argument", "git_environment HOME must be an existing directory"
        )
    if any(home.iterdir()):
        raise WorkspaceGitError("bad_argument", "git_environment HOME must be empty")
    return {
        "GIT_CONFIG_SYSTEM": NULL_DEVICE,
        "GIT_CONFIG_GLOBAL": NULL_DEVICE,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": FALSE_BINARY,
        "HOME": str(home),
        "PATH": path,
        "LANG": "C.UTF-8",
    }


#: libcurl gained comma-separated addresses in one ``CURLOPT_RESOLVE`` entry
#: in 7.59.0. Below that, one entry holds one address.
_MULTI_RESOLVE_MINIMUM = (7, 59, 0)
_LIBCURL_VERSION_RE = re.compile(r"libcurl/(\d+)\.(\d+)(?:\.(\d+))?")


def libcurl_version_text(
    *,
    find_library: Callable[[str], str | None] | None = None,
    load_library: Callable[[str], Any] | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Callable[..., Any] | None = None,
    candidates: Sequence[str] = ("libcurl-gnutls.so.4", "libcurl.so.4", "libcurl.so"),
) -> str:
    """The text :func:`libcurl_supports_multi_resolve` decides on.

    Read from the LIBRARY first: ``curl_version()`` of the libcurl this host
    links (the same one git's ``git-remote-https`` uses) - the production
    container ships no ``curl`` binary at all, so a binary-only probe would
    refuse every checkout (found on the droplet, 2026-08-31). ``curl -V`` is
    the fallback; with neither, fail loud - a silent default would be a
    permanent, invisible downgrade to one address per operation.
    """
    import ctypes
    import ctypes.util

    finder = find_library or ctypes.util.find_library
    loader = load_library or ctypes.CDLL
    # git-remote-https links libcurl-GNUTLS on Debian-family images while
    # find_library('curl') answers the OpenSSL build - two different libcurls.
    # The one git uses decides, so the gnutls name is tried first and the
    # finder's answer after the fixed candidates.
    names: list[str] = list(candidates)
    try:
        found = finder("curl")
    except Exception:  # noqa: BLE001 - a finder that crashes is "not found"
        found = None
    if found and found not in names:
        names.append(found)
    for name in names:
        try:
            lib = loader(name)
            fn = getattr(lib, "curl_version")
            fn.restype = ctypes.c_char_p
            raw = fn()
        except (OSError, AttributeError):
            continue
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
        if text.strip():
            return text
    whicher = which or shutil.which
    binary = whicher("curl")
    if binary:
        runner = run or subprocess.run
        try:
            probe = runner(
                [binary, "-V"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkspaceGitError(
                "bad_argument", f"could not read the libcurl version: {type(exc).__name__}"
            ) from None
        out = getattr(probe, "stdout", b"") or b""
        text = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
        if text.strip():
            return text
    raise WorkspaceGitError(
        "bad_argument",
        "no libcurl to read a version from: neither the shared library nor a curl binary",
    )


def libcurl_supports_multi_resolve(curl_version_text: str) -> bool:
    """Whether this libcurl accepts several addresses in one resolve entry.

    Takes the version TEXT (``curl -V`` carries a ``libcurl/X.Y.Z`` token)
    rather than running anything: the caller knows which binary it is about to
    run. NOT ``git version --build-options`` -- measured on git 2.43, that
    carries no libcurl token at all.

    Unparseable text RAISES rather than answering False. A silent False is a
    permanent, invisible downgrade to one address per operation: everything
    keeps working, a little worse, forever, and nothing ever says so. Asked
    once per worker, a loud failure is cheap and a silent one is not.
    """
    if not isinstance(curl_version_text, str) or not curl_version_text.strip():
        raise WorkspaceGitError(
            "bad_argument", "no libcurl version text to read a multi-resolve capability from"
        )
    found = _LIBCURL_VERSION_RE.search(curl_version_text)
    if found is None:
        raise WorkspaceGitError(
            "bad_argument", "the version text carries no libcurl/X.Y.Z token"
        )
    major, minor, patch = found.group(1), found.group(2), found.group(3)
    return (int(major), int(minor), int(patch or 0)) >= _MULTI_RESOLVE_MINIMUM


@dataclass(frozen=True)
class GitTransport:
    """One validated transport: the URL, the binding and the pinned addresses.

    Built ONCE from the repository identity, and everything downstream reads it
    rather than re-deriving: the URL handed to git, the broker's
    ``(protocol, host, path)`` binding, and the ``http.curloptResolve`` rule
    all come from the same object. That is what stops a caller pinning
    ``github.com`` and then cloning somewhere else -- there is no second place
    the host or the path can be spelled.
    """

    repo: str
    host: str
    port: int
    url: str
    credential_path: str
    addresses: tuple[str, ...]
    multi_resolve: bool

    @classmethod
    def build(
        cls,
        repo: str,
        *,
        curl_version_text: str,
        host: str,
        resolver: Callable[[str, int], list[str]] | None = None,
        classifier: Callable[[str], str] | None = None,
        port: int = 443,
    ) -> GitTransport:
        """Resolve, validate and canonicalise everything one operation needs."""
        if not _HOSTNAME_RE.match(host or ""):
            raise WorkspaceGitError("bad_argument", "transport host is not a hostname")
        if int(port) != 443:
            raise WorkspaceGitError("bad_argument", "only HTTPS on 443 is transported")
        canonical_host = host.lower()
        credential_path = canonical_credential_path(repo)
        owner_repo = credential_path[: -len(".git")]
        if owner_repo.count("/") != 1:
            raise WorkspaceGitError("bad_argument", "repo must be exactly 'owner/name'")
        multi = libcurl_supports_multi_resolve(curl_version_text)
        validated = pin_address(canonical_host, resolver, classifier, port=port)
        # Below 7.59.0 one entry holds one address, so the whole operation runs
        # against the first validated address rather than emitting a comma list
        # that libcurl would silently ignore.
        addresses = tuple(validated) if multi else (validated[0],)
        return cls(
            repo=owner_repo,
            host=canonical_host,
            port=int(port),
            url=f"https://{canonical_host}/{credential_path}",
            credential_path=credential_path,
            addresses=addresses,
            multi_resolve=multi,
        )

    def broker_binding(self) -> tuple[str, str, str]:
        """``(protocol, host, path)`` the broker binds to. One derivation."""
        return ("https", self.host, self.credential_path)

    def forced_options(self, broker_helper_cmd: str) -> list[str]:
        return forced_git_options(
            self.host, self.addresses, broker_helper_cmd, multi_resolve=self.multi_resolve
        )


def _resolve_rule_addresses(validated_ips: str | Sequence[str]) -> list[str]:
    """Normalise addresses for a curl resolve entry; IPv6 is bracketed."""
    if isinstance(validated_ips, str):
        candidates: list[str] = [validated_ips]
    else:
        candidates = list(validated_ips)
    if not candidates:
        raise WorkspaceGitError("bad_argument", "no pinned address to resolve to")
    formatted: list[str] = []
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            raise WorkspaceGitError(
                "bad_argument", "pinned address is not an ip address"
            ) from None
        # curl's resolve syntax brackets a literal IPv6 address.
        formatted.append(f"[{address.compressed}]" if address.version == 6 else address.compressed)
    return formatted


def forced_git_options(
    host: str,
    validated_ips: str | Sequence[str],
    broker_helper_cmd: str,
    *,
    multi_resolve: bool,
) -> list[str]:
    """The ``-c key=value`` arguments every credentialed git child carries.

    The two ``credential.helper`` entries are ordered: the empty one RESETS
    the inherited helper list, and only then is the broker appended. Reversing
    them would leave a system helper in front of the broker.

    ``validated_ips`` may be one address or several (what :func:`pin_address`
    returns). ``multi_resolve`` is REQUIRED and says whether the libcurl that
    will run accepts several addresses in one resolve entry -- ask
    :func:`libcurl_supports_multi_resolve` about the version text of the curl
    that is actually going to run. With it True, several addresses become ONE
    comma-joined entry which libcurl tries in order, so a dead first address
    does not fail an operation whose host has healthy siblings. With it False,
    passing more than one address is REFUSED rather than quietly emitting an
    entry an old libcurl would mis-parse: the caller runs one address per
    whole operation, and that has to be its own decision.

    The host is lower-cased: curl matches the entry against the URL's host,
    and a non-canonical entry does not error -- it silently does nothing,
    which would drop the pin and let git resolve the host itself.
    """
    if not _HOSTNAME_RE.match(host or ""):
        raise WorkspaceGitError("bad_argument", "pinned host is not a hostname")
    canonical_host = host.lower()
    addresses = _resolve_rule_addresses(validated_ips)
    if not multi_resolve and len(addresses) > 1:
        raise WorkspaceGitError(
            "bad_argument",
            f"this libcurl takes one address per operation, got {len(addresses)}",
        )
    pinned = ",".join(addresses)
    if not isinstance(broker_helper_cmd, str) or not broker_helper_cmd:
        raise WorkspaceGitError("bad_argument", "broker helper command must be a non-empty str")
    if "\n" in broker_helper_cmd or "\r" in broker_helper_cmd or "\x00" in broker_helper_cmd:
        raise WorkspaceGitError("bad_argument", "broker helper command has a forbidden character")
    settings = (
        f"core.hooksPath={NULL_DEVICE}",
        "core.fsmonitor=false",
        "credential.helper=",
        f"credential.helper={broker_helper_cmd}",
        "credential.useHttpPath=true",
        "protocol.allow=never",
        "protocol.https.allow=always",
        "http.followRedirects=false",
        "submodule.recurse=false",
        "transfer.fsckObjects=true",
        "fetch.fsckObjects=true",
        "receive.fsckObjects=true",
        f"http.curloptResolve={canonical_host}:443:{pinned}",
    )
    options: list[str] = []
    for setting in settings:
        options.extend(("-c", setting))
    return options


# ---------------------------------------------------------------------------
# Address pinning
# ---------------------------------------------------------------------------


def _production_resolver() -> Callable[[str, int], list[str]]:
    from tinyassets.storage.outbound_connections import _default_dns_resolver

    return _default_dns_resolver


def _production_classifier() -> Callable[[str], str]:
    from tinyassets.storage.outbound_connections import _classify_global_address

    return _classify_global_address


def pin_address(
    hostname: str,
    resolver: Callable[[str, int], list[str]] | None = None,
    classifier: Callable[[str], str] | None = None,
    *,
    port: int = 443,
) -> tuple[str, ...]:
    """Resolve ``hostname`` and return every address git may connect to.

    EVERY resolved address must classify as public unicast. A host that
    answers with a mix of public and private addresses is an attack signal,
    not a host with one usable address: the whole resolution is refused rather
    than narrowed to the public subset.

    All of them come back, in the resolver's order, because pinning only the
    first would fail an operation whose host has other healthy addresses.
    The order is the resolver's -- getaddrinfo already applies the platform's
    address-selection rules, and reordering here would discard them.

    The classifier follows the outbound driver's contract: it RETURNS the
    normalised address when the address is globally routable and RAISES
    otherwise. Any exception, and any empty or non-string return, is a
    refusal.
    """
    if not isinstance(hostname, str) or not hostname:
        raise WorkspaceGitError("bad_argument", "pin_address needs a hostname")
    resolve = resolver if resolver is not None else _production_resolver()
    classify = classifier if classifier is not None else _production_classifier()
    try:
        addresses = list(resolve(hostname, port))
    except Exception as exc:  # a resolver failure is a refusal, never a pass
        raise WorkspaceGitError(
            "address_refused", f"could not resolve {hostname}: {type(exc).__name__}"
        ) from None
    if not addresses:
        raise WorkspaceGitError("address_refused", f"{hostname} resolved to no address")
    validated: list[str] = []
    for candidate in addresses:
        try:
            classified = classify(candidate)
        except Exception:
            classified = ""
        if not isinstance(classified, str) or not classified:
            # Never proceed with the public subset: report the split, not the
            # address, which the outbound driver treats as untrusted input.
            raise WorkspaceGitError(
                "address_refused",
                f"{hostname} resolved to {len(addresses)} addresses and at least one "
                "is not globally routable",
            )
        validated.append(classified)
    return tuple(validated)


# ---------------------------------------------------------------------------
# Running git
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitResult:
    """The bounded, scrubbed outcome of one git child."""

    returncode: int
    stdout_tail: str
    stderr_class: str
    stderr_scrubbed: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# Conservative, ordered: the first matching class wins. Verification and
# branch-policy refusals are checked before auth because a rejected push often
# also mentions permissions, while "Authentication failed" is unambiguous.
_CLASS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "verification",
        (
            r"\bfsck\b",
            r"\bindex-pack\b",
            r"did not send all necessary objects",
            r"object of unexpected type",
            r"\bbad object\b",
            r"\bcorrupt(ed)?\b",
            r"hash mismatch",
            r"\bbundle\b.*\b(invalid|corrupt|requires|not a bundle)",
            r"does not look like a v[0-9] bundle file",
        ),
    ),
    (
        "protected",
        (
            r"protected branch",
            r"\bGH006\b",
            r"pre-receive hook declined",
            r"refusing to allow",
            r"branch is read-only",
        ),
    ),
    (
        "non_fast_forward",
        (
            r"non-fast-forward",
            r"fetch first",
            r"updates were rejected because",
            r"cannot lock ref",
        ),
    ),
    (
        "auth",
        (
            r"authentication failed",
            r"could not read username",
            r"could not read password",
            r"terminal prompts disabled",
            r"invalid username or password",
            r"invalid username or token",
            r"permission denied",
            r"\b401\b",
            r"\b403 forbidden\b",
        ),
    ),
    (
        "not_found",
        (
            r"repository not found",
            r"\b404\b",
            r"couldn't find remote ref",
            r"does not appear to be a git repository",
            r"remote branch .* not found",
        ),
    ),
    (
        "transport",
        (
            r"could not resolve host",
            r"failed to connect",
            r"connection (timed out|refused|reset)",
            r"\bssl\b",
            r"\btls\b",
            r"rpc failed",
            r"early eof",
            r"unexpected disconnect",
            r"operation too slow",
            r"\bcurl\b",
            r"unable to access",
        ),
    ),
)

_COMPILED_CLASS_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (name, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
    for name, patterns in _CLASS_PATTERNS
)


def classify_stderr(stderr_text: str) -> str:
    """Map scrubbed git stderr to exactly one of :data:`STDERR_CLASSES`.

    Only meaningful for a non-zero exit; a clean run classifies as ``other``.
    """
    text = stderr_text or ""
    for name, patterns in _COMPILED_CLASS_PATTERNS:
        for pattern in patterns:
            if pattern.search(text):
                return name
    return "other"


def _tail_text(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        data = raw.encode("utf-8", "replace")
    elif isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    else:
        data = str(raw).encode("utf-8", "replace")
    if len(data) > _MAX_CAPTURED_BYTES:
        data = data[-_MAX_CAPTURED_BYTES:]
    return data.decode("utf-8", "replace")


def _reject_secrets_in(
    command: Sequence[str], env: Mapping[str, str], extra: Sequence[str]
) -> None:
    """Refuse to spawn if any known secret is in argv, options, env or binary.

    The invariant this module exists for is that the token reaches git ONLY
    through the broker. A URL with userinfo, a ``-c http.extraHeader=...``, or
    a secret that found its way into the environment would all defeat that
    silently -- and every one of them is visible right here, before the spawn.
    """
    secrets = [s for s in (*_registered_secrets(), *extra) if s]
    if not secrets:
        return
    haystacks = list(command) + list(env.values()) + list(env.keys())
    for secret in secrets:
        for value in haystacks:
            if secret in value:
                raise WorkspaceGitError(
                    "bad_argument",
                    "a credential appears in the git invocation; the broker is the "
                    "only path a secret may take",
                )


#: Descriptors every git this process runs inherits: the workspace worker's
#: staging in-use share (`workspace_worker._mark_staging_in_use`). Process-wide
#: only in the one-request worker child, which exits after its operation.
_INHERITED_FDS: tuple[int, ...] = ()
#: The same, scoped to a block (`inheriting`): a long-lived server process must
#: not hand one operation's share to every later git -- the number would be
#: stale once that operation closed it.
_SCOPED_FDS: contextvars.ContextVar[tuple[int, ...]] = contextvars.ContextVar(
    "workspace_git_scoped_fds", default=(),
)


def _require_descriptor(fd: object) -> int:
    if not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
        raise WorkspaceGitError("bad_argument", "an inherited descriptor must be an fd")
    return fd


def inherit_descriptor(fd: int) -> None:
    """Make every later git in this process inherit ``fd``."""
    global _INHERITED_FDS
    fd = _require_descriptor(fd)
    if fd not in _INHERITED_FDS:
        _INHERITED_FDS = (*_INHERITED_FDS, fd)


@contextlib.contextmanager
def inheriting(fd: int | None):
    """Every git run inside this block inherits ``fd`` (None: no-op).

    Used by the PARENT for git it runs itself against staging (populate), so a
    git that outlives a killed parent still holds the tree's in-use share
    (gpt-6-astra, PR #4143 round 3).
    """
    if fd is None:
        yield
        return
    token = _SCOPED_FDS.set((*_SCOPED_FDS.get(), _require_descriptor(fd)))
    try:
        yield
    finally:
        _SCOPED_FDS.reset(token)


def _checked_git_request(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    home_dir: str | os.PathLike[str],
    path: str,
    options: Sequence[str],
    timeout_s: float,
    git_binary: str,
    extra_secrets: Sequence[str],
    pass_fds: Sequence[int],
) -> dict[str, str]:
    """Validate one git request and return its canonical environment.

    Shared by :func:`run_git` (daemon side, hands the request to the owner's
    git cell) and :func:`run_git_in_cell` (the cell, which spawns it), so the
    two can never diverge on what a well-formed request is.
    """
    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)) or not argv:
        raise WorkspaceGitError("bad_argument", "run_git needs a non-empty argv sequence")
    for item in (*argv, *options):
        if not isinstance(item, str):
            raise WorkspaceGitError("bad_argument", "git arguments must all be str")
    if not isinstance(git_binary, str) or not git_binary:
        raise WorkspaceGitError("bad_argument", "git_binary must be a non-empty str")
    if not Path(cwd).is_dir():
        raise WorkspaceGitError("bad_argument", "run_git cwd must be an existing directory")
    for descriptor in pass_fds:
        if not isinstance(descriptor, int) or isinstance(descriptor, bool):
            raise WorkspaceGitError("bad_argument", "pass_fds must be descriptors")
    if timeout_s is None or float(timeout_s) <= 0:
        raise WorkspaceGitError("bad_argument", "run_git needs a positive timeout")

    env = git_environment(home_dir, path=path)
    # Defence in depth: git_environment is the only builder, so this can fail
    # only if IT changes. That is exactly when a silent widening would happen.
    if set(env) != set(GIT_ENVIRONMENT_KEYS):
        raise WorkspaceGitError("bad_argument", "the git environment key set is not canonical")
    for key in env:
        if key.startswith("GIT_") and key not in GIT_ENVIRONMENT_KEYS:
            raise WorkspaceGitError("bad_argument", f"{key} is not a permitted GIT_* variable")
    _reject_secrets_in([git_binary, *options, *argv], env, extra_secrets)
    return env


def run_git(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    home_dir: str | os.PathLike[str],
    path: str,
    options: Sequence[str] = (),
    timeout_s: float,
    launcher: Callable[..., object] | None = None,
    git_binary: str = "git",
    extra_secrets: Sequence[str] = (),
    preexec_fn: Callable[[], None] | None = None,
    pass_fds: Sequence[int] = (),
) -> GitResult:
    """Hand ``git <options...> <argv...>`` to the owner's git cell.

    Nothing spawns here. The request is validated and then executed by
    :mod:`tinyassets.role_git`, which admits the owner scope and runs
    :func:`run_git_in_cell` inside that owner's cell.

    The environment is still built and asserted HERE, from ``home_dir`` and
    ``path``, so a caller cannot hand in ``GIT_DIR``,
    ``GIT_ALTERNATE_OBJECT_DIRECTORIES``, ``GIT_CONFIG_COUNT`` or a
    ``GIT_TRACE*``, and every known secret is rejected out of argv, options,
    the environment and the binary path before the request leaves the daemon.
    The cell builds its own ``home_dir`` and asserts the same keys again.

    Output is truncated to the last 64 KiB of each stream and scrubbed before
    it is returned or classified.
    """
    _checked_git_request(
        argv, cwd=cwd, home_dir=home_dir, path=path, options=options,
        timeout_s=timeout_s, git_binary=git_binary, extra_secrets=extra_secrets,
        pass_fds=pass_fds,
    )
    from tinyassets.role_git import run as run_owner_git

    # Owner git runs in the owner's git cell and nowhere else. A caller that
    # needs a launcher, an inherited descriptor or its own child setup is
    # asking for a daemon-uid git; that mode does not exist.
    if (launcher is not None or git_binary not in ('git', '/usr/bin/git') or pass_fds
            or _INHERITED_FDS or _SCOPED_FDS.get() or preexec_fn is not None):
        raise WorkspaceGitError(
            'bad_argument', 'git cell descriptor/launcher mode not admitted')
    completed = run_owner_git(argv, cwd=cwd, options=options, timeout_s=float(timeout_s))
    stderr_scrubbed = scrub_text(_tail_text(completed.stderr), extra_secrets)
    return GitResult(returncode=completed.returncode,
                     stdout_tail=scrub_text(_tail_text(completed.stdout), extra_secrets),
                     stderr_class=classify_stderr(stderr_scrubbed),
                     stderr_scrubbed=stderr_scrubbed)


def run_git_in_cell(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    home_dir: str | os.PathLike[str],
    path: str,
    options: Sequence[str] = (),
    timeout_s: float,
    launcher: Callable[..., object] | None = None,
    git_binary: str = "git",
    extra_secrets: Sequence[str] = (),
    preexec_fn: Callable[[], None] | None = None,
    pass_fds: Sequence[int] = (),
) -> GitResult:
    """Spawn git HERE. The only caller is the owner git cell's entry point.

    This is :func:`run_git`'s body minus the hand-off: the cell is already the
    owner's process in the owner's mount namespace, so spawning git from it is
    the confined execution, not a daemon-uid fallback. The daemon never calls
    it -- :func:`run_git` goes to :mod:`tinyassets.role_git` instead.

    The environment is built HERE, from ``home_dir`` and ``path`` -- a caller
    cannot hand in a dict, so it cannot hand in ``GIT_DIR``,
    ``GIT_ALTERNATE_OBJECT_DIRECTORIES``, ``GIT_CONFIG_COUNT`` or a
    ``GIT_TRACE*``. Blindness is enforced rather than trusted: the key set is
    asserted against :data:`GIT_ENVIRONMENT_KEYS` and every known secret is
    rejected out of argv, options, the environment and the binary path before
    anything spawns.

    The child never inherits this process's environment, never gets a stdin,
    and on POSIX cannot write a core dump and starts in a NEW SESSION -- so it
    leads its own process group and a timeout takes down anything it spawned.
    Output is truncated to the last 64 KiB of each stream and scrubbed before
    it is returned or classified.
    """
    env = _checked_git_request(
        argv, cwd=cwd, home_dir=home_dir, path=path, options=options,
        timeout_s=timeout_s, git_binary=git_binary, extra_secrets=extra_secrets,
        pass_fds=pass_fds,
    )
    command = [git_binary, *options, *argv]
    run = launcher if launcher is not None else _default_launcher
    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "env": dict(env),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": float(timeout_s),
        "check": False,
        "shell": False,
    }
    inherited = () if _IS_WINDOWS else (*_INHERITED_FDS, *_SCOPED_FDS.get())
    if pass_fds or inherited:
        # The descriptor must survive into the child, or /proc/self/fd/<n>
        # in its cwd names nothing. `_INHERITED_FDS` is the staging in-use
        # share: a git that outlives its worker keeps the tree marked in use.
        kwargs["pass_fds"] = tuple(dict.fromkeys((*pass_fds, *inherited)))
    child_setup = preexec_fn if preexec_fn is not None else (
        None if _IS_WINDOWS else _disable_core_dumps
    )
    if child_setup is not None:
        kwargs["preexec_fn"] = child_setup
    if not _IS_WINDOWS:
        # Its own session, so kill_git can signal the whole process group; a
        # git that spawned a helper must not leave the helper behind.
        kwargs["start_new_session"] = True
    try:
        completed = run(command, **kwargs)
    except subprocess.TimeoutExpired:
        # The launcher has already killed the process group by here.
        raise WorkspaceGitError("timeout", f"git {argv[0]} exceeded {timeout_s}s") from None
    except OSError as exc:
        raise WorkspaceGitError("transport", f"git could not be started: {exc}") from None

    stdout_tail = scrub_text(_tail_text(getattr(completed, "stdout", b"")), extra_secrets)
    stderr_scrubbed = scrub_text(_tail_text(getattr(completed, "stderr", b"")), extra_secrets)
    return GitResult(
        returncode=int(getattr(completed, "returncode", 1)),
        stdout_tail=stdout_tail,
        stderr_class=classify_stderr(stderr_scrubbed),
        stderr_scrubbed=stderr_scrubbed,
    )


def _default_launcher(command: Sequence[str], **kwargs: Any) -> Any:
    """Spawn, wait bounded, and on timeout kill the whole process GROUP.

    ``subprocess.run``'s own timeout kills only the tracked pid, so a git that
    double-forked a helper would leave it running with the operation's file
    descriptors. This is the seam the cell's git goes through; a test injects
    its own launcher instead.
    """
    timeout = kwargs.pop("timeout", None)
    kwargs.pop("check", None)
    proc = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The process group, not the pid: see kill_git.
        try:
            kill_git(proc, timeout_s=10.0)
        except WorkspaceGitError:
            logging.getLogger(__name__).error("git survived SIGKILL after a timeout")
        raise
    return subprocess.CompletedProcess(list(command), proc.returncode, stdout, stderr)


def _disable_core_dumps() -> None:  # pragma: no cover - runs in the child
    """POSIX preexec: a core dump of a credentialed git would hold the token."""
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def kill_git(proc: object, *, timeout_s: float = 5.0) -> int:
    """SIGKILL a git started inside the cell and everything it spawned.

    :func:`run_git_in_cell` puts the child in its own session on POSIX, so
    signalling the process GROUP reaches a helper git double-forked away --
    killing only the tracked pid would leave it running. Raises ``timeout`` if
    the tracked process has still not exited: a caller must never be told a
    process is gone while it holds a lease or a credential.
    """
    pid = getattr(proc, "pid", None)
    if pid is None or not hasattr(proc, "wait"):
        raise WorkspaceGitError("bad_argument", "kill_git needs a started process")
    if not _IS_WINDOWS:
        try:
            os.killpg(os.getpgid(pid), _KILL_SIGNAL)
        except (ProcessLookupError, PermissionError):
            # Already reaped, or never had its own group: fall back to the pid.
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
    else:  # pragma: no cover - production is Linux; no process groups here
        try:
            proc.kill()
        except OSError:
            pass
    try:
        return int(proc.wait(timeout=float(timeout_s)))
    except subprocess.TimeoutExpired:
        raise WorkspaceGitError(
            "timeout", f"git did not exit within {timeout_s}s of SIGKILL"
        ) from None


def _require_ok(result: GitResult, what: str, fallback: str = "other") -> GitResult:
    if result.ok:
        return result
    code = result.stderr_class
    if code == "other":
        code = fallback
    raise WorkspaceGitError(code, f"{what} failed ({result.returncode}): {result.stderr_scrubbed}")


# ---------------------------------------------------------------------------
# Bundle helpers -- all credential-free
# ---------------------------------------------------------------------------


def _spawning(runner: Callable[..., GitResult] | None) -> Callable[..., GitResult]:
    """Which git seam these helpers use: the daemon's or the cell's own.

    The daemon (``None``) hands every git to the owner's cell through
    :func:`run_git`. Inside a cell there is no second cell to hand it to, so
    the cell passes :func:`run_git_in_cell` and the helper spawns git itself --
    confined execution, not a daemon-uid fallback. Resolved here, late, so a
    test that replaces ``run_git`` still sees its replacement.
    """
    return run_git if runner is None else runner


def _require_sha(commit_sha: str) -> str:
    if not isinstance(commit_sha, str) or not _SHA1_RE.match(commit_sha):
        raise WorkspaceGitError("bad_argument", "commit sha must be 40 lowercase hex characters")
    return commit_sha


def _require_ref(ref_name: str, label: str) -> str:
    if not isinstance(ref_name, str) or not _REF_COMPONENT_RE.match(ref_name):
        raise WorkspaceGitError("bad_argument", f"{label} is not a valid ref name")
    if ".." in ref_name or ref_name.endswith((".lock", "/", ".")) or "//" in ref_name:
        raise WorkspaceGitError("bad_argument", f"{label} is not a valid ref name")
    return ref_name


# git 2.43 and 2.53 both say "Repository lacks these prerequisite commits" on
# stderr and fail; older and newer wordings ("requires these refs") are matched
# too, so a git that warns instead of failing is still caught.
_PREREQUISITE_RE = re.compile(r"(?i)(prerequisite|requires th(?:is|ese) ref|lacks these)")


def _verify_prerequisite_free(
    target: Path,
    *,
    scratch_dir: str | os.PathLike[str],
    home_dir: str | os.PathLike[str],
    path: str,
    timeout_s: float,
    launcher: Callable[..., object],
    git_binary: str,
    runner: Callable[..., GitResult] | None = None,
) -> list[str]:
    """Verify a bundle in a FRESH EMPTY bare repo; return the refs it carries.

    Empty is the whole point: a bundle whose DECLARED prerequisites are absent
    cannot verify against a repository that has none.

    It is not the whole answer, and the caller must not treat it as one.
    Measured on git 2.53: a bundle made from a SHALLOW clone passes here --
    ``bundle verify`` reports the ref and says nothing about the parent commit
    it cannot reach, because the shallow boundary is not a declared
    prerequisite. Only the fsck-checked import
    (:func:`unbundle_into_fresh_repo`) catches that, with "did not send all
    necessary objects". The push path therefore runs BOTH, in that order, and
    nothing crosses the boundary on a `verify` alone.
    """
    scratch = Path(scratch_dir)
    if not scratch.is_dir():
        raise WorkspaceGitError(
            "bad_argument", "bundle verification scratch_dir must be a directory"
        )
    if any(scratch.iterdir()):
        raise WorkspaceGitError("bad_argument", "bundle verification scratch_dir must be empty")
    bare = scratch / "verify.git"
    bare.mkdir()
    spawn = _spawning(runner)

    def _run(argv: list[str]) -> GitResult:
        return spawn(
            argv,
            cwd=bare,
            home_dir=home_dir,
            path=path,
            timeout_s=timeout_s,
            launcher=launcher,
            git_binary=git_binary,
        )

    _require_ok(_run(["init", "--bare", "--quiet", "."]), "init bare", "verification")
    verified = _run(["bundle", "verify", str(target)])
    combined = f"{verified.stdout_tail}\n{verified.stderr_scrubbed}"
    if _PREREQUISITE_RE.search(combined):
        raise WorkspaceGitError(
            "verification",
            "bundle is not prerequisite-free: it needs objects it does not carry",
        )
    _require_ok(verified, "bundle verify", "verification")
    # ``bundle verify`` prints prose; ``list-heads`` prints "<sha> <ref>" and is
    # what the ref list is read from.
    heads = _require_ok(
        _run(["bundle", "list-heads", str(target)]), "bundle list-heads", "verification"
    )
    refs: list[str] = []
    for line in heads.stdout_tail.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            refs.append(parts[1].strip())
    if not refs:
        raise WorkspaceGitError("verification", "bundle carries no ref")
    return refs


def create_bundle(
    repo_dir: str | os.PathLike[str],
    commit_sha: str,
    bundle_path: str | os.PathLike[str],
    *,
    ref_name: str = "refs/tiny/export",
    home_dir: str | os.PathLike[str],
    path: str,
    scratch_dir: str | os.PathLike[str],
    timeout_s: float = 120.0,
    launcher: Callable[..., object] | None = None,
    git_binary: str = "git",
    runner: Callable[..., GitResult] | None = None,
) -> Path:
    """Bundle exactly one commit from ``repo_dir`` into ``bundle_path``.

    Runs with hooks disabled and object replacement off, so a replace ref or a
    hook planted in the repository cannot change what is exported, and then
    verifies its OWN output in a fresh empty bare repo (``scratch_dir``): a
    bundle that carries prerequisites is refused and deleted here rather than
    failing later on the far side of the boundary, where the failure is a
    half-populated workspace.
    """
    _require_sha(commit_sha)
    _require_ref(ref_name, "bundle ref")
    repo = Path(repo_dir)
    if not repo.is_dir():
        raise WorkspaceGitError("bad_argument", "create_bundle repo_dir must be a directory")
    target = Path(bundle_path)
    if target.exists() or target.is_symlink():
        raise WorkspaceGitError("bad_argument", "create_bundle refuses to overwrite bundle_path")
    if not target.parent.is_dir():
        raise WorkspaceGitError("bad_argument", "create_bundle bundle_path has no parent directory")

    common = ["-c", f"core.hooksPath={NULL_DEVICE}", "--no-replace-objects"]
    spawn = _spawning(runner)

    def _run(argv: list[str]) -> GitResult:
        return spawn(
            argv,
            cwd=repo,
            home_dir=home_dir,
            path=path,
            options=common,
            timeout_s=timeout_s,
            launcher=launcher,
            git_binary=git_binary,
        )

    _require_ok(_run(["update-ref", ref_name, commit_sha]), "update-ref")
    try:
        _require_ok(_run(["bundle", "create", str(target), ref_name]), "bundle create")
    finally:
        # The synthetic ref is temporary whatever happened above.
        _run(["update-ref", "-d", ref_name])
    if not target.is_file():
        raise WorkspaceGitError("verification", "bundle create produced no file")
    try:
        _verify_prerequisite_free(
            target,
            scratch_dir=scratch_dir,
            home_dir=home_dir,
            path=path,
            timeout_s=timeout_s,
            launcher=launcher,
            git_binary=git_binary,
            runner=runner,
        )
    except WorkspaceGitError:
        # An unverifiable bundle must not survive where a caller could pick it
        # up; the exception is the report, the unlink is the cleanup.
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return target


def verify_bundle(
    bundle_path: str | os.PathLike[str],
    *,
    max_bytes: int,
    scratch_dir: str | os.PathLike[str],
    home_dir: str | os.PathLike[str],
    path: str,
    timeout_s: float = 120.0,
    launcher: Callable[..., object] | None = None,
    git_binary: str = "git",
    runner: Callable[..., GitResult] | None = None,
) -> list[str]:
    """Verify a bundle credential-free in a fresh bare repo; return its refs.

    ``scratch_dir`` must be an existing empty directory: the bare repository is
    created inside it so no repository the caller cares about is ever the cwd
    of a verification that reads attacker-supplied pack data.
    """
    target = Path(bundle_path)
    try:
        info = os.lstat(target)
    except OSError:
        raise WorkspaceGitError("verification", "bundle path does not exist") from None
    if not stat.S_ISREG(info.st_mode):
        raise WorkspaceGitError("verification", "bundle path is not a regular file")
    if int(max_bytes) <= 0:
        raise WorkspaceGitError("bad_argument", "verify_bundle needs a positive max_bytes")
    if info.st_size > int(max_bytes):
        raise WorkspaceGitError("verification", "bundle exceeds the permitted size")

    return _verify_prerequisite_free(
        target,
        scratch_dir=scratch_dir,
        home_dir=home_dir,
        path=path,
        timeout_s=timeout_s,
        launcher=launcher,
        git_binary=git_binary,
        runner=runner,
    )


def unbundle_into_fresh_repo(
    bundle_path: str | os.PathLike[str],
    dest_dir: str | os.PathLike[str],
    *,
    ref_name: str,
    home_dir: str | os.PathLike[str],
    path: str,
    dest_fd: int | None = None,
    timeout_s: float = 300.0,
    launcher: Callable[..., object] | None = None,
    git_binary: str = "git",
    runner: Callable[..., GitResult] | None = None,
) -> str:
    """Populate an empty directory from a bundle; return the commit sha.

    The result has no remote and no reference to the bundle's path, which is
    the point: the workspace's ``.git`` must not tell user code where the
    host keeps anything, and must not carry a remote a later git could push to.

    ``dest_fd`` is a directory descriptor the CALLER holds open for the whole
    call. When given, git runs with ``cwd=/proc/self/fd/<n>`` and the
    descriptor passed through, so the destination cannot be swapped between
    the check and the write -- the path is never re-resolved. The caller must
    keep the descriptor open until this returns (and, for a bind, until the
    mount is made).
    """
    _require_ref(ref_name, "fetch ref")
    source = Path(bundle_path)
    if not source.is_file():
        raise WorkspaceGitError("verification", "bundle path is not a regular file")
    dest = Path(dest_dir)
    if dest_fd is not None:
        if not isinstance(dest_fd, int) or isinstance(dest_fd, bool):
            raise WorkspaceGitError("bad_argument", "dest_fd must be a directory descriptor")
        if _IS_WINDOWS:
            raise WorkspaceGitError(
                "bad_argument",
                "a descriptor-pinned destination needs POSIX; this host has no /proc/self/fd",
            )
        try:
            existing = os.listdir(dest_fd)
        except OSError as exc:
            raise WorkspaceGitError(
                "bad_argument", f"dest_fd is not a readable directory: {type(exc).__name__}"
            ) from None
        if existing:
            raise WorkspaceGitError("bad_argument", "dest_dir must be empty")
        work_dir: str | Path = f"/proc/self/fd/{dest_fd}"
    else:
        if dest.exists():
            if not dest.is_dir():
                raise WorkspaceGitError("bad_argument", "dest_dir is not a directory")
            if any(dest.iterdir()):
                raise WorkspaceGitError("bad_argument", "dest_dir must be empty")
        else:
            dest.mkdir(parents=True)
        work_dir = dest
    spawn = _spawning(runner)

    def _run(argv: list[str], options: Sequence[str] = ()) -> GitResult:
        return spawn(
            argv,
            cwd=work_dir,
            home_dir=home_dir,
            path=path,
            options=options,
            timeout_s=timeout_s,
            launcher=launcher,
            git_binary=git_binary,
            pass_fds=() if dest_fd is None else (dest_fd,),
        )

    _require_ok(_run(["init", "--quiet", "."]), "init", "verification")
    _require_ok(
        _run(
            ["fetch", "--quiet", str(source), f"{ref_name}:{ref_name}"],
            options=["-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true"],
        ),
        "fetch from bundle",
        "verification",
    )
    _require_ok(_run(["fsck", "--strict", "--no-dangling"]), "fsck", "verification")
    revision = _require_ok(_run(["rev-parse", ref_name]), "rev-parse", "verification")
    sha = revision.stdout_tail.strip()
    _require_sha(sha)

    remote = _run(["config", "--get", "remote.origin.url"])
    if remote.stdout_tail.strip():
        raise WorkspaceGitError("verification", "the populated repository carries a remote")
    config_path = dest / ".git" / "config"
    if config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8", errors="replace")
        resolved = source.resolve()
        for spelling in {str(source), str(resolved), resolved.as_posix()}:
            if spelling and spelling in config_text:
                raise WorkspaceGitError(
                    "verification", "the populated repository records a host path"
                )
    return sha


def populate_workspace_from_bundle(
    bundle_path: str | os.PathLike[str],
    dest_dir: str | os.PathLike[str],
    ref_name: str,
    checkout_ref: str,
    *,
    home_dir: str | os.PathLike[str],
    path: str,
    timeout_s: float = 300.0,
    launcher: Callable[..., object] | None = None,
    git_binary: str = "git",
    dest_fd: int | None = None,
    runner: Callable[..., GitResult] | None = None,
) -> str:
    """Unbundle into a fresh repo and check the commit out on a local branch.

    ``dest_fd`` (a directory descriptor the caller holds open for the whole
    call) pins BOTH steps to the same inode: the import and the checkout run
    with ``cwd=/proc/self/fd/<n>``, so a rename of ``dest_dir`` between them
    changes nothing (the end-to-end lane found the adapter passing this and
    only the import honouring it).
    """
    _require_ref(checkout_ref, "checkout ref")
    sha = unbundle_into_fresh_repo(
        bundle_path,
        dest_dir,
        ref_name=ref_name,
        home_dir=home_dir,
        path=path,
        timeout_s=timeout_s,
        launcher=launcher,
        git_binary=git_binary,
        dest_fd=dest_fd,
        runner=runner,
    )
    work_dir: str | os.PathLike[str] = (
        dest_dir if dest_fd is None else f"/proc/self/fd/{dest_fd}"
    )
    result = _spawning(runner)(
        ["checkout", "--quiet", "-B", checkout_ref, sha],
        cwd=work_dir,
        home_dir=home_dir,
        path=path,
        timeout_s=timeout_s,
        launcher=launcher,
        git_binary=git_binary,
        pass_fds=() if dest_fd is None else (dest_fd,),
    )
    _require_ok(result, "checkout", "verification")
    return sha

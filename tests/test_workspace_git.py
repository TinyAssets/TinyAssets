"""Tests for the credential-blind git layer (``tinyassets.workspace_git``).

Two kinds of test live here. The pure ones drive the broker protocol, the
environment, the forced options, address pinning and stderr classification
with injected fakes, so they run identically on Linux and Windows. The bundle
tests drive a REAL ``git``: the whole point of the bundle path is what git
actually writes into ``.git/config``, which a fake launcher cannot prove.
They skip when git is not on PATH.

``run_git`` here is :func:`tinyassets.workspace_git.run_git_in_cell` -- the
owner git cell's entry point, which is where the spawn, the environment, the
scrubbing and the kill-the-group behaviour all live. The daemon-side
``run_git`` builds no child at all; its hand-off to the cell has its own
tests at the bottom of this file.
"""

from __future__ import annotations

import itertools
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from tinyassets import workspace_git as wg
from tinyassets.workspace_git import (
    CredentialBroker,
    GitResult,
    WorkspaceGitError,
    classify_stderr,
    create_bundle,
    forced_git_options,
    git_environment,
    kill_git,
    libcurl_supports_multi_resolve,
    pin_address,
    populate_workspace_from_bundle,
    scrub_text,
    unbundle_into_fresh_repo,
    verify_bundle,
)
from tinyassets.workspace_git import run_git_in_cell as run_git

TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
IS_WINDOWS = os.name == "nt"

#: The daemon-side hand-off, captured before the fixture below repoints the
#: helpers' seam at the cell-side function. Its own tests use this.
daemon_run_git = wg.run_git


@pytest.fixture(autouse=True)
def _git_runs_where_the_cell_runs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bundle helpers orchestrate git steps; each step is a cell call.

    In production ``run_git`` hands every step to the owner's git cell, which
    runs ``run_git_in_cell``. These tests drive the helpers with an injected
    launcher, which only the cell-side function accepts -- so point the
    helpers' seam at it, the way the cell's own entry point does.
    """
    monkeypatch.setattr(wg, "run_git", wg.run_git_in_cell)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeCompleted:
    returncode: int = 0
    stdout: bytes = b""
    stderr: bytes = b""


class RecordingLauncher:
    """Stands in for ``subprocess.run`` and records exactly what it was given."""

    def __init__(self, result: FakeCompleted | None = None, raises: BaseException | None = None):
        self.result = result or FakeCompleted()
        self.raises = raises
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if self.raises is not None:
            raise self.raises
        return self.result

    @property
    def last_command(self) -> list[str]:
        return self.calls[-1][0]

    @property
    def last_kwargs(self) -> dict[str, object]:
        return self.calls[-1][1]


_SCRATCH_COUNTER = itertools.count()


def _fresh_scratch(tmp_path: Path) -> Path:
    """A new empty directory for one bundle verification."""
    scratch = tmp_path / f"vscratch-{next(_SCRATCH_COUNTER)}"
    scratch.mkdir()
    return scratch


@pytest.fixture()
def empty_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture()
def posix_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the POSIX branch on any host -- production is Linux, this box is not.

    Without this, every POSIX-only behaviour is asserted by its ABSENCE on
    Windows, and a mutation that deletes the behaviour stays green here.
    """
    monkeypatch.setattr(wg, "_IS_WINDOWS", False)


@pytest.fixture()
def windows_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wg, "_IS_WINDOWS", True)


@pytest.fixture()
def broker() -> CredentialBroker:
    made = CredentialBroker("https", "github.com", "owner/repo.git", "x-access-token", TOKEN)
    yield made
    made.close()


def _get_request(host: str = "github.com", path: str = "owner/repo.git") -> str:
    return f"operation=get\nprotocol=https\nhost={host}\npath={path}\n\n"


# ---------------------------------------------------------------------------
# 1. the module reads no environment variables
# ---------------------------------------------------------------------------


def test_module_source_never_reads_an_environment_variable() -> None:
    source = Path(wg.__file__).read_text(encoding="utf-8")
    # ``\benviron\b`` matches ``os.environ`` but not ``git_environment`` -- the
    # required public name contains the substring, so a substring grep cannot
    # be the assertion.
    assert re.search(r"\benviron\b", source) is None
    for forbidden in ("os.environ", "getenv", "putenv", "environb", "expandvars"):
        assert forbidden not in source


def test_module_docstring_states_the_boundary_verbatim() -> None:
    """The boundary statement is normative (design D1, round 3) -- not a gloss."""
    doc = " ".join((wg.__doc__ or "").split())
    assert "credential-blind" in doc.lower()
    for sentence in (
        "No credentialed git ever opens a workspace.",
        "No host-side git opens a workspace's ``.git`` after the workspace is "
        "published to user code.",
        "A host-side, credential-free initializer may populate a fresh, "
        "unpublished directory from a verified bundle.",
    ):
        assert " ".join(sentence.split()) in doc


# ---------------------------------------------------------------------------
# 2. CredentialBroker.answer -- the protocol, in process
# ---------------------------------------------------------------------------


def test_broker_answers_a_matching_get(broker: CredentialBroker) -> None:
    assert broker.answer(_get_request()) == f"username=x-access-token\npassword={TOKEN}\n"


def test_broker_answers_a_repeated_get_after_a_401(broker: CredentialBroker) -> None:
    first = broker.answer(_get_request())
    second = broker.answer(_get_request())
    assert first == second
    assert second is not None and TOKEN in second


def test_broker_matches_the_canonical_wire_path_exactly(broker: CredentialBroker) -> None:
    """The path git sends is matched EXACTLY, not normalised on both sides.

    Measured against git 2.43 and 2.53: for ``https://host/owner/repo.git`` the
    credential request carries ``path=owner/repo.git``, and for a URL without
    the suffix it carries ``path=owner/repo``. Those are different strings and
    on some hosts different resources, so stripping ``.git`` on both sides made
    a grant for one answer a request for the other. This layer always builds
    the ``.git`` URL, so that is the only spelling it answers.
    """
    assert broker.answer(_get_request(path="owner/repo.git")) is not None
    # git never sends a leading slash, but if it did it would still be the same
    # resource; anything else is a different one.
    assert broker.answer(_get_request(path="/owner/repo.git")) is not None
    assert broker.answer(_get_request(path="owner/repo")) is None
    assert broker.answer(_get_request(path="owner/repo.git/")) is None
    assert broker.answer(_get_request(path="owner/repo.GIT")) is None


def test_a_broker_bound_without_the_suffix_still_answers_the_canonical_path() -> None:
    """The binding is derived from the identity, so both spellings of the
    GRANT produce the one canonical path -- the asymmetry is on the REQUEST."""
    from tinyassets.workspace_git import canonical_credential_path

    assert canonical_credential_path("owner/repo") == "owner/repo.git"
    assert canonical_credential_path("owner/repo.git") == "owner/repo.git"
    assert canonical_credential_path("/owner/repo/") == "owner/repo.git"
    for spelling in ("owner/repo", "owner/repo.git"):
        made = CredentialBroker("https", "github.com", spelling, "x-access-token", TOKEN)
        try:
            assert made.answer(_get_request(path="owner/repo.git")) is not None
            assert made.answer(_get_request(path="owner/repo")) is None
        finally:
            made.close()


def test_broker_refuses_another_path(broker: CredentialBroker) -> None:
    assert broker.answer(_get_request(path="owner/other")) is None
    assert broker.answer(_get_request(path="attacker/repo")) is None
    # a prefix is not a match
    assert broker.answer(_get_request(path="owner/repo-evil")) is None


def test_broker_refuses_another_host(broker: CredentialBroker) -> None:
    assert broker.answer(_get_request(host="evil.example")) is None
    assert broker.answer(_get_request(host="github.com.evil.example")) is None


def test_broker_accepts_the_default_port_and_refuses_another(broker: CredentialBroker) -> None:
    assert broker.answer(_get_request(host="github.com:443")) is not None
    assert broker.answer(_get_request(host="github.com:8443")) is None


def test_broker_refuses_another_protocol(broker: CredentialBroker) -> None:
    request = "operation=get\nprotocol=http\nhost=github.com\npath=owner/repo\n"
    assert broker.answer(request) is None


def test_broker_refuses_a_request_without_a_path(broker: CredentialBroker) -> None:
    assert broker.answer("operation=get\nprotocol=https\nhost=github.com\n") is None


def test_broker_refuses_another_username(broker: CredentialBroker) -> None:
    request = _get_request() + "username=someone-else\n"
    assert broker.answer(request) is None


def test_broker_ignores_store_and_erase(broker: CredentialBroker) -> None:
    for operation in ("store", "erase"):
        request = f"operation={operation}\nprotocol=https\nhost=github.com\npath=owner/repo\n"
        answered = broker.answer(request)
        assert answered == ""
        assert TOKEN not in (answered or "")


def test_broker_refuses_an_unknown_operation(broker: CredentialBroker) -> None:
    unknown = "operation=capability\nprotocol=https\nhost=github.com\npath=owner/repo\n"
    assert broker.answer(unknown) is None
    assert broker.answer("") is None


def test_broker_ignores_unknown_keys(broker: CredentialBroker) -> None:
    request = _get_request() + "wwwauth[]=Basic realm=x\ncapability[]=authtype\n"
    assert broker.answer(request) is not None


def test_broker_exposes_nothing_after_close() -> None:
    made = CredentialBroker("https", "github.com", "owner/repo", "x-access-token", TOKEN)
    assert made.answer(_get_request()) is not None
    made.close()
    assert made.answer(_get_request()) is None
    store = "operation=store\nprotocol=https\nhost=github.com\npath=owner/repo\n"
    assert made.answer(store) is None
    with pytest.raises(WorkspaceGitError) as caught:
        _ = made.helper_command
    assert caught.value.code == "bad_argument"
    made.close()  # idempotent


@pytest.mark.parametrize(
    "kwargs",
    [
        {"protocol": "http"},
        {"host": ""},
        {"path": ""},
        {"username": ""},
        {"secret": ""},
        {"secret": "short"},
        {"host": "github.com\nevil"},
        {"secret": f"{TOKEN}\nprotocol=https"},
        {"path": "/"},
    ],
)
def test_broker_refuses_a_malformed_construction(kwargs: dict[str, str]) -> None:
    fields = {
        "protocol": "https",
        "host": "github.com",
        "path": "owner/repo",
        "username": "x-access-token",
        "secret": TOKEN,
    }
    fields.update(kwargs)
    with pytest.raises(WorkspaceGitError) as caught:
        CredentialBroker(**fields)
    assert caught.value.code == "bad_argument"


# ---------------------------------------------------------------------------
# 3. the broker's transport
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not IS_WINDOWS, reason="POSIX transport is exercised on POSIX")
def test_serve_refuses_loudly_on_windows(tmp_path: Path, broker: CredentialBroker) -> None:
    with pytest.raises(WorkspaceGitError) as caught:
        broker.serve(socket_dir=tmp_path)
    assert caught.value.code == "transport"


#: A caller directory as deep as production's staging path gets. The socket
#: used to live inside it, so this is what broke: `sun_path` is 108 bytes.
_PATHOLOGICAL_CALLER_DIR = (
    "/data/universes/universe-0f8c2a1b/.workspace-staging/"
    "run-2026-08-31T04-55-12-4f2a9c/checkout-the-monorepo-node/broker"
)


@pytest.mark.skipif(IS_WINDOWS, reason="unix domain sockets")
def test_the_broker_picks_a_socket_path_short_enough_for_any_caller(
    broker: CredentialBroker,
) -> None:
    """The address limit must not depend on how deeply the CALLER is nested.

    Found live on the droplet, 2026-08-31: `full_route` refused with
    "broker socket path is too long for sun_path" because the socket went
    inside `<data>/.workspace-staging/<run>/<node>/`. A longer run id would
    have refused every checkout in production, with a message naming a C
    struct field no user could act on.
    """
    helper = broker.serve()
    try:
        socket_path = Path(helper.rsplit(" ", 1)[-1])
        assert socket_path.is_socket()
        assert len(str(socket_path).encode()) <= wg._MAX_SOCKET_PATH_BYTES
        # Short with room to spare, not merely inside the limit today.
        assert len(str(socket_path).encode()) < 60, socket_path
        assert socket_path.parent.name.startswith(wg._BROKER_DIR_PREFIX)
        assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
        # The caller's own directory has nothing to do with it, however deep.
        assert _PATHOLOGICAL_CALLER_DIR not in str(socket_path)
        assert len(_PATHOLOGICAL_CALLER_DIR.encode()) > wg._MAX_SOCKET_PATH_BYTES, (
            "the fixture must be long enough to have been the bug"
        )
    finally:
        broker.close()


@pytest.mark.skipif(IS_WINDOWS, reason="unix domain sockets")
def test_the_broker_removes_the_directory_it_made(broker: CredentialBroker) -> None:
    helper = broker.serve()
    directory = Path(helper.rsplit(" ", 1)[-1]).parent
    assert directory.is_dir()
    broker.close()
    assert not directory.exists(), "a directory the broker made is the broker's to remove"


@pytest.mark.skipif(IS_WINDOWS, reason="unix domain sockets")
def test_the_broker_leaves_a_directory_the_caller_passed(
    broker: CredentialBroker,
) -> None:
    """An override is the caller's; deleting it would be a surprise."""
    caller_dir = Path("/tmp") / f"wgb-caller-{os.getpid()}-{secrets.token_hex(3)}"
    caller_dir.mkdir(parents=True)
    try:
        broker.serve(socket_dir=caller_dir)
        broker.close()
        assert caller_dir.is_dir(), "the caller's directory survives"
        assert not (caller_dir / "credential.sock").exists(), "but the socket does not"
        assert not (caller_dir / "credential_helper.py").exists()
    finally:
        for name in ("credential.sock", "credential_helper.py"):
            (caller_dir / name).unlink(missing_ok=True)
        caller_dir.rmdir()


@pytest.mark.skipif(IS_WINDOWS, reason="unix domain sockets")
def test_an_override_too_long_for_sun_path_is_refused_with_the_number(
    tmp_path: Path, broker: CredentialBroker
) -> None:
    """The guard stays for the override path, and now says what to do."""
    deep = tmp_path / ("d" * 60) / ("e" * 60)
    deep.mkdir(parents=True)
    with pytest.raises(WorkspaceGitError) as caught:
        broker.serve(socket_dir=deep)
    assert caught.value.code == "bad_argument"
    assert "shorter socket_dir" in str(caught.value)
    assert str(wg._MAX_SOCKET_PATH_BYTES) in str(caught.value)


@pytest.mark.skipif(IS_WINDOWS, reason="unix domain sockets")
def test_two_brokers_do_not_share_a_socket_directory() -> None:
    first = CredentialBroker("https", "github.com", "owner/name", "x-access-token", TOKEN)
    second = CredentialBroker("https", "github.com", "owner/name2", "x-access-token", TOKEN)
    try:
        one = Path(first.serve().rsplit(" ", 1)[-1]).parent
        two = Path(second.serve().rsplit(" ", 1)[-1]).parent
        assert one != two
    finally:
        first.close()
        second.close()


@pytest.mark.skipif(IS_WINDOWS, reason="unix domain sockets")
def test_a_missing_socket_root_fails_loudly_rather_than_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fallback would silently reintroduce a caller-length-dependent path."""
    with pytest.raises(WorkspaceGitError) as caught:
        wg.broker_socket_dir("/no/such/root/for/a/socket")
    assert caught.value.code == "transport"


@pytest.mark.skipif(IS_WINDOWS, reason="unix domain sockets")
def test_serve_answers_over_the_unix_socket(tmp_path: Path, broker: CredentialBroker) -> None:
    socket_dir = Path("/tmp") / f"wgb-{os.getpid()}"
    socket_dir.mkdir(parents=True, exist_ok=False)
    try:
        helper = broker.serve(socket_dir=socket_dir)
        assert helper.startswith("!")
        assert str(socket_dir / "credential.sock") in helper
        assert (socket_dir / "credential_helper.py").is_file()
        assert broker.helper_command == helper

        for _ in range(2):  # a 401 retry issues a second get
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(5.0)
            client.connect(str(socket_dir / "credential.sock"))
            client.sendall(_get_request().encode("utf-8"))
            client.shutdown(socket.SHUT_WR)
            received = b""
            while True:
                block = client.recv(4096)
                if not block:
                    break
                received += block
            client.close()
            assert received.decode() == f"username=x-access-token\npassword={TOKEN}\n"

        # a request for another repository is answered with nothing at all
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5.0)
        client.connect(str(socket_dir / "credential.sock"))
        client.sendall(_get_request(path="attacker/repo").encode("utf-8"))
        client.shutdown(socket.SHUT_WR)
        assert client.recv(4096) == b""
        client.close()

        broker.close()
        assert not (socket_dir / "credential.sock").exists()
    finally:
        shutil.rmtree(socket_dir, ignore_errors=True)


def test_helper_command_is_unavailable_before_serving(broker: CredentialBroker) -> None:
    with pytest.raises(WorkspaceGitError):
        _ = broker.helper_command


# ---------------------------------------------------------------------------
# 4. secret registration and scrubbing
# ---------------------------------------------------------------------------


def test_a_live_broker_registers_its_secret_for_scrubbing() -> None:
    text = f"remote: https://x-access-token:{TOKEN}@github.com/owner/repo"
    with CredentialBroker("https", "github.com", "owner/repo", "u", TOKEN) as made:
        assert made.answer(_get_request()) is not None
        scrubbed = scrub_text(text)
        assert TOKEN not in scrubbed
        assert "[redacted]" in scrubbed
    # after close the exact secret is no longer registered; the generic
    # patterns still catch it
    assert TOKEN not in scrub_text(text)


def test_scrub_removes_generic_credential_shapes() -> None:
    raw = (
        "fatal: unable to access 'https://user:s3cr3t@github.com/o/r.git/'\n"
        "Authorization: Basic dXNlcjpwYXNz\n"
        "token ghp_0123456789ABCDEFGHIJKLMNOPQRSTUV used\n"
        "token github_pat_11ABCDEFG0123456789_abcdefghijklmnop used\n"
    )
    scrubbed = scrub_text(raw)
    assert "s3cr3t" not in scrubbed
    assert "dXNlcjpwYXNz" not in scrubbed
    assert "ghp_0123456789ABCDEFGHIJKLMNOPQRSTUV" not in scrubbed
    assert "github_pat_11ABCDEFG0123456789_abcdefghijklmnop" not in scrubbed
    assert scrubbed.count("[redacted]") >= 4


def test_run_git_stdout_keeps_a_branch_that_looks_like_a_secret(
    tmp_path: Path, empty_home: Path
) -> None:
    """``stdout_tail`` is PARSED, so scrubbing it may not rewrite git's nouns.

    ``_head_ref`` and ``_observed_ref`` read refs straight out of ``ls-remote``
    output. A generic `sk-`-token rule in ``scrub_text`` would turn a branch
    genuinely named ``sk-login-timeout-fix`` into ``[redacted]``, and push
    reconciliation would then read a landed push as failed. Credential shapes
    that broad belong to the diagnostic-only path
    (``providers.diagnostics.redacted_failure_detail``), never here.
    """
    sha = "9" * 40
    launcher = RecordingLauncher(FakeCompleted(stdout=(
        f"ref: refs/heads/sk-login-timeout-fix\tHEAD\n"
        f"{sha}\trefs/heads/sk-login-timeout-fix\n"
    ).encode()))
    result = run_git(
        ["ls-remote", "--symref", "https://github.com/o/r.git", "HEAD"],
        cwd=tmp_path, home_dir=empty_home, path="/usr/bin", timeout_s=5, launcher=launcher,
    )
    assert "refs/heads/sk-login-timeout-fix" in result.stdout_tail
    assert "[redacted]" not in result.stdout_tail
    # The parse those callers actually perform still finds the ref and the sha.
    rows = [line.split() for line in result.stdout_tail.splitlines()]
    assert ["ref:", "refs/heads/sk-login-timeout-fix", "HEAD"] in rows
    assert [sha, "refs/heads/sk-login-timeout-fix"] in rows


def test_run_git_stdout_still_scrubs_a_real_credential(
    tmp_path: Path, empty_home: Path
) -> None:
    """Narrowing the net back must not stop it catching what it always caught."""
    launcher = RecordingLauncher(FakeCompleted(
        stdout=f"remote: https://x-access-token:{TOKEN}@github.com/o/r\n".encode(),
    ))
    result = run_git(
        ["ls-remote", "https://github.com/o/r.git"],
        cwd=tmp_path, home_dir=empty_home, path="/usr/bin", timeout_s=5, launcher=launcher,
    )
    assert TOKEN not in result.stdout_tail and "[redacted]" in result.stdout_tail


def test_error_messages_are_scrubbed_in_args_not_only_in_str() -> None:
    with CredentialBroker("https", "github.com", "owner/repo", "u", TOKEN):
        error = WorkspaceGitError("auth", f"git said {TOKEN}")
    assert TOKEN not in str(error)
    assert TOKEN not in repr(error)
    assert all(TOKEN not in str(arg) for arg in error.args)
    assert error.code == "auth"


def test_an_unknown_error_code_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        WorkspaceGitError("nonsense", "x")


# ---------------------------------------------------------------------------
# 5. git_environment
# ---------------------------------------------------------------------------


def test_git_environment_key_set_is_exactly_the_specified_list(empty_home: Path) -> None:
    built = git_environment(empty_home, path="/usr/bin")
    assert set(built) == {
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GIT_ASKPASS",
        "HOME",
        "PATH",
        "LANG",
    }
    assert set(built) == set(wg.GIT_ENVIRONMENT_KEYS)


def test_git_environment_values(empty_home: Path) -> None:
    built = git_environment(empty_home, path="/usr/bin:/bin")
    null_device = "NUL" if IS_WINDOWS else "/dev/null"
    assert built["GIT_CONFIG_SYSTEM"] == null_device
    assert built["GIT_CONFIG_GLOBAL"] == null_device
    assert built["GIT_CONFIG_NOSYSTEM"] == "1"
    assert built["GIT_TERMINAL_PROMPT"] == "0"
    assert built["GIT_ASKPASS"] == ("NUL" if IS_WINDOWS else "/bin/false")
    assert built["HOME"] == str(empty_home)
    assert built["PATH"] == "/usr/bin:/bin"
    assert built["LANG"] == "C.UTF-8"


def test_git_environment_never_sets_a_trace_variable(empty_home: Path) -> None:
    built = git_environment(empty_home, path="/usr/bin")
    assert not [key for key in built if key.startswith("GIT_TRACE")]


def test_git_environment_refuses_a_non_empty_or_missing_home(tmp_path: Path) -> None:
    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / ".gitconfig").write_text("[user]\n", encoding="utf-8")
    with pytest.raises(WorkspaceGitError) as caught:
        git_environment(populated, path="/usr/bin")
    assert caught.value.code == "bad_argument"
    with pytest.raises(WorkspaceGitError):
        git_environment(tmp_path / "missing", path="/usr/bin")


def test_git_environment_requires_an_explicit_path(empty_home: Path) -> None:
    with pytest.raises(WorkspaceGitError):
        git_environment(empty_home, path="")
    with pytest.raises(WorkspaceGitError):
        git_environment(empty_home, path="/usr/bin\nevil")


# ---------------------------------------------------------------------------
# 6. forced_git_options
# ---------------------------------------------------------------------------


def test_forced_git_options_is_the_exact_pinned_list() -> None:
    null_device = "NUL" if IS_WINDOWS else "/dev/null"
    assert forced_git_options(
        "github.com", "140.82.121.4", "!python helper.py sock", multi_resolve=True
    ) == [
        "-c", f"core.hooksPath={null_device}",
        "-c", "core.fsmonitor=false",
        "-c", "credential.helper=",
        "-c", "credential.helper=!python helper.py sock",
        "-c", "credential.useHttpPath=true",
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        "-c", "http.followRedirects=false",
        "-c", "submodule.recurse=false",
        "-c", "transfer.fsckObjects=true",
        "-c", "fetch.fsckObjects=true",
        "-c", "receive.fsckObjects=true",
        "-c", "http.curloptResolve=github.com:443:140.82.121.4",
    ]


def test_the_empty_credential_helper_comes_first() -> None:
    options = forced_git_options("github.com", "140.82.121.4", "!broker", multi_resolve=True)
    helpers = [value for value in options if value.startswith("credential.helper=")]
    assert helpers == ["credential.helper=", "credential.helper=!broker"]
    # and the reset really precedes the broker in the argument list
    assert options.index("credential.helper=") < options.index("credential.helper=!broker")


def _resolve_rule(options: list[str]) -> str:
    rules = [value for value in options if value.startswith("http.curloptResolve=")]
    assert len(rules) == 1, f"expected exactly one resolve rule, got {rules}"
    return rules[0]


def test_resolve_rule_ipv4_only() -> None:
    options = forced_git_options(
        "github.com", ["140.82.121.4", "140.82.121.3"], "!broker", multi_resolve=True
    )
    assert _resolve_rule(options) == "http.curloptResolve=github.com:443:140.82.121.4,140.82.121.3"


def test_resolve_rule_ipv6_only_is_bracketed() -> None:
    options = forced_git_options(
        "github.com", ["2606:50c0:8000::153", "2606:50c0:8001::153"], "!broker",
        multi_resolve=True,
    )
    assert _resolve_rule(options) == (
        "http.curloptResolve=github.com:443:[2606:50c0:8000::153],[2606:50c0:8001::153]"
    )


def test_resolve_rule_mixed_families_keeps_dns_order() -> None:
    options = forced_git_options(
        "github.com", ["2606:50c0:8000::153", "140.82.121.4"], "!broker", multi_resolve=True
    )
    assert _resolve_rule(options) == (
        "http.curloptResolve=github.com:443:[2606:50c0:8000::153],140.82.121.4"
    )


def test_a_single_address_still_produces_one_rule() -> None:
    for given in ("140.82.121.4", ["140.82.121.4"], ("140.82.121.4",)):
        for multi in (True, False):
            options = forced_git_options("github.com", given, "!broker", multi_resolve=multi)
            assert _resolve_rule(options) == "http.curloptResolve=github.com:443:140.82.121.4"


def test_an_old_libcurl_refuses_several_addresses_instead_of_emitting_a_comma_list() -> None:
    """Below 7.59.0 a comma list is mis-parsed; refuse rather than emit it.

    The caller must decide to run one address per operation -- doing that
    silently here would drop the other addresses without anyone choosing to.
    """
    with pytest.raises(WorkspaceGitError) as caught:
        forced_git_options(
            "github.com", ["140.82.121.4", "140.82.121.3"], "!broker", multi_resolve=False
        )
    assert caught.value.code == "bad_argument"
    assert "one address per operation" in str(caught.value)


def test_the_resolve_rule_host_is_canonical_lower_case() -> None:
    options = forced_git_options("GitHub.COM", ["140.82.121.4"], "!broker", multi_resolve=True)
    assert _resolve_rule(options) == "http.curloptResolve=github.com:443:140.82.121.4"


def test_a_host_carrying_a_port_never_reaches_the_resolve_rule() -> None:
    """A resolve entry is (host, port, address); the port is ours, not input."""
    for hostile in ("github.com:8443", "github.com:443", "github.com:80"):
        with pytest.raises(WorkspaceGitError) as caught:
            forced_git_options(hostile, ["140.82.121.4"], "!broker", multi_resolve=True)
        assert caught.value.code == "bad_argument"
    # and the rule we do emit pins 443 and nothing else
    rule = _resolve_rule(
        forced_git_options("github.com", ["140.82.121.4"], "!broker", multi_resolve=True)
    )
    assert rule.split(":")[1] == "443"
    assert rule.count(":") == 2
    assert "8443" not in rule and ":80:" not in rule


def test_an_address_carrying_a_port_is_refused() -> None:
    with pytest.raises(WorkspaceGitError) as caught:
        forced_git_options("github.com", ["140.82.121.4:8443"], "!broker", multi_resolve=True)
    assert caught.value.code == "bad_argument"


@pytest.mark.parametrize(
    "host,address,helper",
    [
        ("github.com", "not-an-ip", "!broker"),
        ("bad host", "140.82.121.4", "!broker"),
        ("github.com", "140.82.121.4", "!broker\ncore.hooksPath=/tmp/evil"),
        ("github.com", "140.82.121.4", ""),
        ("github.com", [], "!broker"),
        ("github.com", ["140.82.121.4", "10.0.0.5\nevil"], "!broker"),
    ],
)
def test_forced_git_options_refuses_injection(host: str, address, helper: str) -> None:
    with pytest.raises(WorkspaceGitError) as caught:
        forced_git_options(host, address, helper, multi_resolve=True)
    assert caught.value.code == "bad_argument"


def test_multi_resolve_is_a_required_decision_not_a_default() -> None:
    """A caller must state which libcurl it is talking to."""
    with pytest.raises(TypeError):
        forced_git_options("github.com", ["140.82.121.4"], "!broker")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "version_text,supported",
    [
        ("git 2.47.0\nlibcurl/7.59.0 OpenSSL/3.0", True),
        ("libcurl/7.59.1", True),
        ("libcurl/8.5.0 OpenSSL/3.0.13 zlib/1.3", True),
        ("libcurl/7.60", True),
        ("libcurl/7.58.0 OpenSSL/1.1.0", False),
        ("libcurl/7.29.0", False),
        ("libcurl/6.99.99", False),
    ],
)
def test_libcurl_supports_multi_resolve(version_text: str, supported: bool) -> None:
    assert libcurl_supports_multi_resolve(version_text) is supported


@pytest.mark.parametrize(
    "unreadable",
    ["curl 8.5.0 (x86_64-pc-linux-gnu)", "", "   ", "libcurl/", None, 75900],
)
def test_an_unreadable_libcurl_version_fails_loudly(unreadable) -> None:
    """A silent False is a permanent invisible downgrade.

    Answering "no multi-resolve" for text we could not read means every
    operation quietly runs against one address forever -- working, a little
    worse, and never reported. It is asked once per worker, so a loud failure
    costs nothing and a silent one costs the fallback nobody chose.
    """
    with pytest.raises(WorkspaceGitError) as caught:
        libcurl_supports_multi_resolve(unreadable)
    assert caught.value.code == "bad_argument"


# ---------------------------------------------------------------------------
# 7. pin_address
# ---------------------------------------------------------------------------


def _classifier_stub(ip_text: str) -> str:
    """Mirrors the production contract: return the address, or raise."""
    if ip_text.startswith(("10.", "192.168.", "127.", "169.254.")):
        raise ValueError("resolved address is not globally routable")
    return ip_text


# ---------------------------------------------------------------------------
# 6b. GitTransport -- one validated object everything downstream reads
# ---------------------------------------------------------------------------


def _transport(repo: str = "owner/name", **over):
    from tinyassets.workspace_git import GitTransport

    kwargs = {
        "curl_version_text": "libcurl/8.5.0",
        # Required: the host is the connection's, never a platform default.
        "host": "github.com",
        "resolver": lambda h, p: ["140.82.121.4", "140.82.121.3"],
        "classifier": _classifier_stub,
    }
    kwargs.update(over)
    return GitTransport.build(repo, **kwargs)


def test_the_transport_derives_url_binding_and_rule_from_one_identity() -> None:
    """One derivation: a caller cannot pin one host and clone from another."""
    transport = _transport()
    assert transport.url == "https://github.com/owner/name.git"
    assert transport.broker_binding() == ("https", "github.com", "owner/name.git")
    assert transport.repo == "owner/name"
    assert transport.port == 443
    rule = [
        option
        for option in transport.forced_options("!broker")
        if option.startswith("http.curloptResolve=")
    ]
    assert rule == ["http.curloptResolve=github.com:443:140.82.121.4,140.82.121.3"]
    # the URL's host, the rule's host and the binding's host are the SAME string
    assert transport.url.split("/")[2] == transport.host
    assert rule[0].split("=")[1].split(":")[0] == transport.host
    assert transport.broker_binding()[1] == transport.host


def test_the_transport_canonicalises_the_host_and_the_repo() -> None:
    transport = _transport("Owner/Name", host="GitHub.COM")
    assert transport.host == "github.com"
    assert transport.url.startswith("https://github.com/")
    # the repo case is the user's; only the HOST is case-insensitive
    assert transport.credential_path.endswith(".git")


def test_the_transport_refuses_anything_but_https_on_443() -> None:
    from tinyassets.workspace_git import WorkspaceGitError as Err

    for kwargs in ({"port": 8443}, {"port": 80}, {"host": "not a host"}):
        with pytest.raises(Err) as caught:
            _transport(**kwargs)
        assert caught.value.code == "bad_argument"


@pytest.mark.parametrize("repo", ["", "name", "owner/name/extra", "/", "owner//name"])
def test_the_transport_refuses_a_repo_that_is_not_owner_slash_name(repo: str) -> None:
    from tinyassets.workspace_git import WorkspaceGitError as Err

    with pytest.raises(Err) as caught:
        _transport(repo)
    assert caught.value.code == "bad_argument"


def test_the_transport_refuses_a_split_dns_answer() -> None:
    from tinyassets.workspace_git import WorkspaceGitError as Err

    with pytest.raises(Err) as caught:
        _transport(resolver=lambda h, p: ["140.82.121.4", "169.254.169.254"])
    assert caught.value.code == "address_refused"


def test_the_transport_pins_one_address_on_an_old_libcurl() -> None:
    transport = _transport(curl_version_text="libcurl/7.29.0")
    assert transport.multi_resolve is False
    assert transport.addresses == ("140.82.121.4",)
    rule = [
        option
        for option in transport.forced_options("!broker")
        if option.startswith("http.curloptResolve=")
    ]
    assert rule == ["http.curloptResolve=github.com:443:140.82.121.4"]


def test_the_transport_binding_is_what_the_broker_answers() -> None:
    """The binding and the request git will send must be the same string."""
    transport = _transport()
    protocol, host, path = transport.broker_binding()
    made = CredentialBroker(protocol, host, path, "x-access-token", TOKEN)
    try:
        # what git sends for transport.url, measured against git 2.43/2.53
        assert made.answer(_get_request(host=host, path="owner/name.git")) is not None
        assert made.answer(_get_request(host=host, path="owner/name")) is None
    finally:
        made.close()


def test_pin_address_returns_every_validated_address_in_dns_order() -> None:
    resolved = ["140.82.121.4", "140.82.121.3", "2606:50c0:8000::153"]
    pinned = pin_address("github.com", lambda h, p: resolved, _classifier_stub)
    assert pinned == ("140.82.121.4", "140.82.121.3", "2606:50c0:8000::153")
    assert isinstance(pinned, tuple)
    # a dead first address must not be able to fail the whole operation, so the
    # siblings have to survive the call
    assert len(pinned) == len(resolved)


def test_pin_address_feeds_forced_git_options_directly() -> None:
    pinned = pin_address(
        "github.com", lambda h, p: ["140.82.121.4", "2606:50c0:8000::153"], _classifier_stub
    )
    rule = _resolve_rule(forced_git_options("github.com", pinned, "!broker", multi_resolve=True))
    assert rule == "http.curloptResolve=github.com:443:140.82.121.4,[2606:50c0:8000::153]"
    # and on an old libcurl the same tuple is a refusal, not a silent truncation
    with pytest.raises(WorkspaceGitError):
        forced_git_options("github.com", pinned, "!broker", multi_resolve=False)


def test_pin_address_refuses_a_split_answer_rather_than_taking_the_public_subset() -> None:
    resolved = ["140.82.121.4", "169.254.169.254", "140.82.121.3"]
    with pytest.raises(WorkspaceGitError) as caught:
        pin_address("github.com", lambda h, p: resolved, _classifier_stub)
    assert caught.value.code == "address_refused"
    # the refusal never echoes the offending address back
    assert "169.254.169.254" not in str(caught.value)


def test_pin_address_refuses_an_empty_resolution() -> None:
    with pytest.raises(WorkspaceGitError) as caught:
        pin_address("github.com", lambda h, p: [], _classifier_stub)
    assert caught.value.code == "address_refused"


def test_pin_address_refuses_when_the_resolver_fails() -> None:
    def boom(hostname: str, port: int) -> list[str]:
        raise OSError("dns is down")

    with pytest.raises(WorkspaceGitError) as caught:
        pin_address("github.com", boom, _classifier_stub)
    assert caught.value.code == "address_refused"


def test_pin_address_asks_the_resolver_for_port_443() -> None:
    seen: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int) -> list[str]:
        seen.append((hostname, port))
        return ["140.82.121.4"]

    pin_address("github.com", resolver, _classifier_stub)
    assert seen == [("github.com", 443)]


def test_pin_address_defaults_are_the_production_outbound_driver_functions() -> None:
    from tinyassets.storage import outbound_connections

    assert wg._production_resolver() is outbound_connections._default_dns_resolver
    assert wg._production_classifier() is outbound_connections._classify_global_address


def test_pin_address_with_the_real_production_classifier() -> None:
    """The real classifier RAISES on refusal; pin_address must read that."""
    from tinyassets.storage import outbound_connections

    real = outbound_connections._classify_global_address
    assert pin_address("example.test", lambda h, p: ["93.184.216.34"], real) == ("93.184.216.34",)
    for private in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "not-an-ip"):
        with pytest.raises(WorkspaceGitError) as caught:
            pin_address("example.test", lambda h, p: [private], real)
        assert caught.value.code == "address_refused"


# ---------------------------------------------------------------------------
# 8. run_git
# ---------------------------------------------------------------------------


def test_run_git_builds_git_options_then_argv(tmp_path: Path, empty_home: Path) -> None:
    launcher = RecordingLauncher()
    run_git(
        ["clone", "--bare", "https://github.com/o/r.git", "staging"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        options=["-c", "core.fsmonitor=false"],
        timeout_s=30,
        launcher=launcher,
    )
    assert launcher.last_command == [
        "git",
        "-c",
        "core.fsmonitor=false",
        "clone",
        "--bare",
        "https://github.com/o/r.git",
        "staging",
    ]


def test_run_git_never_inherits_and_never_offers_a_stdin(
    tmp_path: Path, empty_home: Path
) -> None:
    launcher = RecordingLauncher()
    run_git(
        ["status"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        timeout_s=5,
        launcher=launcher,
    )
    kwargs = launcher.last_kwargs
    # The environment is built INSIDE run_git from home_dir and path; a caller
    # never supplies one, so it cannot supply an extra variable either.
    assert kwargs["env"] == dict(git_environment(empty_home, path="/usr/bin"))
    assert set(kwargs["env"]) == set(wg.GIT_ENVIRONMENT_KEYS)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["timeout"] == 5.0


def test_run_git_disables_core_dumps_on_posix_only(tmp_path: Path, empty_home: Path) -> None:
    launcher = RecordingLauncher()
    run_git(
        ["status"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        timeout_s=5,
        launcher=launcher,
    )
    if IS_WINDOWS:
        assert "preexec_fn" not in launcher.last_kwargs
    else:
        assert launcher.last_kwargs["preexec_fn"] is wg._disable_core_dumps


def test_run_git_takes_an_injected_preexec(tmp_path: Path, empty_home: Path) -> None:
    launcher = RecordingLauncher()
    marker: list[str] = []

    def child_setup() -> None:
        marker.append("ran")

    run_git(
        ["status"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        timeout_s=5,
        launcher=launcher,
        preexec_fn=child_setup,
    )
    assert launcher.last_kwargs["preexec_fn"] is child_setup


def test_run_git_starts_a_new_session_on_posix(
    tmp_path: Path, empty_home: Path, posix_mode: None
) -> None:
    """A new session means kill_git can signal the group, not just the pid."""
    launcher = RecordingLauncher()
    run_git(
        ["status"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        timeout_s=5,
        launcher=launcher,
    )
    assert launcher.last_kwargs["start_new_session"] is True
    assert launcher.last_kwargs["preexec_fn"] is wg._disable_core_dumps


def test_run_git_omits_posix_only_arguments_on_windows(
    tmp_path: Path, empty_home: Path, windows_mode: None
) -> None:
    """subprocess rejects both arguments there, so they must not be passed."""
    launcher = RecordingLauncher()
    run_git(
        ["status"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        timeout_s=5,
        launcher=launcher,
    )
    assert "start_new_session" not in launcher.last_kwargs
    assert "preexec_fn" not in launcher.last_kwargs


def test_a_caller_cannot_hand_run_git_an_environment_at_all(
    tmp_path: Path, empty_home: Path
) -> None:
    """Blindness is ENFORCED, not trusted: there is no env parameter to abuse.

    Previously a caller passed a dict and run_git screened it for GIT_TRACE*.
    Screening is weaker than not accepting: GIT_DIR, GIT_ALTERNATE_OBJECT_
    DIRECTORIES and GIT_CONFIG_COUNT would all have passed that screen.
    """
    for hostile in (
        {"GIT_TRACE_CURL": "1"},
        {"GIT_DIR": "/etc"},
        {"GIT_ALTERNATE_OBJECT_DIRECTORIES": "/proc"},
        {"GIT_CONFIG_COUNT": "1"},
    ):
        with pytest.raises(TypeError):
            run_git(
                ["status"],
                cwd=tmp_path,
                home_dir=empty_home,
                path="/usr/bin",
                timeout_s=5,
                launcher=RecordingLauncher(),
                env=hostile,  # type: ignore[call-arg]
            )
    # and the environment it does build carries none of them
    built = run_git_environment_for(tmp_path, empty_home)
    assert not [key for key in built if key.startswith("GIT_TRACE")]
    assert "GIT_DIR" not in built
    assert "GIT_CONFIG_COUNT" not in built
    assert "GIT_ALTERNATE_OBJECT_DIRECTORIES" not in built


def run_git_environment_for(tmp_path: Path, empty_home: Path) -> dict[str, str]:
    """The env run_git actually spawns with, captured from the launcher."""
    launcher = RecordingLauncher()
    run_git(
        ["status"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        timeout_s=5,
        launcher=launcher,
    )
    return dict(launcher.last_kwargs["env"])


def test_run_git_refuses_to_spawn_when_a_secret_is_in_the_invocation(
    tmp_path: Path, empty_home: Path
) -> None:
    """The broker is the ONLY path a secret may take.

    A token in a URL, in a ``-c http.extraHeader``, or in the binary path all
    defeat credential-blindness silently. Every one of them is visible before
    the spawn, so every one of them is refused there.
    """
    launcher = RecordingLauncher()
    with CredentialBroker("https", "github.com", "o/r", "x-access-token", TOKEN):
        for argv, options, binary in (
            (["clone", f"https://x:{TOKEN}@github.com/o/r.git"], (), "git"),
            (["fetch"], ["-c", f"http.extraHeader=Authorization: Bearer {TOKEN}"], "git"),
            (["fetch"], (), f"/opt/{TOKEN}/git"),
        ):
            with pytest.raises(WorkspaceGitError) as caught:
                run_git(
                    argv,
                    cwd=tmp_path,
                    home_dir=empty_home,
                    path="/usr/bin",
                    options=options,
                    timeout_s=5,
                    launcher=launcher,
                    git_binary=binary,
                )
            assert caught.value.code == "bad_argument"
            assert TOKEN not in str(caught.value)
    assert launcher.calls == [], "nothing may spawn once a secret is in the invocation"


def test_run_git_refuses_an_extra_secret_the_caller_names(
    tmp_path: Path, empty_home: Path
) -> None:
    launcher = RecordingLauncher()
    with pytest.raises(WorkspaceGitError) as caught:
        run_git(
            ["fetch", "https://x:hunter2hunter2@github.com/o/r.git"],
            cwd=tmp_path,
            home_dir=empty_home,
            path="/usr/bin",
            timeout_s=5,
            launcher=launcher,
            extra_secrets=["hunter2hunter2"],
        )
    assert caught.value.code == "bad_argument"
    assert launcher.calls == []


def test_run_git_spawns_normally_when_no_secret_is_present(
    tmp_path: Path, empty_home: Path
) -> None:
    """The guard must not be one that always fires."""
    launcher = RecordingLauncher()
    with CredentialBroker("https", "github.com", "o/r", "x-access-token", TOKEN):
        result = run_git(
            ["clone", "https://github.com/o/r.git"],
            cwd=tmp_path,
            home_dir=empty_home,
            path="/usr/bin",
            timeout_s=5,
            launcher=launcher,
        )
    assert result.returncode == 0
    assert len(launcher.calls) == 1


def test_the_default_launcher_kills_the_process_group_on_a_timeout(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """subprocess.run's own timeout kills only the tracked pid."""
    killed: list[object] = []

    class HangingProc:
        pid = 4321
        returncode = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="git", timeout=timeout or 0)

    proc = HangingProc()
    monkeypatch.setattr(wg.subprocess, "Popen", lambda command, **kw: proc)
    monkeypatch.setattr(wg, "kill_git", lambda p, **kw: killed.append(p) or 0)
    with pytest.raises(subprocess.TimeoutExpired):
        wg._default_launcher(["git", "fetch"], timeout=1, check=False)
    assert killed == [proc], "the timeout path must go through kill_git"


#: What :func:`process_state` reports for a process that cannot still be
#: running: no entry, an unreaped corpse, or somebody else's process wearing a
#: recycled pid.
NOT_RUNNING = frozenset({"gone", "zombie", "recycled"})


def process_state(pid: int, *, marker: str = "") -> str:
    """``"gone"``, ``"zombie"``, ``"recycled"``, or the live state letter.

    ``os.path.isdir(f"/proc/{pid}")`` is NOT a liveness test, in two directions:

    * A killed process whose parent never reaps it stays as a ``Z`` entry.
      Whether anything reaps it is a property of the environment's init -- the
      CI runner does, a container whose PID 1 is pytest does not -- so an
      existence check passes on CI and fails in a container for a process that
      is equally dead in both (found by the Linux oracle, 2026-08-31).
    * A pid is recycled. An entry that exists may be an unrelated process that
      started after the kill, which makes the existence check pass FALSELY.
      The caller's ``marker`` is what separates those: a recycled pid has a
      different command line.

    The state letter is the first field after the LAST ``)``: ``comm`` is
    parenthesised and may itself contain spaces and a closing paren.
    """
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "gone"
    _before, paren, after = stat_text.rpartition(")")
    fields = after.split() if paren else []
    state = fields[0] if fields else ""
    if state == "Z":
        return "zombie"
    if marker:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return "gone"
        if marker.encode() not in cmdline:
            return "recycled"
    return state or "gone"


@pytest.mark.skipif(IS_WINDOWS, reason="/proc is Linux")
def test_the_liveness_helper_separates_alive_from_dead_unreaped_and_recycled() -> None:
    """The decoy, and the trap itself, on a process this test owns.

    A helper that called everything dead would pass every use of it, so it has
    to be shown seeing a LIVE process. And the zombie branch is the whole
    reason this exists: the ``/proc`` entry of a killed-but-unreaped child is
    still there, which is exactly what an existence check reads as "alive".
    """
    assert process_state(os.getpid()) not in NOT_RUNNING, "this very process is alive"
    assert process_state(0) == "gone", "pid 0 is never an entry"

    token = "tinyassets-probe-" + secrets.token_hex(6)
    child = subprocess.Popen(["/bin/sh", "-c", f"sleep 30 # {token}"])
    try:
        deadline = time.time() + 5
        while process_state(child.pid, marker=token) in NOT_RUNNING and time.time() < deadline:
            time.sleep(0.05)
        assert process_state(child.pid, marker=token) not in NOT_RUNNING, "the child is alive"
        # The same LIVE pid, asked about a different process: not it.
        assert process_state(child.pid, marker="not-in-this-cmdline") == "recycled"

        child.kill()
        deadline = time.time() + 5
        while process_state(child.pid, marker=token) != "zombie" and time.time() < deadline:
            time.sleep(0.02)
        # Nothing has reaped it yet -- this test is its parent and has not
        # waited. The entry is still on disk, and that is the trap.
        assert process_state(child.pid, marker=token) == "zombie"
        assert os.path.isdir(f"/proc/{child.pid}"), (
            "the /proc entry outlives the process; an existence check reads it as alive"
        )
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.skipif(IS_WINDOWS, reason="process groups are POSIX")
def test_a_timeout_reaps_a_double_forked_descendant(tmp_path: Path, empty_home: Path) -> None:
    """The whole point of the group kill: a helper git spawned must not survive.

    The child backgrounds a sleeper and then hangs; when run_git times out, the
    descendant must be dead. DEAD, not "missing from /proc": see
    :func:`process_state` for why those are different questions and why the
    difference is environment-dependent.

    The sleeper carries a unique token in its command line so that a pid
    recycled between the kill and the check reads as ``recycled`` rather than
    as a survivor.
    """
    token = "tinyassets-descendant-" + secrets.token_hex(6)
    marker = tmp_path / "pid"
    script = f"/bin/sh -c 'sleep 120 # {token}' & echo $! > {marker}; sleep 120"
    with pytest.raises(WorkspaceGitError) as caught:
        run_git(
            ["-c", script],
            cwd=tmp_path,
            home_dir=empty_home,
            path="/usr/bin:/bin",
            timeout_s=2,
            git_binary="/bin/sh",
        )
    assert caught.value.code == "timeout"
    descendant = int(marker.read_text().strip())
    deadline = time.time() + 5
    while process_state(descendant, marker=token) not in NOT_RUNNING and time.time() < deadline:
        time.sleep(0.05)
    state = process_state(descendant, marker=token)
    assert state in NOT_RUNNING, f"the descendant outlived the kill (state {state!r})"


def test_run_git_refuses_an_environment_that_is_not_the_canonical_set(
    tmp_path: Path, empty_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth, driven: the only way this fires is git_environment
    changing, which is exactly when a silent widening would happen."""
    launcher = RecordingLauncher()
    canonical = dict(git_environment(empty_home, path="/usr/bin"))

    for widened in (
        {**canonical, "GIT_DIR": "/etc"},
        {**canonical, "GIT_TRACE_CURL": "1"},
        {key: value for key, value in canonical.items() if key != "HOME"},
    ):
        monkeypatch.setattr(wg, "git_environment", lambda *a, **k: dict(widened))
        with pytest.raises(WorkspaceGitError) as caught:
            run_git(
                ["status"],
                cwd=tmp_path,
                home_dir=empty_home,
                path="/usr/bin",
                timeout_s=5,
                launcher=launcher,
            )
        assert caught.value.code == "bad_argument"
    assert launcher.calls == [], "a non-canonical environment must not spawn"


def test_run_git_uses_the_group_killing_launcher_when_none_is_injected(
    tmp_path: Path, empty_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not subprocess.run: its timeout kills only the tracked pid."""
    used: list[str] = []

    def spy(command, **kwargs):
        used.append("default")
        return FakeCompleted()

    monkeypatch.setattr(wg, "_default_launcher", spy)
    run_git(["status"], cwd=tmp_path, home_dir=empty_home, path="/usr/bin", timeout_s=5)
    assert used == ["default"]


def test_run_git_pins_the_destination_to_a_descriptor_when_given_one(
    tmp_path: Path, empty_home: Path, monkeypatch: pytest.MonkeyPatch, posix_mode: None
) -> None:
    """``dest_fd`` makes git's cwd the descriptor, not a path to re-resolve."""
    launcher = RecordingLauncher()
    monkeypatch.setattr(wg.os, "listdir", lambda target: [])
    monkeypatch.setattr(wg.Path, "is_dir", lambda self: True)
    bundle = tmp_path / "b.bundle"
    bundle.write_bytes(b"PACK")
    with pytest.raises(WorkspaceGitError):
        # it will fail later (the fake launcher returns no sha), but the FIRST
        # command must already be pinned to the descriptor
        unbundle_into_fresh_repo(
            bundle,
            tmp_path / "dest",
            ref_name="refs/tiny/export",
            home_dir=empty_home,
            path="/usr/bin",
            dest_fd=7,
            launcher=launcher,
        )
    first = launcher.calls[0][1]
    assert first["cwd"] == "/proc/self/fd/7"
    assert first["pass_fds"] == (7,), "the descriptor must survive into the child"


def test_a_descriptor_destination_is_refused_off_posix(
    tmp_path: Path, empty_home: Path, windows_mode: None
) -> None:
    """No /proc/self/fd there; a path-based imitation would be a lie."""
    bundle = tmp_path / "b.bundle"
    bundle.write_bytes(b"PACK")
    with pytest.raises(WorkspaceGitError) as caught:
        unbundle_into_fresh_repo(
            bundle,
            tmp_path / "dest",
            ref_name="refs/tiny/export",
            home_dir=empty_home,
            path="/usr/bin",
            dest_fd=7,
            launcher=RecordingLauncher(),
        )
    assert caught.value.code == "bad_argument"


@pytest.mark.parametrize("bad", ["7", True, 7.0, object()])
def test_a_destination_descriptor_must_be_a_descriptor(
    tmp_path: Path, empty_home: Path, bad
) -> None:
    bundle = tmp_path / "b.bundle"
    bundle.write_bytes(b"PACK")
    with pytest.raises(WorkspaceGitError) as caught:
        unbundle_into_fresh_repo(
            bundle,
            tmp_path / "dest",
            ref_name="refs/tiny/export",
            home_dir=empty_home,
            path="/usr/bin",
            dest_fd=bad,
            launcher=RecordingLauncher(),
        )
    assert caught.value.code == "bad_argument"


def test_run_git_bounds_captured_output(tmp_path: Path, empty_home: Path) -> None:
    noise = b"x" * (200 * 1024) + b"TAIL"
    launcher = RecordingLauncher(FakeCompleted(returncode=1, stdout=noise, stderr=noise))
    result = run_git(
        ["status"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        timeout_s=5,
        launcher=launcher,
    )
    assert len(result.stdout_tail) == 64 * 1024
    assert len(result.stderr_scrubbed) == 64 * 1024
    assert result.stdout_tail.endswith("TAIL")
    assert result.stderr_scrubbed.endswith("TAIL")


def test_run_git_scrubs_a_token_out_of_both_streams(tmp_path: Path, empty_home: Path) -> None:
    raw = f"fatal: unable to access 'https://x-access-token:{TOKEN}@github.com/o/r.git/'".encode()
    launcher = RecordingLauncher(FakeCompleted(returncode=128, stdout=raw, stderr=raw))
    with CredentialBroker("https", "github.com", "o/r", "x-access-token", TOKEN):
        result = run_git(
            ["fetch"],
            cwd=tmp_path,
            home_dir=empty_home,
            path="/usr/bin",
            timeout_s=5,
            launcher=launcher,
        )
    assert TOKEN not in result.stderr_scrubbed
    assert TOKEN not in result.stdout_tail
    assert "[redacted]" in result.stderr_scrubbed
    assert "[redacted]" in result.stdout_tail


def test_run_git_maps_a_timeout_to_its_own_code(tmp_path: Path, empty_home: Path) -> None:
    launcher = RecordingLauncher(raises=subprocess.TimeoutExpired(cmd="git", timeout=1))
    with pytest.raises(WorkspaceGitError) as caught:
        run_git(
            ["fetch"],
            cwd=tmp_path,
            home_dir=empty_home,
            path="/usr/bin",
            timeout_s=1,
            launcher=launcher,
        )
    assert caught.value.code == "timeout"


def test_run_git_maps_a_missing_binary_to_transport(tmp_path: Path, empty_home: Path) -> None:
    launcher = RecordingLauncher(raises=FileNotFoundError("no git"))
    with pytest.raises(WorkspaceGitError) as caught:
        run_git(
            ["fetch"],
            cwd=tmp_path,
            home_dir=empty_home,
            path="/usr/bin",
            timeout_s=5,
            launcher=launcher,
        )
    assert caught.value.code == "transport"


@pytest.mark.parametrize(
    "argv,cwd_exists,timeout",
    [([], True, 5), (["status"], False, 5), (["status"], True, 0)],
)
def test_run_git_refuses_malformed_input(
    tmp_path: Path, empty_home: Path, argv: list[str], cwd_exists: bool, timeout: float
) -> None:
    cwd = tmp_path if cwd_exists else tmp_path / "missing"
    with pytest.raises(WorkspaceGitError) as caught:
        run_git(
            argv,
            cwd=cwd,
            home_dir=empty_home,
            path="/usr/bin",
            timeout_s=timeout,
            launcher=RecordingLauncher(),
        )
    assert caught.value.code == "bad_argument"


# ---------------------------------------------------------------------------
# 8b. kill_git
# ---------------------------------------------------------------------------


class FakeProc:
    """A started process that never existed -- no real signal is ever sent."""

    def __init__(self, pid: int = 4321, *, exits: bool = True, status: int = -9):
        self.pid = pid
        self.killed = False
        self.waited: list[float | None] = []
        self._exits = exits
        self._status = status

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited.append(timeout)
        if self._exits:
            return self._status
        raise subprocess.TimeoutExpired(cmd="git", timeout=timeout or 0)


@pytest.fixture()
def signal_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Intercept the real signal calls. A stray killpg would hit a real group."""
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(wg.os, "getpgid", lambda pid: pid + 1000, raising=False)
    monkeypatch.setattr(
        wg.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)), raising=False
    )
    return sent


def test_kill_git_signals_the_whole_process_group(signal_spy, posix_mode: None) -> None:
    proc = FakeProc(pid=4321)
    assert kill_git(proc) == -9
    # the GROUP, not the pid: a double-forked helper must die too
    assert signal_spy == [(5321, wg._KILL_SIGNAL)]
    if not IS_WINDOWS:
        assert wg._KILL_SIGNAL is signal.SIGKILL


def test_kill_git_on_windows_can_only_reach_the_pid(signal_spy, windows_mode: None) -> None:
    proc = FakeProc(pid=4321)
    assert kill_git(proc) == -9
    assert signal_spy == []
    assert proc.killed is True


def test_kill_git_falls_back_to_the_pid_when_the_group_is_gone(
    monkeypatch: pytest.MonkeyPatch, posix_mode: None
) -> None:
    def gone(pgid: int, sig: int) -> None:
        raise ProcessLookupError("already reaped")

    monkeypatch.setattr(wg.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(wg.os, "killpg", gone, raising=False)
    proc = FakeProc()
    assert kill_git(proc) == -9
    assert proc.killed is True


def test_kill_git_waits_with_a_bounded_timeout(signal_spy) -> None:
    proc = FakeProc()
    kill_git(proc, timeout_s=2.5)
    assert proc.waited == [2.5]


def test_kill_git_raises_when_the_process_will_not_die(signal_spy) -> None:
    proc = FakeProc(exits=False)
    with pytest.raises(WorkspaceGitError) as caught:
        kill_git(proc, timeout_s=0.1)
    assert caught.value.code == "timeout"


def test_kill_git_refuses_something_that_is_not_a_process(signal_spy) -> None:
    for candidate in (None, object(), "git"):
        with pytest.raises(WorkspaceGitError) as caught:
            kill_git(candidate)
        assert caught.value.code == "bad_argument"


# ---------------------------------------------------------------------------
# 9. stderr classification
# ---------------------------------------------------------------------------


CLASS_FIXTURES: dict[str, str] = {
    "auth": (
        "remote: Invalid username or password.\n"
        "fatal: Authentication failed for 'https://github.com/owner/repo.git/'\n"
    ),
    "not_found": (
        "remote: Repository not found.\n"
        "fatal: repository 'https://github.com/owner/repo.git/' not found\n"
    ),
    "non_fast_forward": (
        " ! [rejected]        main -> main (non-fast-forward)\n"
        "error: failed to push some refs to 'https://github.com/owner/repo.git'\n"
    ),
    "protected": (
        "remote: error: GH006: Protected branch update failed for refs/heads/main.\n"
        "remote: error: Required status check \"ci\" is expected.\n"
    ),
    "transport": (
        "fatal: unable to access 'https://github.com/owner/repo.git/': "
        "Could not resolve host: github.com\n"
    ),
    "verification": (
        "error: object file .git/objects/ab/cdef is empty\n"
        "fatal: fsck error in packed object\n"
    ),
    "other": "fatal: your local changes to the following files would be overwritten\n",
}


@pytest.mark.parametrize("expected,fixture", sorted(CLASS_FIXTURES.items()))
def test_classify_stderr(expected: str, fixture: str) -> None:
    assert classify_stderr(fixture) == expected


def test_every_declared_class_has_a_fixture() -> None:
    assert set(CLASS_FIXTURES) == set(wg.STDERR_CLASSES)


def test_classify_stderr_is_total() -> None:
    for text in ("", "   ", "something entirely unexpected"):
        assert classify_stderr(text) in wg.STDERR_CLASSES


def test_git_result_carries_the_class(tmp_path: Path, empty_home: Path) -> None:
    launcher = RecordingLauncher(
        FakeCompleted(returncode=128, stderr=CLASS_FIXTURES["auth"].encode())
    )
    result = run_git(
        ["fetch"],
        cwd=tmp_path,
        home_dir=empty_home,
        path="/usr/bin",
        timeout_s=5,
        launcher=launcher,
    )
    assert isinstance(result, GitResult)
    assert result.stderr_class == "auth"
    assert result.ok is False


# ---------------------------------------------------------------------------
# 10. bundle helpers against a real git
# ---------------------------------------------------------------------------

GIT_BINARY = shutil.which("git")
needs_git = pytest.mark.skipif(GIT_BINARY is None, reason="git is not on PATH")


def _real_env(home: Path) -> dict[str, str]:
    env = git_environment(home, path=str(Path(GIT_BINARY).parent))
    if IS_WINDOWS:
        # git.exe needs these to start at all; neither carries git config.
        for key in ("SystemRoot", "COMSPEC"):
            value = os.environ.get(key)
            if value:
                env[key] = value
    return env


@pytest.fixture()
def real_git(tmp_path: Path):
    if GIT_BINARY is None:
        pytest.skip("git is not on PATH")
    home = tmp_path / "githome"
    home.mkdir()
    env = _real_env(home)

    def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
        # A direct subprocess call, NOT run_git: the fixture builds the source
        # repository, so it supplies the env dict itself.
        return subprocess.run(
            [GIT_BINARY, *argv],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )

    repo = tmp_path / "source"
    repo.mkdir()
    run(["init", "--quiet", "."], repo)
    (repo / "README.md").write_text("workspace\n", encoding="utf-8")
    run(["add", "README.md"], repo)
    run(
        ["-c", "user.name=Tiny", "-c", "user.email=tiny@universes.tinyassets.io",
         "commit", "--quiet", "-m", "first"],
        repo,
    )
    sha = run(["rev-parse", "HEAD"], repo).stdout.strip()
    return {
        "env": env,
        "home": home,
        "path": str(Path(GIT_BINARY).parent),
        "repo": repo,
        "sha": sha,
        "run": run,
    }


@needs_git
def test_create_bundle_refuses_a_sha_that_is_not_40_hex(tmp_path: Path, real_git) -> None:
    for bad in ("HEAD", "abc", real_git["sha"].upper(), real_git["sha"] + "0"):
        with pytest.raises(WorkspaceGitError) as caught:
            create_bundle(
                real_git["repo"], bad, tmp_path / "b.bundle",
                home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(
                    tmp_path,
                ),
            )
        assert caught.value.code == "bad_argument"


@needs_git
def test_create_bundle_refuses_to_overwrite(tmp_path: Path, real_git) -> None:
    existing = tmp_path / "b.bundle"
    existing.write_bytes(b"old")
    with pytest.raises(WorkspaceGitError) as caught:
        create_bundle(
            real_git["repo"], real_git["sha"], existing,
            home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(tmp_path),
        )
    assert caught.value.code == "bad_argument"
    assert existing.read_bytes() == b"old"


@needs_git
def test_create_bundle_deletes_its_temporary_ref(tmp_path: Path, real_git) -> None:
    create_bundle(
        real_git["repo"], real_git["sha"], tmp_path / "b.bundle",
        home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(tmp_path),
    )
    listed = subprocess.run(
        [GIT_BINARY, "for-each-ref", "--format=%(refname)"],
        cwd=str(real_git["repo"]),
        env=real_git["env"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "refs/tiny/export" not in listed.stdout


@needs_git
def test_verify_bundle_returns_the_refs_it_carries(tmp_path: Path, real_git) -> None:
    bundle = tmp_path / "b.bundle"
    create_bundle(
        real_git["repo"], real_git["sha"], bundle,
        home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(tmp_path),
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    refs = verify_bundle(
        bundle,
        max_bytes=10_000_000,
        scratch_dir=scratch,
        home_dir=real_git["home"],
        path=real_git["path"],
    )
    assert refs == ["refs/tiny/export"]


@needs_git
def test_verify_bundle_refuses_an_oversized_bundle(tmp_path: Path, real_git) -> None:
    bundle = tmp_path / "b.bundle"
    create_bundle(
        real_git["repo"], real_git["sha"], bundle,
        home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(tmp_path),
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(WorkspaceGitError) as caught:
        verify_bundle(
            bundle,
            max_bytes=16,
            scratch_dir=scratch,
            home_dir=real_git["home"],
            path=real_git["path"],
        )
    assert caught.value.code == "verification"


@needs_git
def test_verify_bundle_refuses_something_that_is_not_a_regular_file(
    tmp_path: Path, real_git
) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for candidate in (directory, tmp_path / "missing.bundle"):
        with pytest.raises(WorkspaceGitError) as caught:
            verify_bundle(
                candidate,
                max_bytes=1000,
                scratch_dir=scratch,
                home_dir=real_git["home"],
                path=real_git["path"],
            )
        assert caught.value.code == "verification"


@needs_git
def test_verify_bundle_rejects_a_file_that_is_not_a_bundle(tmp_path: Path, real_git) -> None:
    fake = tmp_path / "fake.bundle"
    fake.write_bytes(b"not a bundle at all")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(WorkspaceGitError) as caught:
        verify_bundle(
            fake,
            max_bytes=1000,
            scratch_dir=scratch,
            home_dir=real_git["home"],
            path=real_git["path"],
        )
    assert caught.value.code == "verification"


@needs_git
def test_unbundle_leaves_no_remote_and_no_host_path(tmp_path: Path, real_git) -> None:
    bundle = tmp_path / "b.bundle"
    create_bundle(
        real_git["repo"], real_git["sha"], bundle,
        home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(tmp_path),
    )
    dest = tmp_path / "workspace"
    sha = unbundle_into_fresh_repo(
        bundle, dest, ref_name="refs/tiny/export", home_dir=real_git["home"], path=real_git["path"]
    )
    assert sha == real_git["sha"]

    config_text = (dest / ".git" / "config").read_text(encoding="utf-8")
    assert "remote" not in config_text
    assert str(bundle) not in config_text
    assert bundle.resolve().as_posix() not in config_text
    assert str(real_git["repo"]) not in config_text

    remote = subprocess.run(
        [GIT_BINARY, "config", "--get", "remote.origin.url"],
        cwd=str(dest),
        env=real_git["env"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert remote.stdout.strip() == ""
    assert remote.returncode != 0


@needs_git
def test_unbundle_refuses_a_non_empty_destination(tmp_path: Path, real_git) -> None:
    bundle = tmp_path / "b.bundle"
    create_bundle(
        real_git["repo"], real_git["sha"], bundle,
        home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(tmp_path),
    )
    dest = tmp_path / "workspace"
    dest.mkdir()
    (dest / "squatter").write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceGitError) as caught:
        unbundle_into_fresh_repo(
            bundle,
            dest,
            ref_name="refs/tiny/export",
            home_dir=real_git["home"],
            path=real_git["path"],
        )
    assert caught.value.code == "bad_argument"


@needs_git
def test_unbundle_refuses_a_missing_bundle(tmp_path: Path, real_git) -> None:
    with pytest.raises(WorkspaceGitError) as caught:
        unbundle_into_fresh_repo(
            tmp_path / "missing.bundle",
            tmp_path / "workspace",
            ref_name="refs/tiny/export",
            home_dir=real_git["home"],
            path=real_git["path"],
        )
    assert caught.value.code == "verification"


@needs_git
def test_populate_workspace_checks_the_commit_out_on_a_local_branch(
    tmp_path: Path, real_git
) -> None:
    bundle = tmp_path / "b.bundle"
    create_bundle(
        real_git["repo"], real_git["sha"], bundle,
        home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(tmp_path),
    )
    dest = tmp_path / "workspace"
    sha = populate_workspace_from_bundle(
        bundle,
        dest,
        "refs/tiny/export",
        "tiny/u-abc/slug",
        home_dir=real_git["home"],
        path=real_git["path"],
    )
    assert sha == real_git["sha"]
    assert (dest / "README.md").read_text(encoding="utf-8") == "workspace\n"
    branch = subprocess.run(
        [GIT_BINARY, "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(dest),
        env=real_git["env"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert branch.stdout.strip() == "tiny/u-abc/slug"


# ---------------------------------------------------------------------------
# 10b. bundles must be prerequisite-free
# ---------------------------------------------------------------------------

DEEP_HISTORY_COMMITS = 60  # > 50, so the bundle cannot be mistaken for shallow


@pytest.fixture(scope="module")
def deep_repo(tmp_path_factory) -> dict:
    """A repository with more than 50 commits, built once for this module."""
    if GIT_BINARY is None:
        pytest.skip("git is not on PATH")
    root = tmp_path_factory.mktemp("deep")
    home = root / "githome"
    home.mkdir()
    env = _real_env(home)
    repo = root / "source"
    repo.mkdir()

    def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
        # A direct subprocess call, NOT run_git: the fixture is building the
        # source repository, so it passes the env dict itself.
        return subprocess.run(
            [GIT_BINARY, *argv],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )

    run(["init", "--quiet", "."], repo)
    (repo / "README.md").write_text("deep\n", encoding="utf-8")
    run(["add", "README.md"], repo)
    identity = ["-c", "user.name=Tiny", "-c", "user.email=tiny@universes.tinyassets.io"]
    run([*identity, "commit", "--quiet", "-m", "first"], repo)
    for index in range(DEEP_HISTORY_COMMITS - 1):
        run([*identity, "commit", "--quiet", "--allow-empty", "-m", f"c{index}"], repo)
    sha = run(["rev-parse", "HEAD"], repo).stdout.strip()
    parent = run(["rev-parse", "HEAD~1"], repo).stdout.strip()
    count = run(["rev-list", "--count", "HEAD"], repo).stdout.strip()
    assert count == str(DEEP_HISTORY_COMMITS), count
    return {
        "env": env,
        "home": home,
        "path": str(Path(GIT_BINARY).parent),
        "repo": repo,
        "sha": sha,
        "parent": parent,
        "run": run,
        "root": root,
    }


@needs_git
def test_a_bundle_of_a_deep_history_carries_all_of_it(tmp_path: Path, deep_repo) -> None:
    """>50 commits: the bundle must be complete, not a shallow-looking slice."""
    bundle = tmp_path / "deep.bundle"
    create_bundle(
        deep_repo["repo"], deep_repo["sha"], bundle,
        home_dir=deep_repo["home"], path=deep_repo["path"], scratch_dir=_fresh_scratch(tmp_path),
    )
    refs = verify_bundle(
        bundle,
        max_bytes=50_000_000,
        scratch_dir=_fresh_scratch(tmp_path),
        home_dir=deep_repo["home"],
        path=deep_repo["path"],
    )
    assert refs == ["refs/tiny/export"]

    dest = tmp_path / "workspace"
    sha = populate_workspace_from_bundle(
        bundle,
        dest,
        "refs/tiny/export",
        "tiny/u/deep",
        home_dir=deep_repo["home"],
        path=deep_repo["path"],
    )
    assert sha == deep_repo["sha"]
    counted = subprocess.run(
        [GIT_BINARY, "rev-list", "--count", "HEAD"],
        cwd=str(dest), env=deep_repo["env"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60,
    )
    assert counted.stdout.strip() == str(DEEP_HISTORY_COMMITS)
    assert not (dest / ".git" / "shallow").exists()


@needs_git
def test_verify_bundle_refuses_a_bundle_that_needs_objects_it_lacks(
    tmp_path: Path, deep_repo
) -> None:
    """A real thin bundle: a `HEAD~1..HEAD` range records a prerequisite.

    The range has to name a ref -- a raw `<sha>..<sha>` range packages no ref
    and git refuses to create an empty bundle at all.
    """
    thin = tmp_path / "thin.bundle"
    deep_repo["run"](["bundle", "create", str(thin), "HEAD~1..HEAD"], deep_repo["repo"])
    assert thin.is_file()
    with pytest.raises(WorkspaceGitError) as caught:
        verify_bundle(
            thin, max_bytes=50_000_000, scratch_dir=_fresh_scratch(tmp_path),
            home_dir=deep_repo["home"],
            path=deep_repo["path"],
        )
    assert caught.value.code == "verification"
    assert "prerequisite" in str(caught.value)


@needs_git
def test_a_bundle_from_a_shallow_clone_is_refused(tmp_path: Path, deep_repo) -> None:
    """The real shape D1 forbids: a shallow source cannot cross the bridge.

    A bundle cannot represent a shallow boundary, so a clone made with
    ``--depth 1`` must not be able to produce one this layer accepts -- either
    git refuses to bundle it, or the bundle it makes fails verification in an
    empty repository. Both are the same answer; neither may silently pass.
    """
    shallow = tmp_path / "shallow"
    clone = subprocess.run(
        [
            GIT_BINARY, "clone", "--depth", "1", "--no-local",
            deep_repo["repo"].as_uri(), str(shallow),
        ],
        env=deep_repo["env"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if clone.returncode != 0:
        pytest.skip(f"this git will not make a shallow local clone: {clone.stderr[:120]}")
    assert (shallow / ".git" / "shallow").exists(), "the clone is not actually shallow"

    sha = subprocess.run(
        [GIT_BINARY, "rev-parse", "HEAD"],
        cwd=str(shallow), env=deep_repo["env"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()

    bundle = tmp_path / "shallow.bundle"
    try:
        create_bundle(
            shallow, sha, bundle,
            home_dir=deep_repo["home"],
            path=deep_repo["path"],
            scratch_dir=_fresh_scratch(tmp_path),
        )
    except WorkspaceGitError as caught:
        assert caught.code == "verification", caught
        assert not bundle.exists(), "a refused bundle must not be left behind"
        return

    # MEASURED (git 2.53): git happily bundles a shallow clone, and
    # `bundle verify` in an empty repo ACCEPTS it -- it reports the ref and
    # says nothing about the parent it cannot reach. So verification alone is
    # NOT proof of self-containedness for a shallow source.
    refs = verify_bundle(
        bundle,
        max_bytes=50_000_000,
        scratch_dir=_fresh_scratch(tmp_path),
        home_dir=deep_repo["home"],
        path=deep_repo["path"],
    )
    assert refs == ["refs/tiny/export"]

    # The fsck-checked IMPORT is what catches it, which is exactly why the push
    # path runs both and never treats `bundle verify` as the whole answer.
    with pytest.raises(WorkspaceGitError) as caught:
        unbundle_into_fresh_repo(
            bundle,
            tmp_path / "from-shallow",
            ref_name="refs/tiny/export",
            home_dir=deep_repo["home"],
            path=deep_repo["path"],
        )
    assert caught.value.code == "verification"
    assert "necessary objects" in str(caught.value) or "traverse" in str(caught.value)


class PrerequisiteReportingLauncher:
    """A git whose `bundle verify` reports prerequisites, and whose `bundle
    create` writes a file -- the shape create_bundle's self-check exists for."""

    def __init__(self, bundle_path: Path):
        self.bundle_path = bundle_path

    def _subcommand(self, command: list[str]) -> tuple[str, str]:
        rest = command[1:]
        while rest and (rest[0] == "-c" or rest[0] == "--no-replace-objects"):
            rest = rest[2:] if rest[0] == "-c" else rest[1:]
        return (rest[0] if rest else "", rest[1] if len(rest) > 1 else "")

    def __call__(self, command, **kwargs):
        head, tail = self._subcommand(list(command))
        if head == "bundle" and tail == "create":
            self.bundle_path.write_bytes(b"a bundle with prerequisites")
            return FakeCompleted()
        if head == "bundle" and tail == "verify":
            return FakeCompleted(
                returncode=1,
                stderr=b"error: Repository lacks these prerequisite commits:\n"
                b"error: dfa69fe97186221151839f99c0b42b5b2e6c8219 \n",
            )
        return FakeCompleted()


def test_create_bundle_refuses_and_deletes_its_own_thin_output(
    tmp_path: Path, empty_home: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    bundle = tmp_path / "out.bundle"
    with pytest.raises(WorkspaceGitError) as caught:
        create_bundle(
            repo, "0" * 40, bundle,
            home_dir=empty_home, path="/usr/bin",
            scratch_dir=_fresh_scratch(tmp_path),
            launcher=PrerequisiteReportingLauncher(bundle),
        )
    assert caught.value.code == "verification"
    assert "prerequisite-free" in str(caught.value)
    # an unverifiable bundle must not be left for a caller to pick up
    assert not bundle.exists()


@needs_git
def test_create_bundle_verifies_a_good_bundle_without_complaint(
    tmp_path: Path, real_git
) -> None:
    """The self-check must not be a guard that always fires."""
    bundle = tmp_path / "b.bundle"
    returned = create_bundle(
        real_git["repo"], real_git["sha"], bundle,
        home_dir=real_git["home"], path=real_git["path"], scratch_dir=_fresh_scratch(tmp_path),
    )
    assert returned == bundle
    assert bundle.is_file()


@needs_git
def test_create_bundle_needs_an_empty_scratch_dir(tmp_path: Path, real_git) -> None:
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "squatter").write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceGitError) as caught:
        create_bundle(
            real_git["repo"], real_git["sha"], tmp_path / "b.bundle",
            home_dir=real_git["home"], path=real_git["path"], scratch_dir=dirty,
        )
    assert caught.value.code == "bad_argument"


# The two post-population guards below never fire on a healthy git, so a real
# round trip cannot prove they work -- deleting them leaves the happy-path test
# green. A scripted launcher puts git in the state the guard exists for.


class ScriptedGitLauncher:
    """A git that reports whatever the test needs it to report."""

    def __init__(self, dest: Path, *, remote_url: str = "", config_text: str = ""):
        self.dest = dest
        self.remote_url = remote_url
        self.config_text = config_text

    def _subcommand(self, command: list[str]) -> str:
        rest = command[1:]
        while rest and rest[0] == "-c":
            rest = rest[2:]
        return rest[0] if rest else ""

    def __call__(self, command, **kwargs):
        subcommand = self._subcommand(list(command))
        if subcommand == "init":
            git_dir = self.dest / ".git"
            git_dir.mkdir(parents=True, exist_ok=True)
            (git_dir / "config").write_text(self.config_text, encoding="utf-8")
            return FakeCompleted()
        if subcommand == "rev-parse":
            return FakeCompleted(stdout=b"0" * 40 + b"\n")
        if subcommand == "config":
            return FakeCompleted(
                returncode=0 if self.remote_url else 1,
                stdout=self.remote_url.encode(),
            )
        return FakeCompleted()


def test_unbundle_refuses_a_populated_repo_that_carries_a_remote(
    tmp_path: Path, empty_home: Path
) -> None:
    bundle = tmp_path / "b.bundle"
    bundle.write_bytes(b"pretend bundle")
    dest = tmp_path / "workspace"
    launcher = ScriptedGitLauncher(dest, remote_url="https://github.com/owner/repo.git")
    with pytest.raises(WorkspaceGitError) as caught:
        unbundle_into_fresh_repo(
            bundle,
            dest,
            ref_name="refs/tiny/export",
            home_dir=empty_home, path="/usr/bin",
            launcher=launcher,
        )
    assert caught.value.code == "verification"
    assert "remote" in str(caught.value)


def test_unbundle_refuses_a_populated_repo_that_records_the_bundle_path(
    tmp_path: Path, empty_home: Path
) -> None:
    bundle = tmp_path / "b.bundle"
    bundle.write_bytes(b"pretend bundle")
    dest = tmp_path / "workspace"
    launcher = ScriptedGitLauncher(
        dest, config_text=f"[core]\n\tbundleuri = {bundle}\n"
    )
    with pytest.raises(WorkspaceGitError) as caught:
        unbundle_into_fresh_repo(
            bundle,
            dest,
            ref_name="refs/tiny/export",
            home_dir=empty_home, path="/usr/bin",
            launcher=launcher,
        )
    assert caught.value.code == "verification"
    assert "host path" in str(caught.value)


def test_unbundle_accepts_a_clean_population(tmp_path: Path, empty_home: Path) -> None:
    """The guards above must not fire on a repo that is actually clean."""
    bundle = tmp_path / "b.bundle"
    bundle.write_bytes(b"pretend bundle")
    dest = tmp_path / "workspace"
    launcher = ScriptedGitLauncher(dest, config_text="[core]\n\tbare = false\n")
    sha = unbundle_into_fresh_repo(
        bundle,
        dest,
        ref_name="refs/tiny/export",
        home_dir=empty_home, path="/usr/bin",
        launcher=launcher,
    )
    assert sha == "0" * 40


@pytest.mark.parametrize(
    "ref",
    ["", "-evil", "refs/../evil", "refs/tiny/export.lock", "refs/tiny//export", "refs/tiny/"],
)
def test_ref_names_are_validated(tmp_path: Path, ref: str) -> None:
    with pytest.raises(WorkspaceGitError) as caught:
        wg._require_ref(ref, "test ref")
    assert caught.value.code == "bad_argument"


def test_bundle_helpers_never_touch_the_real_subprocess_by_default() -> None:
    """The launcher is injectable on every helper -- nothing hardwires run()."""
    import inspect

    for function in (create_bundle, verify_bundle, unbundle_into_fresh_repo,
                     populate_workspace_from_bundle, run_git):
        parameters = inspect.signature(function).parameters
        assert "launcher" in parameters
        # None means run_git's own launcher, which kills the process GROUP
        # on a timeout rather than only the tracked pid.
        assert parameters["launcher"].default is None
        assert "home_dir" in parameters and "path" in parameters
        assert "env" not in parameters, "a caller must not be able to hand in an env"


def test_python_is_recent_enough_for_the_typing_used() -> None:
    assert sys.version_info >= (3, 11)


def test_populate_forwards_the_descriptor_to_both_of_its_git_calls(monkeypatch):
    """The wrapper takes ``dest_fd`` and BOTH calls under it must use it.

    The adapter passed ``dest_fd`` here while only ``unbundle_into_fresh_repo``
    accepted it, so every real checkout died with a TypeError the adapter
    reported as ``workspace_checkout_failed`` - and neither suite saw it,
    because the adapter's stubs this function and this one never passed the
    argument (found by the end-to-end chain test, 2026-08-30).

    Forwarding only the fetch would be just as wrong: pinning it to a handle
    and then re-resolving the same path for the checkout leaves exactly the
    window the handle exists to close.
    """
    import tinyassets.workspace_git as wg

    seen: dict[str, object] = {}

    def fake_unbundle(bundle_path, dest_dir, **kwargs):
        seen["unbundle_dest_fd"] = kwargs.get("dest_fd")
        return "a" * 40

    def fake_run_git(argv, **kwargs):
        seen["checkout_argv"] = list(argv)
        seen["checkout_cwd"] = str(kwargs.get("cwd"))
        seen["checkout_pass_fds"] = kwargs.get("pass_fds")
        return wg.GitResult(
            returncode=0, stdout_tail="", stderr_class="", stderr_scrubbed=""
        )

    monkeypatch.setattr(wg, "unbundle_into_fresh_repo", fake_unbundle)
    monkeypatch.setattr(wg, "run_git", fake_run_git)

    sha = wg.populate_workspace_from_bundle(
        "/tmp/x.bundle",
        "/tmp/dest",
        "refs/heads/main",
        "tiny/u/checkout",
        home_dir="/tmp/home",
        path="/usr/bin",
        dest_fd=7,
    )

    assert sha == "a" * 40
    assert seen["unbundle_dest_fd"] == 7
    assert seen["checkout_cwd"] == "/proc/self/fd/7"
    assert seen["checkout_pass_fds"] == (7,)


def test_populate_without_a_descriptor_still_works_by_path(monkeypatch):
    """The path form is what Windows and the tests use; it must not start
    handing git a /proc name that does not exist there."""
    import tinyassets.workspace_git as wg

    seen: dict[str, object] = {}

    def fake_unbundle(bundle_path, dest_dir, **kwargs):
        seen["unbundle_dest_fd"] = kwargs.get("dest_fd")
        return "b" * 40

    def fake_run_git(argv, **kwargs):
        seen["checkout_cwd"] = str(kwargs.get("cwd"))
        seen["checkout_pass_fds"] = kwargs.get("pass_fds")
        return wg.GitResult(
            returncode=0, stdout_tail="", stderr_class="", stderr_scrubbed=""
        )

    monkeypatch.setattr(wg, "unbundle_into_fresh_repo", fake_unbundle)
    monkeypatch.setattr(wg, "run_git", fake_run_git)

    wg.populate_workspace_from_bundle(
        "/tmp/x.bundle",
        "/tmp/dest",
        "refs/heads/main",
        "tiny/u/checkout",
        home_dir="/tmp/home",
        path="/usr/bin",
    )

    assert seen["unbundle_dest_fd"] is None
    assert seen["checkout_cwd"] == "/tmp/dest"
    assert seen["checkout_pass_fds"] == ()



# ---------------------------------------------------------------------------
# libcurl version text: the library first, the binary second, never a default
# ---------------------------------------------------------------------------


class _FakeLib:
    def __init__(self, text: bytes | None):
        self._text = text

    @property
    def curl_version(self):
        if self._text is None:
            raise AttributeError("curl_version")

        class _Fn:
            restype = None

            def __call__(_self):
                return self._text

        return _Fn()


def test_libcurl_version_text_reads_the_library_when_there_is_no_curl_binary():
    from tinyassets.workspace_git import libcurl_version_text

    loaded: list[str] = []

    def _load(name):
        loaded.append(name)
        if name != "libcurl-gnutls.so.4":
            raise OSError("not this one")
        return _FakeLib(b"libcurl/8.5.0 GnuTLS/3.8.3 zlib/1.3")

    text = libcurl_version_text(
        find_library=lambda _n: "libcurl.so.4", load_library=_load, which=lambda _n: None,
    )
    assert text.startswith("libcurl/8.5.0")
    assert loaded[0] == "libcurl-gnutls.so.4", "git's libcurl (gnutls) is tried first"


def test_libcurl_version_text_falls_back_to_the_binary_when_no_library_loads():
    from tinyassets.workspace_git import libcurl_version_text

    def _load(_name):
        raise OSError("no such library")

    class _Probe:
        stdout = b"curl 8.5.0 (x86_64-pc-linux-gnu) libcurl/8.5.0 OpenSSL/3.0.13"

    text = libcurl_version_text(
        find_library=lambda _n: None, load_library=_load,
        which=lambda _n: "/usr/bin/curl", run=lambda argv, **kw: _Probe(),
    )
    assert "libcurl/8.5.0" in text


def test_libcurl_version_text_fails_loud_with_neither():
    from tinyassets.workspace_git import WorkspaceGitError, libcurl_version_text

    def _load(_name):
        raise OSError("no such library")

    with pytest.raises(WorkspaceGitError) as excinfo:
        libcurl_version_text(
            find_library=lambda _n: None, load_library=_load, which=lambda _n: None,
        )
    assert excinfo.value.code == "bad_argument"
    assert "neither" in str(excinfo.value)


# ---------------------------------------------------------------------------
# the daemon-side hand-off: nothing spawns here
# ---------------------------------------------------------------------------


def test_run_git_hands_the_request_to_the_owner_cell(
    tmp_path: Path, empty_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The daemon validates, then the owner's git cell runs it."""
    from types import SimpleNamespace

    from tinyassets import role_git

    seen: dict[str, object] = {}

    def cell(argv, *, cwd, options, timeout_s):
        seen.update(argv=list(argv), cwd=str(cwd), options=tuple(options),
                    timeout_s=timeout_s)
        return SimpleNamespace(returncode=0, stdout="on branch main\n", stderr="")

    monkeypatch.setattr(role_git, "run", cell)
    monkeypatch.setattr(wg.subprocess, "Popen", lambda *a, **k: pytest.fail(
        "the daemon spawned git instead of handing it to the cell"))
    result = daemon_run_git(
        ["status", "--short"], cwd=tmp_path, home_dir=empty_home,
        path="/usr/bin", options=("-c", "core.hooksPath="), timeout_s=12,
    )
    assert seen == {"argv": ["status", "--short"], "cwd": str(tmp_path),
                    "options": ("-c", "core.hooksPath="), "timeout_s": 12.0}
    assert result.returncode == 0 and "on branch main" in result.stdout_tail


@pytest.mark.parametrize(
    ("extra", "detail"),
    [
        ({"launcher": lambda *a, **k: None}, "an injected launcher"),
        ({"git_binary": "/opt/git/bin/git"}, "another git binary"),
        ({"pass_fds": (3,)}, "an inherited descriptor"),
        ({"preexec_fn": lambda: None}, "its own child setup"),
    ],
)
def test_run_git_refuses_every_daemon_uid_spelling(
    tmp_path: Path, empty_home: Path, extra: dict, detail: str
) -> None:
    """Each of these asks for a git the daemon runs itself. That mode is gone."""
    with pytest.raises(WorkspaceGitError) as caught:
        daemon_run_git(["status"], cwd=tmp_path, home_dir=empty_home,
                   path="/usr/bin", timeout_s=5, **extra)
    assert caught.value.code == "bad_argument", detail
    assert "not admitted" in str(caught.value), detail


def test_run_git_validates_before_it_reaches_the_cell(
    tmp_path: Path, empty_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret in the invocation never leaves the daemon."""
    from tinyassets import role_git

    monkeypatch.setattr(role_git, "run", lambda *a, **k: pytest.fail(
        "a credentialed invocation reached the cell"))
    with pytest.raises(WorkspaceGitError, match="credential appears"):
        daemon_run_git(["push", f"https://{TOKEN}@example.com/r.git"], cwd=tmp_path,
                   home_dir=empty_home, path="/usr/bin", timeout_s=5,
                   extra_secrets=(TOKEN,))

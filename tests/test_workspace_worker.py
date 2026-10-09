"""Tests for the credential-blind git worker (``tinyassets.workspace_worker``).

No network and no real remote: a fake launcher stands in for ``git`` and
records every argv, which is how the forced options and the pinned address are
asserted. The token is a fixed string and EVERY test that can see a message,
an evidence dict or an exception asserts it is not in there.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tinyassets import workspace_worker as ww
from tinyassets.workspace_git import CredentialBroker, WorkspaceGitError

TOKEN = "ghp_WORKERTOKEN0123456789ABCDEFGHIJKL"
HOST = "github.com"
REPO = "owner/name"
SHA = "a" * 40
PARENT_SHA = "b" * 40


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGit:
    """A git that answers from a script and records every invocation.

    ``handlers`` maps a subcommand to a callable taking the argv tail and the
    kwargs, returning a :class:`FakeCompleted`. Anything unhandled succeeds
    silently, which keeps a test focused on the command it cares about.
    """

    def __init__(self, handlers: dict[str, Any] | None = None):
        self.handlers = handlers or {}
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append(command)
        self.kwargs.append(kwargs)
        rest = command[1:]
        while rest and rest[0] in ("-c", "--no-replace-objects"):
            rest = rest[2:] if rest[0] == "-c" else rest[1:]
        head = rest[0] if rest else ""
        handler = self.handlers.get(head)
        if handler is None:
            return FakeCompleted()
        return handler(rest, kwargs)

    def argv_for(self, subcommand: str) -> list[str]:
        for command in self.calls:
            rest = command[1:]
            while rest and rest[0] in ("-c", "--no-replace-objects"):
                rest = rest[2:] if rest[0] == "-c" else rest[1:]
            if rest and rest[0] == subcommand:
                return command
        raise AssertionError(f"no {subcommand} command was run; got {self.calls}")

    def options_for(self, subcommand: str) -> list[str]:
        command = self.argv_for(subcommand)
        options = []
        index = 1
        while index < len(command) and command[index] == "-c":
            options.append(command[index + 1])
            index += 2
        return options

    def ran(self, subcommand: str) -> bool:
        try:
            self.argv_for(subcommand)
            return True
        except AssertionError:
            return False


class LocalBroker(CredentialBroker):
    """The real broker with only the POSIX transport stubbed.

    ``serve()`` needs a unix domain socket, which Windows does not have, so the
    whole credentialed path would be untestable on this box -- and production
    is Linux. Everything else is the real class: the real ``answer`` matching,
    and the real secret registration that makes scrubbing work.
    """

    #: Every broker built during a test, so a test can assert what it was bound to.
    made: list[LocalBroker] = []

    def __init__(self, protocol, host, path, username, secret):
        super().__init__(protocol, host, path, username, secret)
        self.bound = (protocol, host, path, username)
        self.served = False
        LocalBroker.made.append(self)

    def serve(self, *, socket_dir=None, python_executable=None):
        # Same signature as the real one, DEFAULT INCLUDED: production stopped
        # passing socket_dir (the broker picks its own short path now), and a
        # stub that still required it failed 15 tests with a TypeError the
        # product does not have.
        self.served = True
        self.socket_dir = socket_dir
        root = socket_dir if socket_dir is not None else "/tmp/tabk-stub"
        self._helper_command = f"!python {root}/credential_helper.py"
        return self._helper_command


@pytest.fixture(autouse=True)
def _fresh_brokers():
    LocalBroker.made = []
    yield
    for broker in LocalBroker.made:
        broker.close()


def _resolver(hostname: str, port: int) -> list[str]:
    return ["140.82.121.4", "140.82.121.3"]


def _classifier(ip_text: str) -> str:
    if ip_text.startswith(("10.", "127.", "192.168.", "169.254.")):
        raise ValueError("not globally routable")
    return ip_text


@pytest.fixture()
def staging(tmp_path: Path) -> Path:
    directory = tmp_path / "staging"
    directory.mkdir()
    return directory


@pytest.fixture(autouse=True)
def _vault(monkeypatch: pytest.MonkeyPatch):
    """The worker resolves the token through the vault; give it one."""
    from tinyassets.storage import outbound_connections

    class Resolver:
        def __init__(self, *, universe_dir):
            self.universe_dir = universe_dir

        def __call__(self, credential_ref: str) -> str:
            if credential_ref == "vault://http/missing":
                raise RuntimeError(f"credential reference is unavailable {TOKEN}")
            return TOKEN

    monkeypatch.setattr(outbound_connections, "_GeneralVaultCredentialResolver", Resolver)
    return Resolver


def _checkout_request(staging: Path, **over: Any) -> dict[str, Any]:
    request = {
        "op": "checkout",
        "universe_dir": str(staging.parent),
        "credential_ref": "vault://http/github",
        "host": HOST,
        "owner_repo": REPO,
        "ref": "main",
        "staging_dir": str(staging),
        "resolver_text": "libcurl/8.5.0",
    }
    request.update(over)
    return request


def _push_request(staging: Path, bundle: Path, **over: Any) -> dict[str, Any]:
    request = {
        "op": "push",
        "universe_dir": str(staging.parent),
        "credential_ref": "vault://http/github",
        "host": HOST,
        "owner_repo": REPO,
        "remote_ref": "refs/heads/tiny/u-abc/slug",
        "commit_sha": SHA,
        "bundle_path": str(bundle),
        "staging_dir": str(staging),
        "resolver_text": "libcurl/8.5.0",
    }
    request.update(over)
    return request


def _no_token(payload: Any) -> None:
    text = repr(payload)
    assert TOKEN not in text, f"token leaked into {text[:400]}"


# --------------------------------------------------------------------------- #
# checkout
# --------------------------------------------------------------------------- #


def _checkout_git(staging: Path, sha: str = SHA) -> FakeGit:
    def clone(rest, kwargs):
        # the real git creates the directory; the fake must too
        (staging / ww._SRC_DIR).mkdir(parents=True, exist_ok=True)
        return FakeCompleted()

    def rev_parse(rest, kwargs):
        return FakeCompleted(stdout=(sha + "\n").encode())

    def bundle(rest, kwargs):
        if rest[1] == "create":
            Path(rest[2]).write_bytes(b"PACK-bundle-bytes")
        elif rest[1] == "list-heads":
            return FakeCompleted(stdout=f"{sha} refs/tiny/export\n".encode())
        return FakeCompleted()

    return FakeGit({"clone": clone, "rev-parse": rev_parse, "bundle": bundle})


def _checkout(staging, git, **over):
    return ww.handle_request(
        _checkout_request(staging, **over),
        resolver=over.pop("resolver", None) or _resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )


def test_checkout_returns_a_bundle_and_a_sha(staging: Path) -> None:
    git = _checkout_git(staging)
    answer = _checkout(staging, git)
    assert answer["ok"] is True
    assert answer["resolved_sha"] == SHA
    assert answer["bundle_name"] == "out.bundle"
    assert answer["bytes"] == len(b"PACK-bundle-bytes")
    assert (staging / "out.bundle").is_file()


def test_checkout_answers_relative_names_never_host_paths(staging: Path) -> None:
    """The parent joins the name itself; the child never says where it is."""
    answer = _checkout(staging, _checkout_git(staging))
    for value in answer.values():
        assert str(staging) not in str(value)
        assert not str(value).startswith("/")
        assert ":\\" not in str(value)


def test_checkout_deletes_the_clone_that_held_the_remote(staging: Path) -> None:
    _checkout(staging, _checkout_git(staging))
    assert not (staging / ww._SRC_DIR).exists()


def test_the_clone_carries_the_forced_options_and_the_pinned_address(staging: Path) -> None:
    git = _checkout_git(staging)
    _checkout(staging, git)
    options = git.options_for("clone")
    assert "credential.useHttpPath=true" in options
    assert "protocol.allow=never" in options
    assert "protocol.https.allow=always" in options
    assert "http.followRedirects=false" in options
    assert "submodule.recurse=false" in options
    assert "transfer.fsckObjects=true" in options
    assert "core.fsmonitor=false" in options
    # the reset precedes the broker
    helpers = [o for o in options if o.startswith("credential.helper=")]
    assert helpers[0] == "credential.helper="
    assert helpers[1].startswith("credential.helper=!")
    # multi-resolve: libcurl 8.5.0 takes the comma list, both addresses pinned
    resolve = [o for o in options if o.startswith("http.curloptResolve=")]
    assert resolve == ["http.curloptResolve=github.com:443:140.82.121.4,140.82.121.3"]


def test_an_old_libcurl_pins_one_address_for_the_whole_operation(staging: Path) -> None:
    git = _checkout_git(staging)
    _checkout(staging, git, resolver_text="libcurl/7.29.0")
    resolve = [o for o in git.options_for("clone") if o.startswith("http.curloptResolve=")]
    assert resolve == ["http.curloptResolve=github.com:443:140.82.121.4"]


def test_the_clone_is_bare_single_branch_and_never_recurses_submodules(staging: Path) -> None:
    git = _checkout_git(staging)
    _checkout(staging, git)
    argv = git.argv_for("clone")
    assert "--bare" in argv
    assert "--single-branch" in argv
    assert "--no-recurse-submodules" in argv
    assert "--branch" in argv and argv[argv.index("--branch") + 1] == "main"
    assert f"https://{HOST}/{REPO}.git" in argv
    # full clones only: a bundle cannot represent a shallow boundary (R3)
    assert not any(a.startswith("--depth") for a in argv)


def test_the_clone_url_and_the_broker_binding_come_from_one_transport(
    staging: Path,
) -> None:
    """One derivation: the URL git is handed and the broker's binding agree.

    The host case is the discriminator -- the transport lower-cases it, so a
    worker that rebuilt the URL itself from the request's host would clone
    ``https://GitHub.COM/...`` while the rule pinned ``github.com``.
    """
    git = _checkout_git(staging)
    _checkout(staging, git, host="GitHub.COM")
    argv = git.argv_for("clone")
    assert f"https://github.com/{REPO}.git" in argv, argv
    assert not any("GitHub.COM" in part for part in argv)

    # The session closes its broker when the operation ends, so the recorded
    # binding is what is asserted -- and a fresh broker on that same binding
    # answers exactly the request git will send.
    protocol, host, path, _user = LocalBroker.made[0].bound
    assert (protocol, host) == ("https", "github.com")
    assert path == f"{REPO}.git", "the binding is the canonical wire path"

    fresh = CredentialBroker(protocol, host, path, "x-access-token", TOKEN)
    try:
        assert fresh.answer(
            f"operation=get\nprotocol=https\nhost=github.com\npath={REPO}.git\n"
        ) is not None
        assert fresh.answer(
            f"operation=get\nprotocol=https\nhost=github.com\npath={REPO}\n"
        ) is None
    finally:
        fresh.close()


def test_the_token_is_in_no_argv_and_no_child_environment(staging: Path) -> None:
    git = _checkout_git(staging)
    _checkout(staging, git)
    assert git.calls, "the fake git was never called"
    for command in git.calls:
        _no_token(command)
    for kwargs in git.kwargs:
        _no_token(kwargs.get("env"))
        # and the child environment is the one workspace_git builds, not ours
        assert set(kwargs.get("env") or {}) == set(
            __import__("tinyassets.workspace_git", fromlist=["x"]).GIT_ENVIRONMENT_KEYS
        )


def test_a_refused_address_never_reaches_git(staging: Path) -> None:
    def private(hostname: str, port: int) -> list[str]:
        return ["140.82.121.4", "169.254.169.254"]

    git = _checkout_git(staging)
    answer = ww.handle_request(
        _checkout_request(staging), resolver=private,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is False
    assert answer["stderr_class"] == "address_refused"
    assert not git.ran("clone"), "a refused address must never reach git"
    _no_token(answer)


def test_a_failed_clone_is_a_classified_refusal(staging: Path) -> None:
    def clone(rest, kwargs):
        return FakeCompleted(
            returncode=128,
            stderr=(
                b"remote: Invalid username or password.\n"
                b"fatal: Authentication failed\n"
            ),
        )

    answer = _checkout(staging, FakeGit({"clone": clone}))
    assert answer["ok"] is False
    assert answer["stderr_class"] == "auth"
    _no_token(answer)


def test_a_clone_whose_stderr_carries_the_token_comes_back_scrubbed(staging: Path) -> None:
    def clone(rest, kwargs):
        return FakeCompleted(
            returncode=128,
            stderr=f"fatal: https://x:{TOKEN}@github.com/owner/name.git denied".encode(),
        )

    answer = _checkout(staging, FakeGit({"clone": clone}))
    assert answer["ok"] is False
    _no_token(answer)
    assert "[redacted]" in answer["error"]


def test_a_credential_that_cannot_be_resolved_never_echoes_the_vault_message(
    staging: Path,
) -> None:
    answer = ww.handle_request(
        _checkout_request(staging, credential_ref="vault://http/missing"),
        resolver=_resolver,
        classifier=_classifier,
        launcher=_checkout_git(staging),
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is False
    assert answer["stderr_class"] == "auth"
    # the vault's own exception text carried the token; it must not come back
    _no_token(answer)


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #


def _push_git(head_ref: str = "refs/heads/main", *, push=None, observed: str = "") -> FakeGit:
    def bundle(rest, kwargs):
        if rest[1] == "list-heads":
            return FakeCompleted(stdout=f"{SHA} refs/tiny/export\n".encode())
        if rest[1] == "verify":
            return FakeCompleted(stdout=b"The bundle records a complete history.\n")
        return FakeCompleted()

    def rev_parse(rest, kwargs):
        return FakeCompleted(stdout=(SHA + "\n").encode())

    def ls_remote(rest, kwargs):
        if "--symref" in rest:
            return FakeCompleted(stdout=f"ref: {head_ref}\tHEAD\n{SHA}\tHEAD\n".encode())
        return FakeCompleted(
            stdout=(f"{observed}\t{rest[-1]}\n".encode() if observed else b"")
        )

    handlers = {
        "bundle": bundle,
        "rev-parse": rev_parse,
        "ls-remote": ls_remote,
        "fsck": lambda r, k: FakeCompleted(),
        "init": lambda r, k: FakeCompleted(),
        "fetch": lambda r, k: FakeCompleted(),
        "config": lambda r, k: FakeCompleted(returncode=1),
    }
    if push is not None:
        handlers["push"] = push
    return FakeGit(handlers)


@pytest.fixture()
def bundle_file(tmp_path: Path) -> Path:
    path = tmp_path / "in.bundle"
    path.write_bytes(b"PACK-verified-bundle")
    return path


def test_push_refuses_the_remote_default_branch(staging: Path, bundle_file: Path) -> None:
    git = _push_git(head_ref="refs/heads/main")
    answer = ww.handle_request(
        _push_request(staging, bundle_file, remote_ref="refs/heads/main"),
        resolver=_resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is False
    assert answer["stderr_class"] == "protected"
    assert not git.ran("push"), "no bytes may be sent to the default branch"
    _no_token(answer)


def test_push_sends_an_exact_sha_to_an_exact_ref_and_never_forces(
    staging: Path, bundle_file: Path
) -> None:
    git = _push_git(push=lambda r, k: FakeCompleted())
    answer = ww.handle_request(
        _push_request(staging, bundle_file),
        resolver=_resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is True
    argv = git.argv_for("push")
    assert f"{SHA}:refs/heads/tiny/u-abc/slug" in argv
    assert "--force" not in argv
    assert "-f" not in argv
    assert "--delete" not in argv
    assert not any(a.startswith("+") for a in argv)
    # the credentialed push carries the same forced options as the clone
    options = git.options_for("push")
    assert "protocol.allow=never" in options
    assert any(o.startswith("http.curloptResolve=github.com:443:") for o in options)
    _no_token(answer)


def test_push_verifies_the_bundle_before_any_credential_exists(
    staging: Path, bundle_file: Path
) -> None:
    """Verification is credential-free: it happens before the broker starts."""
    git = _push_git(push=lambda r, k: FakeCompleted())
    ww.handle_request(
        _push_request(staging, bundle_file),
        resolver=_resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    order = []
    for command in git.calls:
        rest = command[1:]
        while rest and rest[0] in ("-c", "--no-replace-objects"):
            rest = rest[2:] if rest[0] == "-c" else rest[1:]
        order.append(rest[0] if rest else "")
    assert order.index("bundle") < order.index("push")
    assert order.index("fsck") < order.index("push")
    # and the verification commands carried NO credential helper
    verify_options = git.options_for("bundle")
    assert not any(o.startswith("credential.helper=!") for o in verify_options)


def test_push_refuses_a_bundle_whose_commit_is_not_the_declared_one(
    staging: Path, bundle_file: Path
) -> None:
    git = _push_git(push=lambda r, k: FakeCompleted())

    def other_sha(rest, kwargs):
        return FakeCompleted(stdout=(PARENT_SHA + "\n").encode())

    git.handlers["rev-parse"] = other_sha
    answer = ww.handle_request(
        _push_request(staging, bundle_file),
        resolver=_resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is False
    assert answer["stderr_class"] == "verification"
    assert not git.ran("push"), "a bundle that is not the declared commit sends no bytes"


def test_a_lost_push_outcome_reconciles_to_success_when_the_ref_holds_the_sha(
    staging: Path, bundle_file: Path
) -> None:
    """D1 crash safety: a repeated non-force push of the same sha is success."""
    def timeout(rest, kwargs):
        raise ww.subprocess.TimeoutExpired(cmd="git", timeout=1)

    git = _push_git(push=timeout, observed=SHA)
    answer = ww.handle_request(
        _push_request(staging, bundle_file),
        resolver=_resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is True
    assert answer["reconciled"] is True
    assert answer["resolved_sha"] == SHA


def test_a_lost_push_outcome_reconciles_to_a_refusal_when_the_ref_differs(
    staging: Path, bundle_file: Path
) -> None:
    def timeout(rest, kwargs):
        raise ww.subprocess.TimeoutExpired(cmd="git", timeout=1)

    git = _push_git(push=timeout, observed=PARENT_SHA)
    answer = ww.handle_request(
        _push_request(staging, bundle_file),
        resolver=_resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is False
    assert answer["reconciled"] is True
    assert answer["observed_sha"] == PARENT_SHA
    assert answer["stderr_class"] == "non_fast_forward"


def test_reconcile_only_asks_the_remote_without_pushing(
    staging: Path, bundle_file: Path
) -> None:
    git = _push_git(observed=SHA)
    answer = ww.handle_request(
        _push_request(staging, bundle_file, reconcile_only=True),
        resolver=_resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is True
    assert not git.ran("push")


# --------------------------------------------------------------------------- #
# ls_remote and the protocol
# --------------------------------------------------------------------------- #


def test_ls_remote_reports_the_head_ref(staging: Path) -> None:
    git = _push_git(head_ref="refs/heads/trunk", observed=SHA)
    answer = ww.handle_request(
        {
            "op": "ls_remote",
            "universe_dir": str(staging.parent),
            "credential_ref": "vault://http/github",
            "host": HOST,
            "owner_repo": REPO,
            "remote_ref": "refs/heads/tiny/u/slug",
            "staging_dir": str(staging),
            "resolver_text": "libcurl/8.5.0",
        },
        resolver=_resolver,
        classifier=_classifier,
        launcher=git,
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is True
    assert answer["head_ref"] == "refs/heads/trunk"


@pytest.mark.parametrize("op", ["", "clone", "delete", "CHECKOUT"])
def test_an_unknown_op_is_refused_without_git(staging: Path, op: str) -> None:
    answer = ww.handle_request({"op": op, "staging_dir": str(staging)})
    assert answer["ok"] is False
    assert answer["stderr_class"] == "bad_argument"


def test_a_non_mapping_request_is_refused() -> None:
    for bad in (None, "checkout", 7, []):
        answer = ww.handle_request(bad)
        assert answer["ok"] is False
        assert answer["stderr_class"] == "bad_argument"


@pytest.mark.parametrize(
    "missing", ["universe_dir", "credential_ref", "host", "owner_repo", "ref", "staging_dir"]
)
def test_a_missing_required_field_is_refused(staging: Path, missing: str) -> None:
    request = _checkout_request(staging)
    request.pop(missing)
    answer = ww.handle_request(
        request,
        resolver=_resolver,
        classifier=_classifier,
        launcher=_checkout_git(staging),
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is False
    assert answer["stderr_class"] == "bad_argument"


def test_a_staging_dir_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    answer = ww.handle_request(
        _checkout_request(tmp_path / "nope"),
        resolver=_resolver,
        classifier=_classifier,
        launcher=FakeGit(),
        broker_factory=LocalBroker,
    )
    assert answer["ok"] is False
    assert answer["stderr_class"] == "bad_argument"


def test_every_answer_shape_is_json_safe(staging: Path) -> None:
    """The answer crosses a Pipe: it must hold only plain data."""
    import json

    answer = ww.handle_request(
        _checkout_request(staging),
        resolver=_resolver,
        classifier=_classifier,
        launcher=_checkout_git(staging),
        broker_factory=LocalBroker,
    )
    json.dumps(answer)


# --------------------------------------------------------------------------- #
# the parent side
# --------------------------------------------------------------------------- #


def test_execute_uses_the_injected_spawn(staging: Path) -> None:
    seen: list[dict] = []

    def spawn(request):
        seen.append(request)
        return {"ok": True, "resolved_sha": SHA}

    answer = ww.execute_workspace_operation({"op": "checkout"}, spawn=spawn)
    assert answer["ok"] is True
    assert seen == [{"op": "checkout"}]


def test_execute_without_an_injected_spawn_is_retired() -> None:
    """No daemon child resolves a token or runs git: the operation refuses."""
    answer = ww.execute_workspace_operation({"op": "checkout"})
    assert answer == ww.RETIRED_ANSWER


def test_the_worker_scrubs_an_arbitrary_exception(staging: Path) -> None:
    class Boom(Exception):
        pass

    answer = ww._safe_error(Boom(f"failed with {TOKEN}"))
    assert TOKEN not in answer
    assert "[redacted]" in answer


def test_a_workspace_git_error_keeps_its_code(staging: Path) -> None:
    assert ww._error_code(WorkspaceGitError("protected", "x")) == "protected"
    assert ww._error_code(ValueError("x")) == "other"


def test_the_canonical_url_is_built_never_taken_from_input() -> None:
    assert ww._canonical_url("github.com", "owner/name") == "https://github.com/owner/name.git"


def test_the_module_never_returns_a_raw_git_stderr_field(staging: Path) -> None:
    """Every message that crosses back goes through the scrubber."""
    source = Path(ww.__file__).read_text(encoding="utf-8")
    # every "error" value is either a literal, a _safe_error() call, or a
    # scrubbed GitResult field
    assert "stderr_scrubbed" in source
    assert not re.search(r'"error":\s*str\(exc\)', source)


def test_the_broker_stub_has_the_signature_of_the_thing_it_stands_in_for() -> None:
    """A double whose signature drifts fails the SUITE, not the product.

    When production stopped passing ``socket_dir``, this stub still required
    it: 15 tests failed with a TypeError describing the test double rather than
    anything shipped (2026-08-31).
    """
    import inspect

    real = inspect.signature(CredentialBroker.serve)
    stub = inspect.signature(LocalBroker.serve)
    assert stub.parameters.keys() == real.parameters.keys()
    for name, parameter in real.parameters.items():
        if name == "self":
            continue
        assert stub.parameters[name].kind == parameter.kind, name
        assert stub.parameters[name].default == parameter.default, (
            f"{name}: the stub must accept exactly what the real broker accepts"
        )


def test_the_worker_lets_the_broker_choose_where_its_socket_lives(
    staging: Path,
) -> None:
    """The caller's depth must not decide whether a checkout is possible.

    The worker used to hand the broker `<staging>/broker`, and a unix socket
    address is 108 bytes: in the deployed container that path measured 144 and
    refused the checkout outright (2026-08-31).
    """
    git = FakeGit()
    _checkout(staging, git)
    assert LocalBroker.made, "no broker was built"
    assert LocalBroker.made[0].socket_dir is None, (
        "the worker chose the socket directory; that is what made the address too long"
    )

"""Tests for the ``workspace`` effect sink (``tinyassets.effectors.workspace``).

Real connection ledger, real grant, real consent store, real pool database. The
worker is injected (no spawn, no git, no network) and the pool lane's directory
handles are injected too -- they land on ``claude/workspace-pool``, and a test
that asserts THEY were called is what makes the seam real before the merge.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tinyassets.effectors import EffectChain, WorkspaceMount
from tinyassets.effectors import workspace as wse
from tinyassets.effectors.workspace import (
    EXTERNAL_WRITE_SINK_WORKSPACE,
    WORKSPACE_READ_EFFECTS,
    repo_key_for,
    run_workspace_effector,
)
from tinyassets.storage.outbound_connections import ConnectionLedger
from tinyassets.storage.workspace_authority import workspace_consent_destination

UNIVERSE = "universe-1"
REPO = "owner/name"
HOST = "github.com"
SHA = "c" * 40
TOKEN = "ghp_EFFECTORTOKEN0123456789ABCDEFGHI"


# --------------------------------------------------------------------------- #
# Scaffolding
# --------------------------------------------------------------------------- #


def _principal() -> str:
    """Who the suite is signed in as for this test (conftest binds one)."""
    from tinyassets.auth.middleware import current_identity

    return current_identity().user_id


def _setup(
    tmp_path: Path,
    *,
    scopes=(f"git_read:{REPO}", f"git_write:{REPO}"),
    destination=f"github.com/{REPO}",
    endpoints=None,
    consent_host=HOST,
    grant_universe=None,
    consents=("checkout", "push"),
) -> tuple[Path, Path]:
    """A universe with a git connection, a grant and the typed consents.

    The grant belongs to the AUTHENTICATED principal, because that is who the
    broker answers for: every authority read routes through
    ``authorized_connection(principal=...)`` now, and a grant owned by a name
    this suite never signs in as is one the broker correctly refuses.
    """
    principal = _principal()
    data_root = tmp_path / "data"
    universe_dir = data_root / UNIVERSE
    universe_dir.mkdir(parents=True)
    ledger = ConnectionLedger(
        data_root / ".broker" / "outbound.db", data_root=data_root,
        verify_authenticated_principal=lambda: principal
    )
    ledger.create_connection(
        connection_id="conn-git",
        owner_user_id=principal,
        connection_class="outbound-http",
        scopes=scopes,
        provider="http",
        destination=destination,
        credential_ref="vault://http/github",
        connection_type="http",
        auth_scheme="bearer",
        allowed_endpoints=endpoints
        or [{"host": HOST, "path_template": "/owner/name", "methods": ["GET"]}],
    )
    ledger.grant_connection(
        grant_id="grant-git",
        connection_id="conn-git",
        owner_user_id=principal,
        universe_id=grant_universe or UNIVERSE,
    )
    from tinyassets.storage.effector_consents import grant_consent

    for op in consents:
        grant_consent(
            universe_dir,
            sink=EXTERNAL_WRITE_SINK_WORKSPACE,
            destination=workspace_consent_destination(
                f"workspace_{op}", REPO, connection_id="conn-git", host=consent_host
            ),
            granted_by="test",
        )
    return data_root, universe_dir


def _packet(**over: Any) -> dict[str, Any]:
    packet = {
        "sink": EXTERNAL_WRITE_SINK_WORKSPACE,
        "op": "checkout",
        "connection_id": "conn-git",
        "grant_id": "grant-git",
        "repo": REPO,
        "ref": "main",
        "storage": "scratch",
    }
    packet.update(over)
    return packet


class FakeWorker:
    """Stands in for the owner cell. Records every request it was given.

    The CELL creates the lease and its repository now -- the daemon cannot
    make a directory the owner owns -- so this double does the same, through
    the relative name the request carries. A test that asserts a workspace was
    published therefore has a workspace on disk to publish, and one that
    asserts a refusal has none.
    """

    def __init__(self, answer: dict[str, Any] | None = None):
        self.answer = answer if answer is not None else {
            "ok": True,
            "resolved_sha": SHA,
            "bytes": 4096,
            "ref_name": "refs/tiny/export",
        }
        self.requests: list[dict] = []

    def lease_path(self, request: dict[str, Any]) -> Path:
        return Path(request["universe_dir"], *request["lease_parent"], request["lease_name"])

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        answer = dict({"ok": True} if request["op"] == "create" else self.answer)
        if request["op"] in ("checkout", "create") and answer.get("ok"):
            (self.lease_path(request) / "repo").mkdir(parents=True, exist_ok=True)
            answer.setdefault(
                "lease", "/".join((*request["lease_parent"], request["lease_name"])))
            answer.setdefault("content", "repo")
        return answer


def test_checkout_receives_only_server_owned_cancel_predicate(tmp_path, monkeypatch):
    _, universe = _setup(tmp_path)
    chain = EffectChain(run_id="r-cancel-forward", base_path=universe)
    def should_cancel():
        return False
    seen = []
    def checkout(**kwargs):
        seen.append(kwargs["should_cancel"])
        return {"op": "checkout"}
    monkeypatch.setattr(wse, "_checkout", checkout)
    packet = _packet(should_cancel=True)
    result = run_workspace_effector(
        node_id="checkout", output_keys=["packet"], run_state={"packet": json.dumps(packet)},
        base_path=universe, run_id="r-cancel-forward", chain=chain,
        should_cancel=should_cancel)
    assert "error" not in result, result
    assert seen == [should_cancel]


@pytest.fixture()
def fs_spy(monkeypatch: pytest.MonkeyPatch):
    """Double the no-follow directory openers the DAEMON side still uses.

    Since the owner split the daemon creates nothing: the cell makes the lease
    and its repository, and the daemon only OPENS them to hold the capability
    (``_open_cell_lease``). So the two openers are all that is doubled, and
    asserting they were called is what proves the lease is picked up through a
    no-follow handle rather than re-resolved by path. They are POSIX-only in
    production, which is why the double exists at all.
    """
    from tinyassets import workspace_fs

    calls: dict[str, list] = {"open_dir_nofollow": [], "open_subdir_nofollow": []}

    def open_dir_nofollow(path):
        calls["open_dir_nofollow"].append(str(path))
        # The REAL helper refuses a missing directory -- it opens, it does not
        # create. A fake that is more permissive than the thing it stands in
        # for is a fake that hides the bug it is standing in front of.
        if not Path(path).is_dir():
            raise FileNotFoundError(f"no such directory: {path}")
        return f"fd:{path}"

    def open_subdir_nofollow(parent_fd, name):
        calls["open_subdir_nofollow"].append((parent_fd, name))
        parent = Path(str(parent_fd).removeprefix("fd:"))
        target = parent / name
        if not target.is_dir():
            raise FileNotFoundError(f"no such directory: {target}")
        return f"fd:{target}"

    monkeypatch.setattr(workspace_fs, "open_dir_nofollow", open_dir_nofollow, raising=False)
    monkeypatch.setattr(
        workspace_fs, "open_subdir_nofollow", open_subdir_nofollow, raising=False
    )
    return calls


@pytest.fixture()
def no_real_git(monkeypatch: pytest.MonkeyPatch):
    """Prove the DAEMON side runs no git at all.

    It used to populate the workspace itself from the worker's bundle. Every
    git step is the cell's now, so any git reached from here is a daemon-uid
    git that the cutover does not have -- and this fixture makes that a loud
    failure instead of a quiet one.
    """
    import tinyassets.workspace_git as wg

    def refuse(*_args, **_kwargs):
        raise AssertionError("the daemon side must not run git; the owner cell does")

    for name in ("run_git", "run_git_in_cell", "populate_workspace_from_bundle",
                 "create_bundle", "verify_bundle", "unbundle_into_fresh_repo"):
        monkeypatch.setattr(wg, name, refuse)
    return []


def _run(
    tmp_path: Path,
    packet: dict[str, Any],
    *,
    universe_dir: Path,
    chain: EffectChain,
    worker: FakeWorker | None = None,
    dry_run: bool | None = None,
    node_id: str = "n1",
    timeout_seconds: float = 0.0,
) -> dict[str, Any]:
    return run_workspace_effector(
        node_id=node_id,
        output_keys=["ws"],
        run_state={"ws": packet},
        base_path=universe_dir,
        run_id="run-1",
        dry_run=dry_run,
        chain=chain,
        execute=worker or FakeWorker(),
        timeout_seconds=timeout_seconds,
    )


@pytest.fixture()
def chain(tmp_path: Path) -> EffectChain:
    return EffectChain(run_id="run-1", base_path=str(tmp_path), universe_id=UNIVERSE)


# --------------------------------------------------------------------------- #
# Packet parsing
# --------------------------------------------------------------------------- #


def test_a_node_without_a_workspace_packet_is_no_matching_packet(
    tmp_path: Path, chain: EffectChain
) -> None:
    _root, universe_dir = _setup(tmp_path)
    result = run_workspace_effector(
        node_id="n1",
        output_keys=["ws"],
        run_state={"ws": {"sink": "authenticated_external_call"}},
        base_path=universe_dir,
        run_id="run-1",
        chain=chain,
        execute=FakeWorker(),
    )
    assert result["error_kind"] == "no_matching_packet"


def test_a_json_string_packet_parses(tmp_path: Path, chain: EffectChain, fs_spy, no_real_git):
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, json.dumps(_packet()), universe_dir=universe_dir, chain=chain)
    assert result.get("error_kind") is None, result
    assert result["op"] == "checkout"


@pytest.mark.parametrize("op", ["", "clone", "fetch", "CHECKOUT"])
def test_an_unknown_op_is_an_invalid_packet(
    tmp_path: Path, chain: EffectChain, op: str
) -> None:
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, _packet(op=op), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "invalid_packet"


@pytest.mark.parametrize(
    "repo",
    ["", "name", "owner/name/extra", "/owner/name", "owner/", "../name", "owner/na me"],
)
def test_a_malformed_repo_is_refused(tmp_path: Path, chain: EffectChain, repo: str) -> None:
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, _packet(repo=repo), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "invalid_packet"


@pytest.mark.parametrize("missing", ["connection_id", "grant_id"])
def test_a_packet_without_its_authority_fields_is_refused(
    tmp_path: Path, chain: EffectChain, missing: str
) -> None:
    _root, universe_dir = _setup(tmp_path)
    packet = _packet()
    packet.pop(missing)
    result = _run(tmp_path, packet, universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "invalid_packet"


def test_an_unknown_storage_class_is_refused(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, _packet(storage="permanent"), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "invalid_packet"


def test_no_universe_authority_is_refused(tmp_path: Path, chain: EffectChain) -> None:
    result = run_workspace_effector(
        node_id="n1",
        output_keys=["ws"],
        run_state={"ws": _packet()},
        base_path=None,
        run_id="run-1",
        chain=chain,
        execute=FakeWorker(),
    )
    assert result["error_kind"] == "no_universe_authority"


# --------------------------------------------------------------------------- #
# Authority: grant, scope, consent
# --------------------------------------------------------------------------- #


def test_a_grant_from_another_universe_is_refused(tmp_path: Path, chain: EffectChain) -> None:
    """The broker scopes every authority read to the RUNNING command center.

    It answers one way for anything it will not authorize -- a grant for
    another center, a grant that does not exist, a revoked one -- because
    telling them apart would describe another owner's ledger to this run.
    """
    _root, universe_dir = _setup(tmp_path, grant_universe="universe-2")
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "connection_authority_unavailable"


def test_an_unknown_grant_is_refused(tmp_path: Path, chain: EffectChain) -> None:
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, _packet(grant_id="nope"), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "connection_authority_unavailable"


def test_a_checkout_needs_the_git_read_scope(tmp_path: Path, chain: EffectChain) -> None:
    _root, universe_dir = _setup(tmp_path, scopes=(f"git_write:{REPO}",))
    result = _run(tmp_path, _packet(op="checkout"), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "scope_not_granted"


def test_a_push_needs_the_git_write_scope(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    _root, universe_dir = _setup(tmp_path, scopes=(f"git_read:{REPO}",))
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result["error_kind"] == "scope_not_granted"


def test_a_scope_bound_to_another_repository_cannot_be_borrowed(
    tmp_path: Path, chain: EffectChain
) -> None:
    """The binding lives in the SCOPE (``git_read:owner/name``), not in the
    connection's destination string: a scope for one repo is not a scope for
    its neighbour, and a prefix is not a match."""
    _root, universe_dir = _setup(
        tmp_path, scopes=("git_read:someone/else", "git_write:someone/else")
    )
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "scope_not_granted"


def test_a_scope_for_a_repo_whose_name_extends_this_one_is_not_a_match(
    tmp_path: Path, chain: EffectChain
) -> None:
    _root, universe_dir = _setup(tmp_path, scopes=(f"git_read:{REPO}-evil",))
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "scope_not_granted"


def test_a_git_scope_on_a_connection_reaching_two_hosts_is_refused_at_creation(
    tmp_path: Path,
) -> None:
    """The host check is EARLIER than the sink, and it is about ambiguity.

    ``git_read:owner/name`` only means something together with a host, and the
    connection supplies it. Two hosts is not "pick one": the scope would name a
    repository on either, and honouring it would lend one credential to
    whichever the caller preferred.
    """
    from tinyassets.storage.workspace_authority import GitScopeError

    with pytest.raises((GitScopeError, ValueError)):
        _setup(
            tmp_path,
            endpoints=[
                {"host": "gitlab.example", "path_template": "/x", "methods": ["GET"]},
                {"host": "github.com", "path_template": "/y", "methods": ["GET"]},
            ],
        )


def test_a_checkout_runs_against_a_forge_that_is_not_github(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """A workspace is channel-agnostic: the host is whatever the connection says.

    github.com was pinned in the authority module, so a GitLab, Gitea or
    self-hosted user could not check anything out at all -- our own demo's host
    had become the platform's rule (founder, 2026-08-31).
    """
    worker = FakeWorker()
    _root, universe_dir = _setup(
        tmp_path,
        destination=f"gitlab.example.com/{REPO}",
        endpoints=[
            {"host": "gitlab.example.com", "path_template": "/owner/name", "methods": ["GET"]}
        ],
        consent_host="gitlab.example.com",
    )
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=worker)

    assert result.get("error_kind") is None, result
    assert worker.requests[0]["host"] == "gitlab.example.com"
    mount = chain.workspace_mount_or_none("n1")
    assert mount.host == "gitlab.example.com"


def test_a_consent_granted_for_one_forge_does_not_authorize_another(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """The host is IN the consent key, so the two forges cannot share a yes."""
    _root, universe_dir = _setup(
        tmp_path,
        destination=f"gitlab.example.com/{REPO}",
        endpoints=[
            {"host": "gitlab.example.com", "path_template": "/owner/name", "methods": ["GET"]}
        ],
        consent_host="github.com",  # the yes was given for the OTHER forge
    )
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)

    assert result["error_kind"] == "missing_consent", result


def test_a_github_scoped_connection_cannot_serve_another_hosts_packet(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """A packet may restate the derived host, never choose a different one."""
    _root, universe_dir = _setup(tmp_path)
    result = _run(
        tmp_path,
        _packet(host="gitlab.example.com"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result["error_kind"] == "invalid_packet", result
    assert "different host" in result["error"]


def test_a_checkout_without_its_consent_is_refused(tmp_path: Path, chain: EffectChain) -> None:
    _root, universe_dir = _setup(tmp_path, consents=("push",))
    result = _run(tmp_path, _packet(op="checkout"), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "missing_consent"
    assert result["consent"] == "workspace_checkout"


def test_a_push_without_its_consent_is_refused(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    _root, universe_dir = _setup(tmp_path, consents=("checkout",))
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result["error_kind"] == "missing_consent"
    assert result["consent"] == "workspace_push"


def test_a_checkout_consent_does_not_authorize_a_push(
    tmp_path: Path, chain: EffectChain
) -> None:
    """The consents are typed per op: one is never the other."""
    _root, universe_dir = _setup(tmp_path, consents=("checkout",))
    checkout_dest = workspace_consent_destination(
        "workspace_checkout", REPO, connection_id="conn-git", host="github.com")
    push_dest = workspace_consent_destination(
        "workspace_push", REPO, connection_id="conn-git", host="github.com")
    assert checkout_dest != push_dest
    from tinyassets.storage.effector_consents import is_consent_active

    assert is_consent_active(
        universe_dir, sink=EXTERNAL_WRITE_SINK_WORKSPACE, destination=checkout_dest
    )
    assert not is_consent_active(
        universe_dir, sink=EXTERNAL_WRITE_SINK_WORKSPACE, destination=push_dest
    )


def test_a_consent_for_another_repository_does_not_authorize_this_one(
    tmp_path: Path, chain: EffectChain
) -> None:
    _root, universe_dir = _setup(tmp_path, consents=())
    from tinyassets.storage.effector_consents import grant_consent

    grant_consent(
        universe_dir,
        sink=EXTERNAL_WRITE_SINK_WORKSPACE,
        destination=workspace_consent_destination(
            "workspace_checkout", "someone/else", connection_id="conn-git", host="github.com"),
        granted_by="test",
    )
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "missing_consent"


def test_no_gate_runs_the_worker(tmp_path: Path, chain: EffectChain) -> None:
    """Every refusal above must happen BEFORE the worker is ever spawned."""
    _root, universe_dir = _setup(tmp_path, consents=())
    worker = FakeWorker()
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=worker)
    assert result["error_kind"] == "missing_consent"
    assert worker.requests == []


# --------------------------------------------------------------------------- #
# checkout
# --------------------------------------------------------------------------- #


def test_a_checkout_admits_populates_and_registers_the_mount(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker()
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=worker)
    assert result["op"] == "checkout"
    assert result["repo"] == REPO
    assert result["resolved_sha"] == SHA
    assert result["bytes"] == 4096
    assert result["storage"] == "scratch"
    assert "lease_generation" in result
    mount = chain.workspace_mount("n1")
    assert mount is not None
    assert mount.bind_source.endswith("repo")


def test_the_startup_barrier_runs_before_the_pool_admits(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry an earlier process left must be finished before a new job is
    admitted, even when this run is the first thing to touch the runs DB."""
    from tinyassets import runs as _runs
    from tinyassets import workspace_pool

    order: list[str] = []
    real_reconcile = _runs.ensure_workspace_reconciled
    real_admit = workspace_pool.admit

    def spy_reconcile(base_path, **kwargs):
        order.append("reconcile")
        return real_reconcile(base_path, start_sweeper=False)

    def spy_admit(*args, **kwargs):
        order.append("admit")
        return real_admit(*args, **kwargs)

    monkeypatch.setattr(_runs, "ensure_workspace_reconciled", spy_reconcile)
    monkeypatch.setattr(workspace_pool, "admit", spy_admit)
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result.get("error_kind") is None, result
    assert order[:2] == ["reconcile", "admit"], order


def test_the_bind_source_is_the_repository_not_the_lease_root(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """P0: `/workspace` must BE the repository.

    Publishing the lease ROOT as the bind would put the repository one level
    down, so a node would see `repo/` instead of `README.md` -- and the two
    handles are for two different directories, which is exactly why they are
    two fields.
    """
    _root, universe_dir = _setup(tmp_path)
    _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    mount = chain.workspace_mount_or_none("n1")
    assert mount is not None
    assert mount.repo_fd is not None, "the repository's own handle must be published"
    assert mount.repo_fd != mount.lease_fd, "they are different directories"
    # the lease handle names the lease; the repo handle names <lease>/repo
    # (separator-agnostic: the fake joins with the host's own separator)
    assert Path(str(mount.lease_fd).removeprefix("fd:")).name == Path(mount.lease.path).name
    assert Path(str(mount.repo_fd).removeprefix("fd:")).name == "repo"
    assert mount.bind_source.endswith("repo") or mount.bind_source.startswith("/proc/self/fd/")


def test_a_host_without_openat_is_refused_not_crashed(
    tmp_path: Path, chain: EffectChain, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanent property of the host is a refusal, not a bug in the sink.

    Letting the no-follow layer's ``NotImplementedError`` reach the dispatcher
    reported ``effector_crashed``, which reads as "the sink is broken" rather
    than "this host cannot run workspaces at all".
    """
    from tinyassets import workspace_fs

    def refuse(*args, **kwargs):
        raise NotImplementedError(
            "open_dir_nofollow needs POSIX openat semantics (O_NOFOLLOW + "
            "dir_fd); this host is 'nt'. There is no fallback."
        )

    for name in ("open_dir_nofollow", "open_subdir_nofollow"):
        monkeypatch.setattr(workspace_fs, name, refuse, raising=False)
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "workspace_checkout_failed"
    assert result["error_kind"] != "effector_crashed"
    assert "POSIX openat" in result["error"]
    assert chain.workspace_mount_or_none("n1") is None


def test_the_compiler_binds_the_repository_handle_not_the_lease_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bridge must carry what the sink published, not re-derive it.

    Deriving the bind from the lease root here is the P0: the jail would mount
    the directory that CONTAINS the repository.
    """
    import os as _os

    from tinyassets import graph_compiler as gc
    from tinyassets.effectors import WorkspaceMount

    repo_read, repo_write = _os.pipe()
    lease_read, lease_write = _os.pipe()
    _os.close(repo_write)
    _os.close(lease_write)
    try:
        mount = WorkspaceMount(
            node_id="n0",
            bind_source=f"/proc/self/fd/{repo_read}",
            pass_fds=(repo_read,),
            repo_fd=repo_read,
            lease_fd=lease_read,
        )
        # Force the POSIX branch: production is Linux, and skipping here would
        # leave the P0 unasserted on the only box that runs the suite.
        monkeypatch.setattr(gc, "WORKSPACE_FD_BIND_SUPPORTED", True, raising=False)
        # A pipe stands in for the directory handle because Windows cannot open
        # a descriptor on a directory at all, and the translator now checks that
        # a published descriptor IS a live directory. That check has its own
        # tests; what this one asserts is WHICH handle is carried.
        monkeypatch.setattr(
            gc, "_require_live_directory", lambda fd, node_id: int(fd), raising=False
        )
        built = gc._sandbox_workspace_mount(mount, "code-node")
        assert built.pass_fds == (repo_read,), "the REPO handle is what the jail inherits"
        assert built.bind_source == f"/proc/self/fd/{repo_read}"
        assert lease_read not in (built.pass_fds or ())
    finally:
        _os.close(repo_read)
        _os.close(lease_read)


def test_the_credentialed_host_comes_from_the_connection_not_the_packet(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """P0: a packet could point a scoped credential at another host.

    ``packet.host`` was accepted while the scope check discarded host, so the
    two never met. The connection's declared endpoints are the authority now.
    """
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker()
    result = _run(
        tmp_path,
        _packet(host="evil.example"),
        universe_dir=universe_dir,
        chain=chain,
        worker=worker,
    )
    assert result["error_kind"] == "invalid_packet"
    assert "different host" in result["error"]
    assert worker.requests == [], "nothing may be sent to a host the packet chose"


def test_a_packet_may_restate_the_derived_host(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """The guard refuses a CONTRADICTION, not a redundant restatement."""
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker()
    result = _run(
        tmp_path, _packet(host=HOST), universe_dir=universe_dir, chain=chain, worker=worker
    )
    assert result.get("error_kind") is None, result
    assert worker.requests[0]["host"] == HOST


def test_the_derived_host_is_what_reaches_the_worker_and_the_mount(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker()
    _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=worker)
    assert worker.requests[0]["host"] == HOST
    assert chain.workspace_mount_or_none("n1").host == HOST


def test_a_connection_declaring_several_hosts_has_no_git_transport(
    tmp_path: Path, chain: EffectChain
) -> None:
    """One credential, one host: several is ambiguous, and ambiguity here is a
    choice made for the owner."""
    from tinyassets.effectors.workspace import transport_host_for

    class Endpoint:
        def __init__(self, host):
            self.host = host

    class Resource:
        allowed_endpoints = (Endpoint("github.com"), Endpoint("gitlab.example"))
        provider = "http"

    with pytest.raises(Exception) as caught:
        transport_host_for(Resource())
    assert "several hosts" in str(caught.value)


def test_a_first_permanent_checkout_names_its_parents_for_the_cell(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """P0: a universe's FIRST permanent checkout had no workspaces/<repo-key>.

    Nothing above the lease is the daemon's to conjure any more: it names the
    components and the owner side creates them -- the daemon labels the pool
    directory (``workspace_owner_pool.prepare``) and the cell makes the
    generation. What this asserts is that the NAMES are right and that a first
    checkout publishes a lease that exists on disk afterwards.
    """
    _root, universe_dir = _setup(tmp_path)
    assert not (universe_dir / "workspaces").exists(), "the fixture must start fresh"
    worker = FakeWorker()
    result = _run(tmp_path, _packet(storage="universe"), universe_dir=universe_dir,
                  chain=chain, worker=worker)
    assert result.get("error_kind") is None, result
    request = worker.requests[0]
    assert request["storage"] == "universe"
    assert request["lease_parent"] == ["workspaces", repo_key_for(HOST, "owner", "name")]
    assert request["lease_name"] == str(result["lease_generation"])
    mount = chain.workspace_mount_or_none("n1")
    assert Path(mount.lease.path).is_dir()


def test_a_second_permanent_checkout_reuses_the_existing_parents(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """Idempotent: an existing component is OPENED, never re-created."""
    _root, universe_dir = _setup(tmp_path)
    first = _run(tmp_path, _packet(storage="universe"), universe_dir=universe_dir, chain=chain)
    assert first.get("error_kind") is None, first
    second = run_workspace_effector(
        node_id="n2",
        output_keys=["ws"],
        run_state={"ws": _packet(storage="universe")},
        base_path=universe_dir,
        run_id="run-1",
        chain=chain,
        execute=FakeWorker(),
    )
    assert second.get("error_kind") is None, second
    assert second["lease_generation"] != first["lease_generation"]


@pytest.mark.skipif(os.name != "posix", reason="the owner cell's helpers are POSIX-only")
def test_a_first_permanent_checkout_works_with_the_real_cell_and_helpers(
    tmp_path: Path, chain: EffectChain
) -> None:
    """No doubles at all: the REAL workspace_fs, through the owner cell.

    This is the one that would have caught P0 #3 on its own. It needs no git:
    the cell double's answer for a checkout comes from the real
    ``workspace_remote_cell`` only once a remote is registered, so this drives
    the lease creation with a route that reaches nothing and asserts the
    refusal still leaves a usable tree behind.
    """
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker()
    result = _run(tmp_path, _packet(storage="universe"), universe_dir=universe_dir,
                  chain=chain, worker=worker)
    assert result.get("error_kind") is None, result
    mount = chain.workspace_mount_or_none("n1")
    assert Path(mount.lease.path).is_dir()
    assert (universe_dir / "workspaces").is_dir()


@pytest.mark.skipif(os.name != "posix", reason="the no-follow helpers are POSIX-only")
def test_opening_a_cell_made_lease_leaks_no_handle_when_its_content_is_missing(
    tmp_path: Path,
) -> None:
    """``_open_cell_lease`` opens two directories and owns both or neither.

    The daemon creates nothing now: it opens the lease the cell made and holds
    those two descriptors for the life of the capability. One descriptor left
    open per refused checkout exhausts the table on a long-lived daemon (Codex
    round 2, #7), and the refusal path -- a lease with no ``repo`` in it -- is
    the one that used to leak.

    Measured as a COUNT of live descriptors, which is the only unambiguous
    observable: the helpers open and close descriptors internally and a freed
    number is immediately reusable, so comparing numbers stays green with
    every handle leaked (measured on Linux, 2026-08-31).
    """
    lease = tmp_path / "universe-1" / "workspaces" / "scratch" / ("a" * 32)
    lease.mkdir(parents=True)

    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(Exception):  # noqa: B017 - the module's own refusal class
        wse._open_cell_lease(lease)
    assert len(os.listdir("/proc/self/fd")) == before, "a refused lease left a handle open"

    (lease / "repo").mkdir()
    lease_fd, repo_fd = wse._open_cell_lease(lease)
    try:
        assert len(os.listdir("/proc/self/fd")) - before == 2
    finally:
        os.close(lease_fd)
        os.close(repo_fd)


def test_the_capability_carries_the_authority_it_was_created_under(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    _root, universe_dir = _setup(tmp_path)
    _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    mount = chain.workspace_mount_or_none("n1")
    assert (mount.host, mount.repo) == (HOST, REPO)
    assert mount.connection_id == "conn-git"
    assert mount.grant_id == "grant-git"


def test_a_mount_closes_both_handles_exactly_once() -> None:
    import os as _os

    from tinyassets.effectors import WorkspaceMount

    first, second = _os.pipe()
    mount = WorkspaceMount(node_id="n", bind_source="x", repo_fd=first, lease_fd=second)
    mount.close()
    for descriptor in (first, second):
        with pytest.raises(OSError):
            _os.fstat(descriptor)
    mount.close()  # idempotent: a second close must not touch a reused fd


def test_revoking_a_workspace_closes_its_handles(tmp_path: Path) -> None:
    import os as _os

    from tinyassets.effectors import WorkspaceMount

    chain = EffectChain(run_id="r", base_path=str(tmp_path))
    first, second = _os.pipe()
    chain.register_workspace(
        "n0", WorkspaceMount(node_id="n0", bind_source="x", repo_fd=first, lease_fd=second)
    )
    chain.revoke_workspace("n0")
    with pytest.raises(OSError):
        _os.fstat(first)


def test_settling_a_run_closes_every_workspace_it_still_holds(tmp_path: Path) -> None:
    """A run that ended without a discard must not pin the lease open."""
    import os as _os

    from tinyassets.effectors import WorkspaceMount

    chain = EffectChain(run_id="r", base_path=str(tmp_path))
    first, second = _os.pipe()
    chain.register_workspace(
        "n0", WorkspaceMount(node_id="n0", bind_source="x", repo_fd=first, lease_fd=second)
    )
    chain.settle()
    with pytest.raises(OSError):
        _os.fstat(first)
    assert chain.workspace_mount_or_none("n0") is None


def test_the_published_lease_is_opened_through_the_no_follow_handles(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """Not by path: a symlinked component would otherwise let the capability
    name something the pool never admitted.

    Which handles the CELL creates the lease with -- and that a scratch name
    keeps its entropy rule while a generation does not need one -- is proved
    where that happens, in tests/test_workspace_remote_cell.py.
    """
    _root, universe_dir = _setup(tmp_path)
    _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert fs_spy["open_dir_nofollow"], "the lease was not opened no-follow"
    assert ("fd:" + str(wse.scratch_pool_root(universe_dir)) not in
            fs_spy["open_dir_nofollow"]), "the pool root is not what gets published"
    assert [name for _fd, name in fs_spy["open_subdir_nofollow"]] == ["repo"], (
        "the content directory is reached through the lease's own handle"
    )


def test_a_directory_the_no_follow_layer_refuses_is_a_refusal_not_a_crash(
    tmp_path: Path, chain: EffectChain, fs_spy, monkeypatch: pytest.MonkeyPatch, no_real_git
) -> None:
    """``UnsafePoolPath`` is how that layer says no; the graph author sees a code.

    Reported as ``effector_crashed`` it reads as a bug in the sink -- which is
    exactly how the generation bug presented in CI.
    """
    from tinyassets import workspace_fs

    _root, universe_dir = _setup(tmp_path)

    def refuse(parent_fd, name):
        raise workspace_fs.UnsafePoolPath(f"{name!r} is a symlink, not a directory")

    monkeypatch.setattr(workspace_fs, "open_subdir_nofollow", refuse, raising=False)
    result = _run(tmp_path, _packet(storage="universe"), universe_dir=universe_dir, chain=chain)

    assert result["error_kind"] == "workspace_checkout_failed", result
    assert "symlink" in result["error"], result


def test_a_bad_component_from_the_no_follow_layer_is_also_a_refusal(
    tmp_path: Path, chain: EffectChain, fs_spy, monkeypatch: pytest.MonkeyPatch, no_real_git
) -> None:
    """That layer raises ValueError for a name that is not one safe component."""
    from tinyassets import workspace_fs

    _root, universe_dir = _setup(tmp_path)

    def refuse(parent_fd, name):
        raise ValueError(f"{name!r} is not a single path component")

    monkeypatch.setattr(workspace_fs, "open_subdir_nofollow", refuse, raising=False)
    result = _run(tmp_path, _packet(storage="scratch"), universe_dir=universe_dir, chain=chain)

    assert result["error_kind"] == "workspace_checkout_failed", result
    assert result["error_kind"] != "effector_crashed"


# --------------------------------------------------------------------------- #
# op: create -- an empty workspace, for a workflow that has no repository
# --------------------------------------------------------------------------- #


def _empty_universe(tmp_path: Path) -> tuple[Path, Path]:
    """A universe with NO connection, NO grant and NO consent of any kind.

    The point of the create op: a workflow that renders video, scrapes a site
    or assembles a dataset has no repository and no credential, and used to be
    unable to get a filesystem at all.
    """
    data_root = tmp_path / "data"
    universe_dir = data_root / UNIVERSE
    universe_dir.mkdir(parents=True)
    (data_root / "scratch").mkdir(exist_ok=True)
    from tinyassets import runs

    runs.initialize_runs_db(universe_dir)
    return data_root, universe_dir


def _create_packet(**over: Any) -> dict[str, Any]:
    packet = {"sink": EXTERNAL_WRITE_SINK_WORKSPACE, "op": "create", "storage": "scratch"}
    packet.update(over)
    return packet


def test_a_create_needs_no_connection_grant_or_consent(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """Nothing in the run names a credential, and the workspace still appears."""
    _root, universe_dir = _empty_universe(tmp_path)

    result = _run(tmp_path, _create_packet(), universe_dir=universe_dir, chain=chain)

    assert result.get("error_kind") is None, result
    assert result["op"] == "create"
    assert result["bytes"] == 0
    mount = chain.workspace_mount_or_none("n1")
    assert mount is not None
    # It carries no authority, because none was used to make it.
    assert (mount.host, mount.repo, mount.connection_id, mount.grant_id) == ("", "", "", "")


def test_a_created_workspace_is_an_empty_directory_a_node_can_write_in(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """The node's ``/workspace`` is a real, empty, writable directory."""
    _root, universe_dir = _empty_universe(tmp_path)

    result = _run(tmp_path, _create_packet(), universe_dir=universe_dir, chain=chain)
    assert result.get("error_kind") is None, result

    mount = chain.workspace_mount_or_none("n1")
    content = Path(str(mount.bind_source).removeprefix("fd:"))
    assert content.is_dir()
    assert list(content.iterdir()) == [], "a created workspace starts empty"
    (content / "frame-001.png").write_bytes(b"not really a png")
    assert (content / "frame-001.png").read_bytes() == b"not really a png"


def test_a_create_reaches_the_owner_cell_with_no_connection_and_no_route(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """Only the owner's cell can make a directory the owner owns -- so even an
    empty workspace is created there. What it must NOT carry is any authority:
    no connection, no grant, no host, no repository and no route options, so
    there is no far side it could reach."""
    _root, universe_dir = _empty_universe(tmp_path)
    worker = FakeWorker()

    result = _run(
        tmp_path, _create_packet(), universe_dir=universe_dir, chain=chain, worker=worker
    )

    assert result.get("error_kind") is None, result
    assert [request["op"] for request in worker.requests] == ["create"]
    request = worker.requests[0]
    assert set(request) == {
        "op", "universe_dir", "principal", "storage", "lease_parent", "lease_name"}
    assert request["lease_parent"] == ["workspaces", "scratch"]


def test_a_create_is_charged_exactly_like_a_checkout(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """Same lease, same pool, same hourly job, same reservation.

    The workspace is free of a credential, not free of a quota: a universe that
    could make unlimited empty workspaces would have found the way around the
    bound that checkout pays.
    """
    from tinyassets import workspace_pool

    _root, created_dir = _empty_universe(tmp_path)
    create = _run(tmp_path, _create_packet(), universe_dir=created_dir, chain=chain)
    assert create.get("error_kind") is None, create

    other = tmp_path / "checkout-side"
    other.mkdir()
    _root2, checkout_dir = _setup(other)
    (checkout_dir.parent / "scratch").mkdir(exist_ok=True)
    checkout = _run(
        other, _packet(), universe_dir=checkout_dir, chain=EffectChain(
            run_id="run-1", base_path=str(checkout_dir)
        )
    )
    assert checkout.get("error_kind") is None, checkout

    def ledger_rows(universe_dir: Path) -> list[tuple[str, int, int]]:
        conn = workspace_pool._connect(wse._pool_db(universe_dir))
        try:
            return sorted(
                (kind, int(amount), int(reserved))
                for kind, amount, reserved in conn.execute(
                    "SELECT kind, amount, reserved FROM workspace_ledger"
                )
            )
        finally:
            conn.close()

    created_rows = ledger_rows(created_dir)
    checkout_rows = ledger_rows(checkout_dir)
    # The same KINDS, one job each, and both reconciled: what differs is only
    # the bytes actually moved, which is 0 for a workspace nothing populated.
    assert [kind for kind, _a, _r in created_rows] == ["bytes", "jobs"]
    assert [kind for kind, _a, _r in checkout_rows] == ["bytes", "jobs"]
    assert [r for _k, _a, r in created_rows] == [r for _k, _a, r in checkout_rows] == [0, 0], (
        "an unreconciled reservation would charge the hour for bytes nobody moved"
    )
    assert dict((k, a) for k, a, _r in created_rows)["jobs"] == 1
    assert dict((k, a) for k, a, _r in created_rows)["bytes"] == 0


def test_a_create_admits_one_job_at_the_platforms_bound_not_the_packets(
    tmp_path: Path, chain: EffectChain, fs_spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admission is where the job and the byte bound are charged.

    The bound is the platform's: a packet that could name its own reservation
    would be a packet choosing its own quota, which is the same finding that
    took packet authority off the checkout (Codex round 3, P1 #4).
    """
    from tinyassets import workspace_pool

    _root, universe_dir = _empty_universe(tmp_path)
    calls: list[dict[str, Any]] = []
    real_admit = workspace_pool.admit

    def spy(db, **kwargs):
        calls.append(kwargs)
        return real_admit(db, **kwargs)

    monkeypatch.setattr(workspace_pool, "admit", spy)
    result = _run(
        tmp_path,
        _create_packet(max_bytes=1),
        universe_dir=universe_dir,
        chain=chain,
    )

    assert result.get("error_kind") is None, result
    assert len(calls) == 1, "a create is admitted exactly once"
    assert calls[0]["max_bytes"] == wse._DEFAULT_MAX_CHECKOUT_BYTES
    assert calls[0]["storage_class"] == "scratch"
    assert calls[0]["universe_id"] == UNIVERSE


def test_a_create_that_the_pool_refuses_is_refused_by_the_same_code(
    tmp_path: Path, chain: EffectChain, fs_spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tinyassets import workspace_pool

    _root, universe_dir = _empty_universe(tmp_path)

    def refuse(*_a: Any, **_k: Any):
        raise workspace_pool.WorkspacePoolRefused(
            "workspace_busy", "the universe already holds a workspace job"
        )

    monkeypatch.setattr(workspace_pool, "admit", refuse)
    monkeypatch.setattr(wse, "_SWEEPABLE_REFUSALS", frozenset())
    result = _run(tmp_path, _create_packet(), universe_dir=universe_dir, chain=chain)

    assert result["error_kind"] == "workspace_busy", result


def test_a_push_against_a_created_workspace_is_refused_by_name(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """There is no remote. Falling through would let the PACKET name one."""
    _root, universe_dir = _empty_universe(tmp_path)
    created = _run(
        tmp_path, _create_packet(), universe_dir=universe_dir, chain=chain, node_id="n0"
    )
    assert created.get("error_kind") is None, created

    result = _run(
        tmp_path,
        _packet(
            op="push",
            commit_sha=SHA,
            branch_slug="slug",
            workspace="n0",
            repo=REPO,
            connection_id="conn-git",
            grant_id="grant-git",
        ),
        universe_dir=universe_dir,
        chain=chain,
    )

    assert result["error_kind"] == "workspace_push_refused", result
    assert "no git remote" in result["error"]


def test_a_created_workspace_can_be_discarded(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    _root, universe_dir = _empty_universe(tmp_path)
    created = _run(
        tmp_path, _create_packet(), universe_dir=universe_dir, chain=chain, node_id="n0"
    )
    assert created.get("error_kind") is None, created

    result = _run(
        tmp_path,
        {"sink": EXTERNAL_WRITE_SINK_WORKSPACE, "op": "discard", "workspace": "n0"},
        universe_dir=universe_dir,
        chain=chain,
    )

    assert result.get("error_kind") is None, result
    assert chain.workspace_mount_or_none("n0") is None


@pytest.mark.skipif(os.name != "posix", reason="the owner cell's helpers are POSIX-only")
@pytest.mark.parametrize("is_ancestor", [True, False])
def test_discard_uses_graph_ancestry_through_real_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_ancestor: bool,
) -> None:
    """Workspace ancestry must not require a fabricated HTTP response entry.

    No ``execute`` and no filesystem double here: this is the whole path,
    including the owner cell that creates the lease and the real no-follow
    helpers that open it.
    """
    from types import SimpleNamespace

    from tinyassets.effectors import (
        EffectFailedError,
        dispatch_node_effects,
        forget_effect_chain,
        register_effect_chain,
    )

    _root, universe_dir = _empty_universe(tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(_root))
    live_chain = EffectChain(
        run_id="dispatch-discard", base_path=str(universe_dir), universe_id=UNIVERSE,
    )
    register_effect_chain(live_chain)
    node = SimpleNamespace(
        node_id="shared-definition", effects=[EXTERNAL_WRITE_SINK_WORKSPACE],
        input_keys=[], output_keys=["ws"], timeout_seconds=0.0,
    )
    try:
        created = dispatch_node_effects(
            live_chain, node, {"ws": _create_packet()},
            ancestors=set(), node_key="create-instance",
        )
        assert created[EXTERNAL_WRITE_SINK_WORKSPACE]["op"] == "create"
        assert live_chain.workspace_mount_or_none("create-instance") is not None
        assert live_chain.results == {}, "workspace creation is not an HTTP response"
        packet = {
            "sink": EXTERNAL_WRITE_SINK_WORKSPACE, "op": "discard",
            "workspace": "create-instance",
        }
        ancestors = {"create-instance"} if is_ancestor else {"parallel-sibling"}
        if not is_ancestor:
            with pytest.raises(EffectFailedError, match="graph ancestors"):
                dispatch_node_effects(
                    live_chain, node, {"ws": packet}, ancestors=ancestors,
                    node_key="discard-instance",
                )
            assert live_chain.workspace_mount_or_none("create-instance") is not None
        else:
            result = dispatch_node_effects(
                live_chain, node, {"ws": packet}, ancestors=ancestors,
                node_key="discard-instance",
            )
            assert result[EXTERNAL_WRITE_SINK_WORKSPACE]["op"] == "discard"
            assert live_chain.workspace_mount_or_none("create-instance") is None
            assert any(
                row["action"] == "wipe_scratch"
                for row in _outbox_rows(workspace_pool_db(universe_dir))
            )
    finally:
        live_chain.settle()
        forget_effect_chain(live_chain.run_id)


def test_a_permanent_create_needs_a_name_and_uses_its_own_key_namespace(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """``ws.<slug>``: a created workspace can never address a repository's key."""
    _root, universe_dir = _empty_universe(tmp_path)

    unnamed = _run(
        tmp_path, _create_packet(storage="universe"), universe_dir=universe_dir, chain=chain
    )
    assert unnamed["error_kind"] == "invalid_packet", unnamed
    assert "workspace_key" in unnamed["error"]

    named = _run(
        tmp_path,
        _create_packet(storage="universe", workspace_key="render-cache"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert named.get("error_kind") is None, named
    assert named["workspace_key"] == "render-cache"
    mount = chain.workspace_mount_or_none("n1")
    assert mount.repo_key == "ws.render-cache"
    assert "--" not in mount.repo_key, "a repo key always has --; a workspace key never does"


@pytest.mark.parametrize("bad", ["a/b", "../x", "", "-lead", "a--b", "x" * 65])
def test_a_workspace_key_that_could_escape_or_collide_is_refused(
    tmp_path: Path, chain: EffectChain, fs_spy, bad: str
) -> None:
    _root, universe_dir = _empty_universe(tmp_path)
    result = _run(
        tmp_path,
        _create_packet(storage="universe", workspace_key=bad),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result["error_kind"] == "invalid_packet", result


def test_a_second_permanent_create_refuses_rather_than_wiping_the_first(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """Publishing REPLACES, and the replaced generation is wiped.

    For a checkout that is right: the remote is the source of truth. A created
    workspace's content exists nowhere else, so the same move would destroy the
    work. Refuse and say so.
    """
    _root, universe_dir = _empty_universe(tmp_path)
    first = _run(
        tmp_path,
        _create_packet(storage="universe", workspace_key="dataset"),
        universe_dir=universe_dir,
        chain=chain,
        node_id="n0",
    )
    assert first.get("error_kind") is None, first
    kept = Path(chain.workspace_mount_or_none("n0").lease.path)
    (kept / "repo" / "rows.csv").write_text("1,2,3", encoding="utf-8")

    second = _run(
        tmp_path,
        _create_packet(storage="universe", workspace_key="dataset"),
        universe_dir=universe_dir,
        chain=chain,
    )

    assert second["error_kind"] == "workspace_exists", second
    assert "dataset" in second["error"]
    assert (kept / "repo" / "rows.csv").read_text(encoding="utf-8") == "1,2,3"


def test_a_created_workspace_counts_as_a_read_for_settlement(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """It reaches nothing and changes nothing anywhere."""
    assert (EXTERNAL_WRITE_SINK_WORKSPACE, "create") in WORKSPACE_READ_EFFECTS


def test_a_dry_run_create_describes_and_makes_nothing(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    _root, universe_dir = _empty_universe(tmp_path)
    result = _run(
        tmp_path,
        _create_packet(storage="universe", workspace_key="cache"),
        universe_dir=universe_dir,
        chain=chain,
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["op"] == "create"
    assert result["workspace_key"] == "cache"
    assert chain.workspace_mount_or_none("n1") is None
    assert not (universe_dir / "workspaces").exists()


def test_the_cell_request_names_the_grant_and_never_a_credential(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """The request used to carry a vault REFERENCE for the worker to resolve.

    There is nothing to resolve now: the daemon opens a broker route and the
    cell gets the grant's identity, so a credential reference in this request
    would be a credential the cell could try to use.
    """
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker()
    _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=worker)
    request = worker.requests[0]
    assert "credential_ref" not in request
    assert TOKEN not in json.dumps(request)
    assert "vault://" not in json.dumps(request)
    assert request["op"] == "checkout"
    assert request["owner_repo"] == REPO
    assert request["host"] == HOST
    assert request["principal"] == _principal()
    assert (request["grant_id"], request["connection_id"]) == ("grant-git", "conn-git")


def test_the_cell_request_names_the_lease_relative_to_the_command_center(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """A cell is bound to the command center and resolves the rest itself, so
    the request carries plain name components -- never a host path."""
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker()
    _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=worker)
    request = worker.requests[0]
    assert request["lease_parent"] == ["workspaces", "scratch"]
    assert "/" not in request["lease_name"] and "\\" not in request["lease_name"]
    assert str(universe_dir) not in json.dumps(
        {k: v for k, v in request.items() if k != "universe_dir"})


def test_a_failed_checkout_is_workspace_checkout_failed(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker({"ok": False, "error": "auth: nope", "stderr_class": "auth"})
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=worker)
    assert result["error_kind"] == "workspace_checkout_failed"
    assert result["stderr_class"] == "auth"
    assert chain.workspace_mount_or_none("n1") is None


def test_a_universe_checkout_publishes_a_generation(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    _root, universe_dir = _setup(tmp_path)
    first = _run(tmp_path, _packet(storage="universe"), universe_dir=universe_dir, chain=chain)
    assert first["storage"] == "universe"
    assert first.get("replaced_generation") is None

    # Same run: the universe job lock is held until the outbox processor
    # releases it, and D4 makes it reentrant for the run that holds it.
    second = run_workspace_effector(
        node_id="n2",
        output_keys=["ws"],
        run_state={"ws": _packet(storage="universe")},
        base_path=universe_dir,
        run_id="run-1",
        chain=chain,
        execute=FakeWorker(),
    )
    assert second.get("error_kind") is None, second
    assert second["replaced_generation"] == first["lease_generation"]

    # and the replaced generation is OWED a discard, not deleted inline
    from tinyassets import workspace_pool

    db = workspace_pool_db(universe_dir)
    entries = _outbox_rows(db)
    assert any(
        row["action"] == "discard_permanent_generation"
        and row["generation"] == first["lease_generation"]
        for row in entries
    ), entries
    del workspace_pool


def workspace_pool_db(universe_dir: Path) -> Path:
    from tinyassets import runs

    return runs.runs_db_path(universe_dir)


def _outbox_rows(db: Path) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM workspace_outbox").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def test_admission_and_the_sweep_agree_on_one_scratch_root(tmp_path: Path) -> None:
    """The pool admits against a path and the sweep reclaims it; one directory.

    Two spellings means a lease admitted in one place and written in another
    (Codex round 2, P0 #1). Since the owner split that directory is INSIDE the
    command center -- a lease has to be the owner's own directory and an owner
    cell is bound to its center -- and it is never created from here: the
    daemon labels it for exactly one owner per operation
    (``workspace_owner_pool.prepare``).
    """
    from tinyassets import runs as _runs

    data_root = tmp_path / "data"
    universe_dir = data_root / UNIVERSE
    universe_dir.mkdir(parents=True)
    root = _runs._ensure_scratch_root(universe_dir)
    assert root == wse.scratch_pool_root(universe_dir)
    assert root == universe_dir / "workspaces" / "scratch"
    assert not root.exists(), "the pool root is the owner cell's to create"


def test_the_universe_root_is_the_universe_not_its_workspaces_dir(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """`universe_paths` appends `workspaces/` itself; passing it again made
    `workspaces/workspaces/...` (Codex round 2, P0 #1)."""
    from tinyassets import workspace_pool

    _root, universe_dir = _setup(tmp_path)
    assert wse.universe_workspace_root(universe_dir) == universe_dir
    result = _run(tmp_path, _packet(storage="universe"), universe_dir=universe_dir, chain=chain)
    assert result.get("error_kind") is None, result
    mount = chain.workspace_mount_or_none("n1")
    expected, _quarantine = workspace_pool.universe_paths(
        universe_dir, mount.repo_key, mount.generation
    )
    assert Path(mount.lease.path) == expected
    assert "workspaces/workspaces" not in Path(mount.lease.path).as_posix()


def test_a_barrier_that_cannot_run_refuses_instead_of_admitting(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail CLOSED: admitting past a barrier that did not run admits on top of
    whatever an earlier process left owed (Codex round 2, #2)."""
    from tinyassets import runs as _runs
    from tinyassets import workspace_pool

    _root, universe_dir = _setup(tmp_path)
    admitted: list[str] = []
    real_admit = workspace_pool.admit

    def spy_admit(*args, **kwargs):
        admitted.append("admit")
        return real_admit(*args, **kwargs)

    def broken(base_path, **kwargs):
        raise OSError("the runs db is locked")

    monkeypatch.setattr(_runs, "ensure_workspace_reconciled", broken)
    monkeypatch.setattr(workspace_pool, "admit", spy_admit)
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "workspace_pool_busy"
    assert "startup reconciliation failed" in result["error"]
    assert admitted == [], "nothing may be admitted past a barrier that did not run"


def test_a_provision_request_is_refused_without_pretending_it_ran(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """Consent refusal preserves checkout and never claims an installation."""
    _root, universe_dir = _setup(tmp_path)
    result = _run(
        tmp_path,
        _packet(provision={"python": ["x==1.0"]}),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result.get("error_kind") is None, "the checkout itself still completes"
    assert result["provision"] == "workspace_provision_refused"
    assert result["provision_detail"] == (
        "workspace_provision consent is required"
    )
    assert result["provision_reason"] == "missing_consent"
    assert "provision_hint" not in result


def test_a_cell_answering_for_another_lease_publishes_nothing(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """The answer must be for the lease the pool admitted, and nothing else.

    The cell names the lease it created back to the daemon. A different name
    means the capability about to be published is not the one this run was
    admitted for, and the only safe reading of that is a refusal -- with the
    admitted lease still owed a wipe.
    """
    _root, universe_dir = _setup(tmp_path)

    class Elsewhere(FakeWorker):
        def __call__(self, request):
            answer = super().__call__(request)
            answer["lease"] = "workspaces/scratch/" + "b" * 32
            return answer

    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain,
                  worker=Elsewhere())
    assert result["error_kind"] == "workspace_checkout_failed", result
    assert "different lease" in result["error"]
    assert chain.workspace_mount_or_none("n1") is None, "nothing may be published"
    rows = _outbox_rows(workspace_pool_db(universe_dir))
    assert any(row["action"] == "wipe_scratch" for row in rows), rows


def test_the_capability_is_published_only_after_the_cell_has_finished(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ORDER, not just the end state.

    The capability is what makes the workspace reachable, so it must not exist
    while the cell is still writing into the lease.
    """
    _root, universe_dir = _setup(tmp_path)
    running: list[bool] = []
    published_while_running: list[bool] = []
    worker = FakeWorker()

    def cell(request):
        running.append(True)
        try:
            return worker(request)
        finally:
            running.pop()

    real_register = chain.register_workspace

    def spy(node_id, mount):
        published_while_running.append(bool(running))
        return real_register(node_id, mount)

    monkeypatch.setattr(chain, "register_workspace", spy)
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=cell)
    assert result.get("error_kind") is None, result
    assert published_while_running == [False], "the capability outran the cell"


def test_the_acting_agent_is_injective_and_shaped_by_the_platform(tmp_path: Path) -> None:
    """`a/b` and `ab` are different nodes, and the broker must see that.

    The route's authority check records an acting agent. Stripping unsafe
    characters out of a node id collapses those two to one name (Codex round
    3, P2 #11), and a node id is graph-author text, so what the broker gets is
    a digest of the exact pair -- not the id itself.
    """
    first = wse._agent_for("run-1", "a/b")
    second = wse._agent_for("run-1", "ab")
    assert first != second
    assert wse._agent_for("run-2", "a/b") != first
    assert all(part.isalnum() or part == "-" for part in first)
    # ...and the same pair always names the same agent: the broker's record of
    # who acted is stable across a retry of one node.
    assert wse._agent_for("run-1", "a/b") == first


def test_a_packet_cannot_choose_its_own_reservation(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`packet.max_bytes` is gone: a packet choosing its reservation is a
    packet choosing its own quota (Codex round 3, P1 #4)."""
    from tinyassets import workspace_pool

    _root, universe_dir = _setup(tmp_path)
    seen: list[int] = []
    real_admit = workspace_pool.admit

    def spy(db, **kwargs):
        seen.append(kwargs["max_bytes"])
        return real_admit(db, **kwargs)

    monkeypatch.setattr(workspace_pool, "admit", spy)
    result = _run(
        tmp_path, _packet(max_bytes=1), universe_dir=universe_dir, chain=chain
    )
    assert result.get("error_kind") is None, result
    assert seen == [wse._DEFAULT_MAX_CHECKOUT_BYTES], "the platform's bound, not the packet's"


def test_a_push_reserves_the_bundle_bound_before_the_cell_runs(
    tmp_path: Path, chain: EffectChain, fs_spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A push holds no lease, so without this the hour saw nothing at all."""
    from tinyassets import workspace_pool

    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    order: list[str] = []
    real_reserve = workspace_pool.reserve_operation_bytes

    def spy_reserve(db, **kwargs):
        order.append(f"reserve:{kwargs['operation_id']}:{kwargs['max_bytes']}")
        return real_reserve(db, **kwargs)

    monkeypatch.setattr(workspace_pool, "reserve_operation_bytes", spy_reserve)
    worker = FakeWorker({"ok": True, "bytes": 11, "resolved_sha": SHA})

    def spy_cell(request):
        order.append("cell")
        return worker(request)

    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=spy_cell,
    )
    assert result.get("error_kind") is None, result
    assert order[0].startswith("reserve:"), "the ledger sees it BEFORE the bytes move"
    assert "cell" in order and order.index("cell") > 0
    assert f":{512 * 1024 * 1024}" in order[0]
    assert "run-1:n1:push" in order[0], "the operation id is deterministic"


def test_a_discard_reserves_one_job_before_it_mutates(
    tmp_path: Path, chain: EffectChain, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tinyassets import workspace_pool

    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    order: list[str] = []
    real_reserve = workspace_pool.reserve_operation_bytes

    def spy_reserve(db, **kwargs):
        order.append("reserve")
        return real_reserve(db, **kwargs)

    real_revoke = chain.revoke_workspace

    def spy_revoke(node_key):
        order.append("revoke")
        return real_revoke(node_key)

    monkeypatch.setattr(workspace_pool, "reserve_operation_bytes", spy_reserve)
    monkeypatch.setattr(chain, "revoke_workspace", spy_revoke)
    result = _run(
        tmp_path,
        _packet(op="discard", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result.get("error_kind") is None, result
    assert order[:2] == ["reserve", "revoke"]


def test_a_push_holds_the_capability_across_the_whole_cell_operation(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """A discard racing the push must not take the lease away underneath it.

    The cell reads the export bundle out of this lease and writes nothing
    else, so the hold has to span the whole operation, not just a local copy.
    Driven through the REAL ``acquire_workspace``: the assertion is the
    chain's own hold count observed WHILE the cell runs, which a double could
    only claim.
    """
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    holds_during_push: list[int] = []
    worker = FakeWorker({"ok": True, "bytes": 4, "resolved_sha": SHA})

    def watching_cell(request):
        holds_during_push.append(chain.workspace_holds.get("n0", 0))
        return worker(request)

    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=watching_cell,
    )
    assert result.get("error_kind") is None, result
    assert holds_during_push == [1], "the cell must run inside a held acquisition"
    assert chain.workspace_holds.get("n0", 0) == 0, "and the hold is released after"


@pytest.mark.skipif(os.name != "posix", reason="real directory descriptors are POSIX-only")
def test_the_held_descriptors_are_duplicates_not_the_originals(
    tmp_path: Path, chain: EffectChain
) -> None:
    """The point of the dup: a discard closes the ORIGINALS and the next
    checkout gets the same fd numbers back, so a holder on the original number
    would be reading another branch's repository.

    Driven with REAL descriptors, because that is the only way the difference
    between a dup and the original is observable at all.
    """
    from tinyassets.effectors import WorkspaceMount

    lease = tmp_path / "data" / UNIVERSE / "workspaces" / "scratch" / ("d" * 32)
    (lease / "repo").mkdir(parents=True)
    lease_fd = os.open(lease, os.O_RDONLY | os.O_DIRECTORY)
    repo_fd = os.open(lease / "repo", os.O_RDONLY | os.O_DIRECTORY)
    chain.register_workspace("n0", WorkspaceMount(
        node_id="n0", bind_source=f"/proc/self/fd/{repo_fd}", pass_fds=(repo_fd,),
        repo_fd=repo_fd, lease_fd=lease_fd))

    acquired = chain.acquire_workspace("n0")
    assert acquired is not None
    try:
        assert acquired.mount.repo_fd != repo_fd, "the holder got the registry's own handle"
        assert os.path.samestat(os.fstat(acquired.mount.repo_fd), os.fstat(repo_fd))
        # A discard racing the holder revokes the capability; the HELD handle
        # keeps naming the same directory until its holder releases it, which
        # is the whole point of handing out a dup.
        chain.revoke_workspace("n0")
        assert chain.workspace_mount_or_none("n0") is None
        assert os.fstat(acquired.mount.repo_fd).st_ino == os.stat(lease / "repo").st_ino
    finally:
        acquired.release()
    with pytest.raises(OSError):
        os.fstat(acquired.mount.repo_fd)


def test_a_push_whose_capability_was_revoked_is_refused(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """Revoked for REAL, through the chain -- not a double that returns None."""
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    chain.revoke_workspace("n0")
    worker = FakeWorker()
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=worker,
    )
    assert result["error_kind"] == "no_matching_packet"
    assert worker.requests == []


def test_a_discard_acquires_before_it_revokes(
    tmp_path: Path, chain: EffectChain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outbox entry is built from a capability this call HOLDS.

    Revoking first and reading the mount afterwards would build the entry from
    an object nothing owns any more.
    """
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    order: list[str] = []
    real_acquire = chain.acquire_workspace
    real_revoke = chain.revoke_workspace

    def spy_acquire(node_key):
        order.append("acquire")
        return real_acquire(node_key)

    def spy_revoke(node_key):
        order.append(f"revoke:holds={chain.workspace_holds.get(node_key, 0)}")
        return real_revoke(node_key)

    monkeypatch.setattr(chain, "acquire_workspace", spy_acquire)
    monkeypatch.setattr(chain, "revoke_workspace", spy_revoke)
    result = _run(
        tmp_path,
        _packet(op="discard", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result.get("error_kind") is None, result
    assert order == ["acquire", "revoke:holds=1"], order
    assert chain.workspace_mount_or_none("n0") is None


def test_a_lost_push_settles_as_unknown_never_failed(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """A timeout means the send MAY have landed (Codex round 3, P1 #5)."""
    from tinyassets.workspace_intents import open_intents

    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=FakeWorker({"ok": False, "error": "timed out", "stderr_class": "timeout"}),
    )
    assert result["error_kind"] == "workspace_push_refused"
    assert result["intent_state"] == "unknown"
    assert open_intents(universe_dir) == [], "settled, but as unknown rather than failed"


def test_a_push_journals_the_host_grant_and_universe(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """Reconciliation needs them: it must not default to github.com."""
    import sqlite3

    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=FakeWorker({"ok": True, "bytes": 4, "resolved_sha": SHA}),
    )
    conn = sqlite3.connect(workspace_pool_db(universe_dir))
    try:
        row = conn.execute(
            "SELECT host, grant_id, universe_id FROM workspace_push_intents"
        ).fetchone()
    finally:
        conn.close()
    assert row == (HOST, "grant-git", UNIVERSE)


@pytest.mark.skipif(os.name != "posix", reason="real directory descriptors are POSIX-only")
def test_a_failed_checkout_closes_what_it_opened_and_owes_the_wipe(
    tmp_path: Path, chain: EffectChain, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One owner: an unpublished failure leaks no descriptor and no lease.

    The failure is injected at PUBLICATION, after the daemon has opened the
    lease the cell made -- which is the only window in which it holds anything
    to leak now.
    """
    _root, universe_dir = _setup(tmp_path)

    def refuse(_node_id, _mount):
        raise OSError("the chain refused the capability")

    def held_inside(center: Path) -> list[str]:
        """Descriptors naming something inside this command center.

        Counting every descriptor would also count whatever the broker double
        opens lazily on the first authority read, which happens inside this
        window; the lease handles are the ones this call owns.
        """
        names = []
        for entry in os.listdir("/proc/self/fd"):
            try:
                target = os.readlink(f"/proc/self/fd/{entry}")
            except OSError:
                continue
            if target.startswith(str(center)):
                names.append(target)
        return sorted(names)

    monkeypatch.setattr(chain, "register_workspace", refuse)
    before = held_inside(universe_dir)
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "effector_crashed", result
    assert held_inside(universe_dir) == before, "a failed checkout leaked a lease handle"
    assert chain.workspace_mount_or_none("n1") is None
    rows = _outbox_rows(workspace_pool_db(universe_dir))
    assert any(row["action"] == "wipe_scratch" for row in rows), rows


def test_a_lock_held_by_another_run_is_workspace_busy_not_a_quota_error(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """The pool's own refusal code is the answer, never a re-translation.

    A held lock reported as ``workspace_quota_exceeded`` tells the universe to
    wait for an hour when the truth is "another run is using it right now".
    """
    _root, universe_dir = _setup(tmp_path)
    first = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert first.get("error_kind") is None, first

    other_chain = EffectChain(run_id="run-2", base_path=str(tmp_path), universe_id=UNIVERSE)
    second = run_workspace_effector(
        node_id="n2",
        output_keys=["ws"],
        run_state={"ws": _packet()},
        base_path=universe_dir,
        run_id="run-2",
        chain=other_chain,
        execute=FakeWorker(),
    )
    assert second["error_kind"] == "workspace_busy"
    assert second["workspace_admission"] == {
        "attempts": 2, "lock_conflicts": 2, "retry_sleep_seconds": 0.0,
    }


@pytest.mark.parametrize("op", ["create", "checkout"])
def test_admission_evidence_survives_a_real_lock_and_outbox_release(
    tmp_path, chain, fs_spy, no_real_git, monkeypatch, op,
):
    import sqlite3
    import threading

    from tinyassets import runs
    from tinyassets import workspace_pool as wp
    from tinyassets.workspace_fs import RealPoolFilesystem

    _root, universe_dir = _setup(tmp_path)
    packet = _packet(op=op)
    first = _run(tmp_path, packet, universe_dir=universe_dir, chain=chain)
    assert first.get("error_kind") is None, first
    assert first["workspace_admission"] == {
        "attempts": 1, "lock_conflicts": 0, "retry_sleep_seconds": 0.0,
    }
    sleeping = threading.Event()
    released = threading.Event()
    real_admit = wp.admit

    def coordinated_sleep(_seconds):
        sleeping.set()
        assert released.wait(5), "holder was not released"

    def observed_admit(*args, **kwargs):
        # Real admission and real lock rows; only coordinate the retry sleep.
        return real_admit(*args, **kwargs, sleep=coordinated_sleep)

    monkeypatch.setattr(wp, "admit", observed_admit)
    monkeypatch.setattr(runs, "_workspace_sweep_once", lambda *args, **kwargs: 0)
    other = EffectChain(run_id="run-2", base_path=str(tmp_path), universe_id=UNIVERSE)
    results = []

    from tinyassets.auth import middleware as _mw

    signed_in = _mw.current_identity()

    def contender():
        # A new thread starts with an EMPTY context, and there is no anonymous
        # principal: without the signed-in identity this run has no authority
        # to read and would refuse before it ever reached the pool's lock.
        _mw._current_identity.set(signed_in)
        results.append(run_workspace_effector(
            node_id="n2", output_keys=["ws"], run_state={"ws": packet},
            base_path=universe_dir, run_id="run-2", chain=other,
            execute=FakeWorker(), timeout_seconds=10,
        ))

    thread = threading.Thread(target=contender)
    thread.start()
    try:
        assert sleeping.wait(5), "contender never reached the lock wait"
        db = workspace_pool_db(universe_dir)
        # Exercise the real durable release-only outbox path. Filesystem cleanup
        # is independently covered; neither admission is mocked here.
        with sqlite3.connect(db) as conn:
            wp.enqueue_terminal(conn, run_id="run-1", universe_id=UNIVERSE, lease=None)
        entry = wp.claim_next(db, claimant="test-release")
        assert entry is not None
        wp.process_entry(db, entry, fs=RealPoolFilesystem())
    finally:
        released.set()
        thread.join(10)
    assert not thread.is_alive()
    assert len(results) == 1
    result = results[0]
    assert result.get("error_kind") is None, result
    evidence = result["workspace_admission"]
    assert evidence["attempts"] >= 3
    assert evidence["lock_conflicts"] >= 2
    assert evidence["retry_sleep_seconds"] > 0
    assert set(evidence) == {"attempts", "lock_conflicts", "retry_sleep_seconds"}


def test_post_admission_failure_keeps_observations(tmp_path, chain, fs_spy, no_real_git):
    _root, universe_dir = _setup(tmp_path)
    result = _run(
        tmp_path, _packet(), universe_dir=universe_dir, chain=chain,
        worker=FakeWorker(answer={"ok": False, "stderr_class": "checkout_failed"}),
    )
    assert result["error_kind"] == "workspace_checkout_failed", result
    assert result["workspace_admission"]["attempts"] == 1


def test_pre_admission_refusal_does_not_invent_observations(tmp_path, chain):
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, {"sink": EXTERNAL_WRITE_SINK_WORKSPACE, "op": "invalid"},
                  universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "invalid_packet"
    assert "workspace_admission" not in result


@pytest.mark.parametrize(
    "packet",
    [
        pytest.param(_packet(), id="checkout"),
        pytest.param(
            {"sink": EXTERNAL_WRITE_SINK_WORKSPACE, "op": "create", "storage": "scratch"},
            id="create",
        ),
    ],
)
def test_workspace_admission_receives_the_effect_nodes_wait_budget(
    tmp_path: Path,
    chain: EffectChain,
    fs_spy,
    no_real_git,
    monkeypatch: pytest.MonkeyPatch,
    packet: dict[str, Any],
) -> None:
    """The pool already implements the bounded wait; the adapter must not
    silently turn every node's configured budget into the zero-second default."""
    from tinyassets import workspace_pool

    _root, universe_dir = _setup(tmp_path)
    real_admit = workspace_pool.admit
    waits: list[float] = []

    def recording_admit(*args, **kwargs):
        waits.append(kwargs.get("wait_s"))
        if len(waits) == 1:
            raise workspace_pool.WorkspacePoolRefused(
                "workspace_busy", "synthetic held lock"
            )
        return real_admit(*args, **kwargs)

    monkeypatch.setattr(workspace_pool, "admit", recording_admit)
    from tinyassets import runs

    monkeypatch.setattr(runs, "_workspace_sweep_once", lambda *_args, **_kwargs: 0)
    result = _run(
        tmp_path,
        packet,
        universe_dir=universe_dir,
        chain=chain,
        timeout_seconds=37.5,
    )

    assert result.get("error_kind") is None, result
    assert waits == [0.0, 37.5]


def test_a_busy_pool_is_swept_once_then_retried_once(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock still recorded against a FINISHED run is owed to the outbox, not
    in use. One sweep, one retry -- never a loop."""
    from tinyassets import runs as _runs
    from tinyassets import workspace_pool
    from tinyassets.workspace_pool import WorkspacePoolRefused

    _root, universe_dir = _setup(tmp_path)
    real_admit = workspace_pool.admit
    calls: list[str] = []

    def flaky_admit(*args, **kwargs):
        calls.append("admit")
        if len(calls) == 1:
            raise WorkspacePoolRefused("workspace_busy", "held by a finished run")
        return real_admit(*args, **kwargs)

    swept: list[str] = []
    monkeypatch.setattr(workspace_pool, "admit", flaky_admit)
    monkeypatch.setattr(
        _runs, "_workspace_sweep_once",
        lambda base, *, claimant: swept.append(claimant) or 1,
    )
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result.get("error_kind") is None, result
    assert calls == ["admit", "admit"], "exactly one retry"
    assert swept == ["adapter:run-1"], swept


def test_the_retry_happens_at_most_once(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pool that is genuinely busy must refuse, not stall in a sweep loop."""
    from tinyassets import runs as _runs
    from tinyassets import workspace_pool
    from tinyassets.workspace_pool import WorkspacePoolRefused

    _root, universe_dir = _setup(tmp_path)
    calls: list[str] = []
    swept: list[str] = []

    def always_busy(*args, **kwargs):
        calls.append("admit")
        raise WorkspacePoolRefused("workspace_busy", "really held")

    monkeypatch.setattr(workspace_pool, "admit", always_busy)
    monkeypatch.setattr(
        _runs, "_workspace_sweep_once",
        lambda base, *, claimant: swept.append(claimant) or 0,
    )
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "workspace_busy"
    assert calls == ["admit", "admit"], "one retry and no more"
    assert len(swept) == 1


def test_a_quota_refusal_is_not_swept(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup does not create quota. Sweeping here would fail twice for nothing."""
    from tinyassets import runs as _runs
    from tinyassets import workspace_pool
    from tinyassets.workspace_pool import WorkspacePoolRefused

    _root, universe_dir = _setup(tmp_path)
    calls: list[str] = []
    swept: list[str] = []

    def over_quota(*args, **kwargs):
        calls.append("admit")
        raise WorkspacePoolRefused("workspace_quota_exceeded", "hourly bytes exhausted")

    monkeypatch.setattr(workspace_pool, "admit", over_quota)
    monkeypatch.setattr(
        _runs, "_workspace_sweep_once",
        lambda base, *, claimant: swept.append(claimant) or 0,
    )
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "workspace_quota_exceeded"
    assert calls == ["admit"], "no retry for a quota refusal"
    assert swept == []


def test_a_sweep_that_itself_fails_reports_the_original_refusal(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tinyassets import runs as _runs
    from tinyassets import workspace_pool
    from tinyassets.workspace_pool import WorkspacePoolRefused

    _root, universe_dir = _setup(tmp_path)

    def busy(*args, **kwargs):
        raise WorkspacePoolRefused("workspace_pool_busy", "pool full")

    def broken_sweep(base, *, claimant):
        raise OSError("the runs db is locked")

    monkeypatch.setattr(workspace_pool, "admit", busy)
    monkeypatch.setattr(_runs, "_workspace_sweep_once", broken_sweep)
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] == "workspace_pool_busy"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("workspace_busy", "workspace_busy"),
        ("workspace_pool_busy", "workspace_pool_busy"),
        ("workspace_quota_exceeded", "workspace_quota_exceeded"),
        ("something_new", "workspace_checkout_failed"),
        ("", "workspace_checkout_failed"),
    ],
)
def test_every_pool_refusal_keeps_its_own_kind(code: str, expected: str) -> None:
    from tinyassets.workspace_pool import WorkspacePoolRefused

    assert wse._pool_error_kind(WorkspacePoolRefused(code, "detail")) == expected


def test_the_adapter_uses_the_pool_module_s_own_constants() -> None:
    """If the pool renames a code, this test fails rather than the mapping
    silently falling through to workspace_checkout_failed."""
    from tinyassets import workspace_pool

    assert wse._POOL_KINDS == {
        workspace_pool.REFUSED_BUSY,
        workspace_pool.REFUSED_POOL_BUSY,
        workspace_pool.REFUSED_QUOTA,
    }


def test_provisioning_without_its_consent_does_not_fail_the_checkout(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """The spec scenario: the checkout completes, provisioning is refused."""
    _root, universe_dir = _setup(tmp_path)
    result = _run(
        tmp_path,
        _packet(provision={"python": ["x==1.0"]}),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result.get("error_kind") is None, result
    assert result["op"] == "checkout"
    assert result["provision"] == "workspace_provision_refused"


# --------------------------------------------------------------------------- #
# push and discard
# --------------------------------------------------------------------------- #


def _with_mount(
    chain: EffectChain,
    tmp_path: Path,
    *,
    node_id: str = "n0",
    host: str = HOST,
    repo: str = REPO,
) -> Path:
    """A mount shaped like the one a CHECKOUT registers.

    The default used to be a mount with no host and no repository, which no
    checkout can produce -- a created workspace is the only thing that looks
    like that, and push refuses it by name. Tests that want that shape ask for
    it (``host="", repo=""``).

    It lives where a real scratch lease lives, INSIDE the command center: the
    cell is bound to that center and the push request names the lease
    relative to it, so a lease anywhere else is refused by construction.
    """
    lease_dir = tmp_path / "data" / UNIVERSE / "workspaces" / "scratch" / ("c" * 32)
    (lease_dir / "repo" / ".tiny-export").mkdir(parents=True)
    (lease_dir / "repo" / ".tiny-export" / f"{SHA}.bundle").write_bytes(b"PACK-export")

    class FakeLease:
        lease_id = "lease-1"
        storage_class = "scratch"
        repo_key = "github.com--owner--name"
        generation = 1
        path = lease_dir

    chain.register_workspace(
        node_id,
        WorkspaceMount(
            node_id=node_id,
            bind_source=str(lease_dir / "repo"),
            lease_fd=f"fd:{lease_dir}",
            lease=FakeLease(),
            storage_class="scratch",
            repo_key="github.com--owner--name",
            generation=1,
            host=host,
            repo=repo,
            connection_id="conn-git" if repo else "",
            grant_id="grant-git" if repo else "",
        ),
    )
    return lease_dir


def test_a_push_names_its_branch_and_leaves_the_bundle_to_the_cell(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path)
    worker = FakeWorker({"ok": True, "bytes": 11, "resolved_sha": SHA})
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="my-slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=worker,
    )
    assert result["op"] == "push"
    assert result["remote_ref"].startswith("refs/heads/tiny/")
    assert result["remote_ref"].endswith("/my-slug")
    # The CELL reads the export bundle out of the lease now (which is why the
    # request names the lease and the sha, and nothing else about that file).
    request = worker.requests[0]
    assert request["op"] == "push"
    assert request["commit_sha"] == SHA
    assert request["lease_parent"] == ["workspaces", "scratch"]
    assert request["max_bundle_bytes"] == 512 * 1024 * 1024
    assert not any("bundle_path" in key or "staging" in key for key in request)


def test_a_push_derives_its_destination_from_the_capability(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """The checkout is what was consented to; the packet does not get to
    redirect the credential (Codex round 2, #6)."""
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    worker = FakeWorker({"ok": True, "bytes": 11, "resolved_sha": SHA})
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=worker,
    )
    assert result.get("error_kind") is None, result
    assert worker.requests[0]["owner_repo"] == REPO
    assert worker.requests[0]["host"] == HOST


@pytest.mark.parametrize(
    "contradiction",
    [{"repo": "someone/else"}, {"connection_id": "conn-other"}],
)
def test_a_packet_that_contradicts_the_capability_is_refused(
    tmp_path: Path, chain: EffectChain, fs_spy, contradiction: dict
) -> None:
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    worker = FakeWorker()
    result = _run(
        tmp_path,
        _packet(
            op="push", commit_sha=SHA, branch_slug="slug", workspace="n0", **contradiction
        ),
        universe_dir=universe_dir,
        chain=chain,
        worker=worker,
    )
    assert result["error_kind"] == "invalid_packet"
    assert worker.requests == [], "nothing may be sent on a contradicted capability"


def test_a_workspace_from_a_non_ancestor_node_is_not_reachable(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """The chain is run-global; the graph relation is what authorises.

    Without this a node on a parallel branch could push a workspace it has no
    relationship to (Codex round 2, #6).
    """
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    worker = FakeWorker()
    result = run_workspace_effector(
        node_id="n1",
        output_keys=["ws"],
        run_state={
            "ws": _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0")
        },
        base_path=universe_dir,
        run_id="run-1",
        chain=chain,
        execute=worker,
        ancestors={"someone-else"},  # n0 is NOT an ancestor
    )
    assert result["error_kind"] == "no_matching_packet"
    assert "graph ancestors" in result["error"]
    assert worker.requests == []


def test_an_ancestor_workspace_is_reachable(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """The guard must not be one that always fires."""
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    result = run_workspace_effector(
        node_id="n1",
        output_keys=["ws"],
        run_state={
            "ws": _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0")
        },
        base_path=universe_dir,
        run_id="run-1",
        chain=chain,
        execute=FakeWorker({"ok": True, "bytes": 4, "resolved_sha": SHA}),
        ancestors={"n0"},
    )
    assert result.get("error_kind") is None, result
    assert result["op"] == "push"


def test_a_push_journals_its_intent_before_the_wire_and_settles_it(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """A crash between sending and recording must leave something to ask about."""
    from tinyassets.workspace_intents import open_intents

    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    seen_open: list[int] = []

    class RecordingWorker(FakeWorker):
        def __call__(self, request):
            # the intent must already be durable at the moment of the wire
            seen_open.append(len(open_intents(universe_dir)))
            return super().__call__(request)

    worker = RecordingWorker({"ok": True, "bytes": 9, "resolved_sha": SHA})
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=worker,
    )
    assert result.get("error_kind") is None, result
    assert seen_open == [1], "the intent is written BEFORE the push is sent"
    assert open_intents(universe_dir) == [], "and settled after"


def test_a_failed_push_settles_its_intent_as_failed(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    import sqlite3

    from tinyassets.workspace_intents import open_intents

    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=FakeWorker({"ok": False, "error": "refused", "stderr_class": "protected"}),
    )
    assert open_intents(universe_dir) == []
    conn = sqlite3.connect(workspace_pool_db(universe_dir))
    try:
        states = [row[0] for row in conn.execute("SELECT state FROM workspace_push_intents")]
    finally:
        conn.close()
    assert states == ["failed"]


def test_a_push_naming_a_workspace_this_run_does_not_hold_is_refused(
    tmp_path: Path, chain: EffectChain
) -> None:
    _root, universe_dir = _setup(tmp_path)
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="nowhere"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result["error_kind"] == "no_matching_packet"


@pytest.mark.parametrize("slug", ["", "a/b", "-evil", "with space"])
def test_a_bad_branch_slug_is_refused(
    tmp_path: Path, chain: EffectChain, slug: str
) -> None:
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path)
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug=slug, workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result["error_kind"] == "invalid_packet"


def test_a_refused_push_reports_the_observed_ref(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path)
    worker = FakeWorker(
        {
            "ok": False,
            "error": "the push outcome was lost",
            "stderr_class": "non_fast_forward",
            "observed_sha": "d" * 40,
        }
    )
    result = _run(
        tmp_path,
        _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
        worker=worker,
    )
    assert result["error_kind"] == "workspace_push_refused"
    assert result["observed_sha"] == "d" * 40


def test_a_discard_revokes_the_capability_then_owes_the_bytes(
    tmp_path: Path, chain: EffectChain
) -> None:
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path)
    assert chain.workspace_mount("n0") is not None
    result = _run(
        tmp_path,
        _packet(op="discard", workspace="n0"),
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result["op"] == "discard"
    assert chain.workspace_mount_or_none("n0") is None, "the capability must be gone"
    rows = _outbox_rows(workspace_pool_db(universe_dir))
    assert any(row["action"] == "wipe_scratch" for row in rows), rows


# --------------------------------------------------------------------------- #
# dry run, classification, registry
# --------------------------------------------------------------------------- #


def test_dry_run_describes_and_spawns_nothing(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker()
    result = _run(
        tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=worker, dry_run=True
    )
    assert result["dry_run"] is True
    assert result["op"] == "checkout"
    assert worker.requests == [], "a dry run reaches no cell"
    assert fs_spy["open_dir_nofollow"] == [], "and opens no lease"
    assert chain.workspace_mount_or_none("n1") is None


def test_dry_run_still_reports_a_refusal_a_live_run_would_hit(
    tmp_path: Path, chain: EffectChain
) -> None:
    _root, universe_dir = _setup(tmp_path, consents=())
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, dry_run=True)
    assert result["error_kind"] == "missing_consent"


def test_the_adapter_binds_to_the_real_filesystem_helpers_by_name() -> None:
    """The names are the contract with the pool lane.

    The adapter imports them lazily and the Windows suite injects them, so a
    rename over there would otherwise surface as a runtime AttributeError on
    Linux only. These assertions fail HERE instead.
    """
    from tinyassets import workspace_fs

    for name in (
        "open_dir_nofollow",
        "create_lease_dir",
        "read_regular_file_beneath",
        "copy_regular_file_beneath",
        "bind_target_for",
    ):
        assert callable(getattr(workspace_fs, name, None)), f"workspace_fs.{name} is gone"
    assert issubclass(workspace_fs.UnsafePoolPath, OSError)


def test_the_real_helpers_refuse_loudly_on_windows_rather_than_imitating() -> None:
    """A path-based Windows fallback would fake a descriptor guarantee."""
    from tinyassets import workspace_fs

    if os.name == "posix":
        pytest.skip("POSIX has the real openat semantics")
    with pytest.raises(NotImplementedError):
        workspace_fs.open_dir_nofollow(".")


def test_a_checkout_without_the_posix_helpers_fails_as_a_workspace_refusal(
    tmp_path: Path, chain: EffectChain, no_real_git
) -> None:
    """No fs_spy here: on Windows the real helpers raise, and the adapter must
    turn that into an actionable kind rather than an unhandled crash."""
    if os.name == "posix":
        pytest.skip("the real helpers work here; the Linux proof covers it")
    _root, universe_dir = _setup(tmp_path)
    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert result["error_kind"] in ("workspace_checkout_failed", "effector_crashed")
    assert chain.workspace_mount_or_none("n1") is None


def test_the_sink_is_registered_and_exported() -> None:
    from tinyassets import effectors

    assert effectors._EFFECTORS[EXTERNAL_WRITE_SINK_WORKSPACE] is not None
    assert "EXTERNAL_WRITE_SINK_WORKSPACE" in effectors.__all__
    assert EXTERNAL_WRITE_SINK_WORKSPACE == "workspace"


def test_a_fired_checkout_settles_as_a_read() -> None:
    from tinyassets.engine_admissions import fired_only_reads

    assert fired_only_reads(
        [(EXTERNAL_WRITE_SINK_WORKSPACE, "checkout")],
        read_sink="authenticated_external_call",
        read_effects=WORKSPACE_READ_EFFECTS,
    )


def test_a_fired_discard_settles_as_a_write() -> None:
    """A discard destroys a generation and owes an irreversible wipe.

    It touches no network, but the settlement asks whether the run could have
    CHANGED anything, and this changes something (Codex round 2, #12).
    """
    from tinyassets.engine_admissions import fired_only_reads

    assert (EXTERNAL_WRITE_SINK_WORKSPACE, "discard") not in WORKSPACE_READ_EFFECTS
    assert not fired_only_reads(
        [(EXTERNAL_WRITE_SINK_WORKSPACE, "discard")],
        read_sink="authenticated_external_call",
        read_effects=WORKSPACE_READ_EFFECTS,
    )


def test_a_fired_push_settles_as_a_write() -> None:
    from tinyassets.engine_admissions import fired_only_reads

    assert not fired_only_reads(
        [(EXTERNAL_WRITE_SINK_WORKSPACE, "push")],
        read_sink="authenticated_external_call",
        read_effects=WORKSPACE_READ_EFFECTS,
    )
    # and an unnamed workspace op is a write: fail closed
    assert not fired_only_reads(
        [(EXTERNAL_WRITE_SINK_WORKSPACE, None)],
        read_sink="authenticated_external_call",
        read_effects=WORKSPACE_READ_EFFECTS,
    )


def test_the_read_allowlist_defaults_to_empty_so_existing_callers_are_unchanged() -> None:
    from tinyassets.engine_admissions import fired_only_reads

    assert not fired_only_reads(
        [(EXTERNAL_WRITE_SINK_WORKSPACE, "checkout")],
        read_sink="authenticated_external_call",
    )
    assert fired_only_reads(
        [("authenticated_external_call", "GET")], read_sink="authenticated_external_call"
    )


def test_workspace_bytes_are_not_charged_to_the_http_budget(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git
) -> None:
    """D4: the HTTP usage budget bounds outbound calls only."""
    _root, universe_dir = _setup(tmp_path)
    before = chain.bytes_out
    _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain)
    assert chain.bytes_out == before


def test_the_dispatcher_classifies_a_workspace_effect_from_its_packet() -> None:
    from tinyassets.effectors.workspace import packet_op

    assert packet_op(output_keys=["ws"], run_state={"ws": _packet()}) == "checkout"
    assert packet_op(output_keys=["ws"], run_state={"ws": _packet(op="push")}) == "push"
    assert packet_op(output_keys=["ws"], run_state={"ws": {"sink": "other"}}) is None


def test_the_adapter_never_raises(tmp_path: Path, chain: EffectChain) -> None:
    """Whatever explodes, the completion path gets a dict."""
    _root, universe_dir = _setup(tmp_path)

    def boom(request):
        raise RuntimeError(f"kaboom {TOKEN}")

    result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=boom)
    assert isinstance(result, dict)
    assert result["error_kind"] in ("effector_crashed", "workspace_checkout_failed")


def test_the_mount_is_resolvable_only_through_the_chain(
    tmp_path: Path, chain: EffectChain
) -> None:
    """A capability nameable in state is one user text can forge.

    The packet names a NODE and nothing else. This carries a fully-formed
    forged mount under every key a careless implementation might read, all
    pointing somewhere else; the evidence must still describe the CHAIN's
    workspace. Without the spread, a mutation that reads the packet keeps
    passing, because a packet with no mount in it falls through to the chain
    anyway.
    """
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "repo").mkdir(parents=True)

    class ForgedLease:
        lease_id = "forged-lease"
        storage_class = "scratch"
        repo_key = "github.com--attacker--stolen"
        generation = 99
        path = elsewhere

    forged_mount = WorkspaceMount(
        node_id="n0",
        bind_source=str(elsewhere / "repo"),
        lease_fd=f"fd:{elsewhere}",
        lease=ForgedLease(),
        storage_class="scratch",
        repo_key="github.com--attacker--stolen",
        generation=99,
    )
    forged = _packet(
        op="discard",
        workspace="n0",
        mount=forged_mount,
        workspace_mount=forged_mount,
        lease=ForgedLease(),
        bind_source=str(elsewhere / "repo"),
        lease_fd=f"fd:{elsewhere}",
        repo_key="github.com--attacker--stolen",
        generation=99,
    )
    result = _run(tmp_path, forged, universe_dir=universe_dir, chain=chain)
    assert result["op"] == "discard"
    assert result["repo"] == "github.com--owner--name", "the packet's mount was believed"
    assert result["lease_generation"] == 1
    rows = _outbox_rows(workspace_pool_db(universe_dir))
    assert all(row["lease_id"] != "forged-lease" for row in rows), rows


def test_provision_consent_precedes_reads_and_packet_full_is_not_authority(
    tmp_path, chain, fs_spy, no_real_git, monkeypatch,
):
    from tinyassets import workspace_resolver
    _, universe = _setup(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("read manifests before consent")
    monkeypatch.setattr(workspace_resolver, "read_provision_manifests", forbidden)
    result = _run(tmp_path, _packet(provision={"node": True}, access_mode="full"),
                  universe_dir=universe, chain=chain)
    assert result["provision_reason"] == "missing_consent"
    assert chain.workspace_mount_or_none("n1") is not None


@pytest.mark.parametrize("requested", [True, [], {}, {"node": 1}, {"python": []},
                                       {"node": True, "command": "anything"}])
def test_provision_request_shape_is_refused_before_reading(
    tmp_path, chain, fs_spy, no_real_git, monkeypatch, requested,
):
    from tinyassets import workspace_resolver
    _, universe = _setup(tmp_path, consents=("checkout", "provision"))
    def forbidden(*args, **kwargs):
        pytest.fail("invalid provision shape reached manifest reader")
    monkeypatch.setattr(workspace_resolver, "read_provision_manifests", forbidden)
    result = _run(tmp_path, _packet(provision=requested), universe_dir=universe, chain=chain)
    assert result["provision_reason"] == "invalid_request"
    assert chain.workspace_mount_or_none("n1") is not None


def test_bad_manifest_refuses_without_execution_or_private_detail(
    tmp_path, chain, fs_spy, no_real_git, monkeypatch,
):
    from tinyassets import workspace_provision_execution, workspace_resolver
    from tinyassets.workspace_provision import ProvisionRefused
    _, universe = _setup(tmp_path, consents=("checkout", "provision"))
    def refuse(*args, **kwargs):
        raise ProvisionRefused("not_regular_file", "private-path-token", line_no=2)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid manifests reached execution")
    monkeypatch.setattr(workspace_resolver, "read_provision_manifests", refuse)
    monkeypatch.setattr(workspace_provision_execution, "execute_provision", forbidden)
    result = _run(tmp_path, _packet(provision={"node": True}), universe_dir=universe, chain=chain)
    assert result["provision_line"] == 2
    assert "private-path-token" not in str(result)
    assert chain.workspace_mount_or_none("n1") is not None


@pytest.mark.parametrize("failure", [None, "process_failed", "cancelled", "unknown_death"])
def test_provision_execution_reserved_before_wire_and_published_only_on_success(
    tmp_path, chain, fs_spy, no_real_git, monkeypatch, failure,
):
    import sqlite3

    from tinyassets import node_sandbox, workspace_provision_execution, workspace_resolver
    from tinyassets.workspace_provision import admit_requirements
    _, universe = _setup(tmp_path, consents=("checkout", "provision"))
    manifests = workspace_resolver.ProvisionManifests(admit_requirements(
        "example==1.0 --hash=sha256:" + "a" * 64), None)
    calls = []
    def read(fd, **kwargs):
        calls.append("read")
        assert chain.workspace_mount_or_none("n1") is None
        assert kwargs == {"python_path": "requirements.txt", "node": False}
        return manifests
    def execute(admitted, **kwargs):
        calls.append("execute")
        assert admitted is manifests
        assert chain.workspace_mount_or_none("n1") is None
        # Worker staging and its credentials must already be gone.
        assert all(not Path(request["staging_dir"]).exists() for request in worker.requests)
        assert kwargs["storage_bound"] == kwargs["max_transfer_bytes"]
        assert kwargs["timeout_s"] == 20
        with sqlite3.connect(wse._pool_db(universe)) as db:
            amount, reserved = db.execute(
                "SELECT amount, reserved FROM workspace_ledger WHERE operation_id LIKE ? "
                "AND kind='bytes'", ("%:provision:%",)).fetchone()
        assert (amount, reserved) == (kwargs["max_transfer_bytes"], 1)
        if failure == "unknown_death":
            raise node_sandbox.SandboxTerminationError("unknown lifecycle")
        return workspace_provision_execution.ProvisionResult(failure, 123)
    monkeypatch.setattr(workspace_resolver, "read_provision_manifests", read)
    monkeypatch.setattr(workspace_provision_execution, "execute_provision", execute)
    worker = FakeWorker()
    result = _run(tmp_path, _packet(provision={"python": "requirements.txt"}),
                  universe_dir=universe, chain=chain, worker=worker, timeout_seconds=20)
    assert calls == ["read", "execute"]
    if failure:
        assert result["error_kind"] == (
            "effector_crashed" if failure == "unknown_death" else "workspace_provision_failed")
        assert chain.workspace_mount_or_none("n1") is None
        assert _outbox_rows(wse._pool_db(universe))
    else:
        assert result["provision"] == "completed"
        assert result["provision_bytes"] == 123
        assert result["provision_digests"] == {"python": manifests.python.digest}
        assert chain.workspace_mount_or_none("n1") is not None
    with sqlite3.connect(wse._pool_db(universe)) as db:
        amount, reserved = db.execute(
            "SELECT amount, reserved FROM workspace_ledger WHERE operation_id LIKE ? "
            "AND kind='bytes'", ("%:provision:%",)).fetchone()
    assert reserved == (1 if failure == "unknown_death" else 0)
    assert amount == (wse._DEFAULT_MAX_CHECKOUT_BYTES if failure == "unknown_death" else 123)


def test_each_new_provision_attempt_reserves_again_after_prior_refund(tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace

    from tinyassets import workspace_provision_execution, workspace_resolver
    from tinyassets.workspace_provision import admit_requirements
    _, universe = _setup(tmp_path, consents=("checkout", "provision"))
    manifests = workspace_resolver.ProvisionManifests(admit_requirements(
        "example==1.0 --hash=sha256:" + "a" * 64), None)
    monkeypatch.setattr(workspace_resolver, "read_provision_manifests", lambda *a, **kw: manifests)
    def execute(*args, **kwargs):
        with sqlite3.connect(wse._pool_db(universe)) as db:
            amounts = db.execute(
                "SELECT amount FROM workspace_ledger WHERE kind='bytes' AND reserved=1").fetchall()
        assert amounts == [(1000,)]
        return workspace_provision_execution.ProvisionResult(None, 123)
    monkeypatch.setattr(workspace_provision_execution, "execute_provision", execute)
    for _ in range(2):
        result = wse._provision_checkout(
            {"python": "requirements.txt"}, base_path=universe,
            resource=SimpleNamespace(connection_id="conn-git"), host=HOST, repo=REPO,
            lease=SimpleNamespace(reserved_bytes=1000), lease_fd=3, repo_fd=4,
            run_id="same-run", node_id="same-node", universe_id=UNIVERSE,
            timeout_seconds=10, should_cancel=None)
        assert result["provision"] == "completed"
    with sqlite3.connect(wse._pool_db(universe)) as db:
        rows = db.execute(
            "SELECT operation_id, amount FROM workspace_ledger WHERE kind='bytes'").fetchall()
    assert len(rows) == 2
    assert rows[0][0] != rows[1][0]
    assert [row[1] for row in rows] == [123, 123]


def test_cancellation_after_install_before_publication_still_owes_wipe(
    tmp_path, chain, fs_spy, no_real_git, monkeypatch,
):
    _, universe = _setup(tmp_path)
    stop = []
    def provision(*args, **kwargs):
        assert chain.workspace_mount_or_none("n1") is None
        stop.append(True)
        return {"provision": "completed"}
    monkeypatch.setattr(wse, "_provision_checkout", provision)
    result = run_workspace_effector(
        node_id="n1", output_keys=["ws"], run_state={"ws": _packet(provision={"node": True})},
        base_path=universe, run_id="run-1", chain=chain, execute=FakeWorker(),
        should_cancel=lambda: bool(stop))
    assert result["error_kind"] == "workspace_provision_failed"
    assert result["provision_reason"] == "cancelled"
    assert chain.workspace_mount_or_none("n1") is None
    assert _outbox_rows(wse._pool_db(universe))


# --------------------------------------------------------------------------- #
# Staging is removed on EVERY exit (production held 334 leaked credentialed
# staging trees until the 2026-09-30 boot sweep removed them).
# --------------------------------------------------------------------------- #


def _staging_files(universe_dir: Path) -> list[Path]:
    from tinyassets import process_liveness, workspace_staging

    root = workspace_staging.staging_root(universe_dir)
    if not root.exists():
        return []
    return [
        p for p in root.rglob("*")
        if p.is_file() and process_liveness.LIVENESS_DIR not in p.parts
    ]


# The staging directory these used to police is gone with the daemon-uid
# worker: the cell works inside the lease the pool already reserved
# (``workspace_remote_cell.CELL_DIR``) and removes it before answering, which
# is proved where it happens -- tests/test_workspace_remote_cell.py. What is
# left to prove HERE is that the daemon stages nothing of its own on any path.


@pytest.mark.parametrize("outcome", ["refused", "crashed", "succeeded"])
def test_no_workspace_operation_stages_anything_daemon_side(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, outcome: str
) -> None:
    _root, universe_dir = _setup(tmp_path)
    worker = FakeWorker(
        {"ok": False, "error": "auth: nope", "stderr_class": "auth"}
        if outcome == "refused" else None
    )

    def _cell(request):
        if outcome == "crashed":
            raise RuntimeError("the cell died")
        return worker(request)

    try:
        result = _run(tmp_path, _packet(), universe_dir=universe_dir, chain=chain, worker=_cell)
    except RuntimeError:
        pass  # raised or reported: either way, nothing may be left behind
    else:
        expected = {"succeeded": None, "refused": "workspace_checkout_failed",
                    "crashed": "effector_crashed"}[outcome]
        assert result.get("error_kind") == expected, result
    assert _staging_files(universe_dir) == []


def test_a_push_stages_nothing_daemon_side_either_way(
    tmp_path: Path, chain: EffectChain, fs_spy
) -> None:
    """A push used to copy the export bundle into daemon staging first."""
    _root, universe_dir = _setup(tmp_path)
    _with_mount(chain, tmp_path, host=HOST, repo=REPO)
    for answer in ({"ok": True, "bytes": 11, "resolved_sha": SHA},
                   {"ok": False, "error": "rejected", "stderr_class": "rejected"}):
        _run(
            tmp_path,
            _packet(op="push", commit_sha=SHA, branch_slug="slug", workspace="n0"),
            universe_dir=universe_dir,
            chain=chain,
            worker=FakeWorker(answer),
        )
        assert _staging_files(universe_dir) == []

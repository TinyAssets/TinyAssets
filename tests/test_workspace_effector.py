"""Tests for the ``workspace`` effect sink (``tinyassets.effectors.workspace``).

Real connection ledger, real grant, real consent store, real pool database. The
worker is injected (no spawn, no git, no network) and the pool lane's directory
handles are injected too -- they land on ``claude/workspace-pool``, and a test
that asserts THEY were called is what makes the seam real before the merge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tinyassets.effectors import EffectChain, WorkspaceMount
from tinyassets.effectors import workspace as wse
from tinyassets.effectors.workspace import (
    EXTERNAL_WRITE_SINK_WORKSPACE,
)
from tinyassets.effectors.workspace import (
    run_workspace_effector as _run_workspace_effector,
)
from tinyassets.storage.outbound_connections import ConnectionLedger
from tinyassets.storage.workspace_authority import workspace_consent_destination

UNIVERSE = "universe-1"
REPO = "owner/name"
HOST = "github.com"
SHA = "c" * 40
TOKEN = "ghp_EFFECTORTOKEN0123456789ABCDEFGHI"


@pytest.fixture(autouse=True)
def _collect_prior_run_allocations(in_process_broker):
    # In production the broker cannot collect a daemon's run allocations.
    # Here it shares the process: collect prior tests' cyclic allocations on
    # the test thread, so a finalizer's synchronous accounting RPC cannot
    # block the broker loop that must answer the workspace's next request.
    import gc

    gc.collect()


# --------------------------------------------------------------------------- #
# Scaffolding
# --------------------------------------------------------------------------- #


def run_workspace_effector(**kwargs: Any) -> dict[str, Any]:
    """Dispatch inside the admitted owner-box session, as a production run does.

    Every owner read the sink makes (the lease stat, the generation measure)
    goes through the session binding; with no ``base_path`` there is no center
    to admit, and the sink must refuse on its own.
    """
    from tinyassets.control_plane import binding

    base_path = kwargs.get("base_path")
    if base_path is None:
        return _run_workspace_effector(**kwargs)
    with binding.use_binding(binding.file_binding(Path(base_path))):
        return _run_workspace_effector(**kwargs)


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
    grant_owner=None,
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
        owner_user_id=grant_owner or principal,
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
        owner_user_id=grant_owner or principal,

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


def _host(universe_dir: Path, guest: str | Path) -> Path:
    """The host directory a guest lease path (``/cc/<center>/...``) names.

    On the box double the command center directory IS the box, so the guest
    path's center-relative part is the same path under the test's universe.
    """
    return Path(universe_dir) / Path(guest).relative_to(wse._lease_center_root(universe_dir))


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




@pytest.fixture()
def fs_spy(monkeypatch: pytest.MonkeyPatch, _owner_box_double):
    """Spy on the owner-box stats the DAEMON side makes to pick up the lease.

    The daemon creates nothing: the cell makes the lease and its repository,
    and the daemon only CHECKS them through the admitted box handle
    (``_open_cell_lease``), which resolves every component no-follow on the
    host. Recording those stats is what proves the lease is picked up through
    the box rather than re-resolved by a host path.
    """
    assert _owner_box_double is not None, "the owner box double needs Linux"
    calls: dict[str, list] = {"stat": []}
    from tinyassets.boxes.boxd import OwnerBoxd

    real_stat = OwnerBoxd.stat

    def stat(core, handle, path, *args, **kwargs):
        calls["stat"].append(path)
        return real_stat(core, handle, path, *args, **kwargs)

    monkeypatch.setattr(OwnerBoxd, "stat", stat)
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
















# --------------------------------------------------------------------------- #
# Authority: grant, scope, consent
# --------------------------------------------------------------------------- #
































# --------------------------------------------------------------------------- #
# checkout
# --------------------------------------------------------------------------- #






































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
    The pool names it by its GUEST path (``/cc/<center>/...``); on the box
    double that is the same directory under the test's data root.
    """
    relative = Path("workspaces") / "scratch" / ("c" * 32)
    lease_dir = tmp_path / "data" / UNIVERSE / relative
    guest_dir = wse._lease_center_root(tmp_path / "data" / UNIVERSE) / relative
    (lease_dir / "repo" / ".tiny-export").mkdir(parents=True)
    (lease_dir / "repo" / ".tiny-export" / f"{SHA}.bundle").write_bytes(b"PACK-export")

    class FakeLease:
        lease_id = "lease-1"
        storage_class = "scratch"
        repo_key = "github.com--owner--name"
        generation = 1
        path = guest_dir

    chain.register_workspace(
        node_id,
        WorkspaceMount(
            node_id=node_id,
            bind_source=str(guest_dir / "repo"),
            lease_fd=None,
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
























# --------------------------------------------------------------------------- #
# dry run, classification, registry
# --------------------------------------------------------------------------- #










































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





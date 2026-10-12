"""Negative regressions for cloud-only runtime admission (change task 9).

Test-matrix rows 1, 2 and the bounded 4/5 pair from
`openspec/changes/cloud-only-runtime-admission/design.md`. These are written
against the **unfixed** tree on purpose: the negatives assert the refusal that
tasks 6-7 are supposed to add, so they fail here at the missing-refusal
assertion and pass once admission is actually wired. The positives assert
preserved behaviour and pass at baseline, which is what proves the negatives
are refusals rather than broken setup.

Nothing here is enforcement, and nothing here is production authority. Every
case runs on a temp data root with a fixture DB and an injected resolver
(`design.md` § Local isolated tests vs production authority): a green run
establishes no custody, no cloud fact and no live claim. The resolver is
injected through the existing `ProcessProvenanceObservation` seam — no
environment bypass, no tests-only production helper, and no production edit.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import tinyassets.platform_runtime_provenance as provenance
from tests.test_branch_tasks_v2 import _activation_subject, _commit, _MutableClock
from tinyassets.branch_tasks_v2 import AssignedConsumerLease, Epoch2BranchTaskAdapter
from tinyassets.daemon_registry import create_daemon, ensure_daemon_runtime
from tinyassets.daemon_server import initialize_author_server, list_runtime_instances
from tinyassets.platform_runtime_provenance import (
    CLOUD,
    NOT_CLOUD,
    ProcessProvenanceObservation,
    RuntimeProvenance,
)
from tinyassets.storage import db_path
from tinyassets.storage.automation_activations import (
    AutomationActivationExecutor,
    AutomationActivationStore,
)

NOW = "2026-07-24T08:01:00+00:00"

UNADMITTED = RuntimeProvenance(
    verdict=NOT_CLOUD,
    reason="metadata_unreachable",
    metadata_reachable=False,
    expected_identity_prepared=False,
)
ADMITTED = RuntimeProvenance(
    verdict=CLOUD,
    reason="instance_match",
    metadata_reachable=True,
    expected_identity_prepared=True,
)


@pytest.fixture
def bind_provenance(monkeypatch):
    """Install one process observation carrying an injected verdict.

    This drives the real seam a runtime guard has to read: the module-level
    `_PROCESS_OBSERVATION` that both `observe_platform_runtime_provenance()`
    and the non-mutating `peek_platform_runtime_provenance()` resolve through.
    The cache is warmed once here so a guard reading either accessor sees the
    same immutable verdict, exactly as a real process does after startup
    resolution. No metadata socket is opened: the resolver is a callable.
    """

    def _bind(verdict: RuntimeProvenance) -> ProcessProvenanceObservation:
        observation = ProcessProvenanceObservation(resolver=lambda: verdict)
        observation.observe()
        monkeypatch.setattr(provenance, "_PROCESS_OBSERVATION", observation)
        return observation

    return _bind


# --- matrix 5: the copied-container label set (shared by every site) -------

#: Non-path environment values `deploy/compose.yml` bakes onto the `daemon`
#: service (read 2026-09-23). These are copied **literally**, because that is
#: exactly what an off-cloud checkout of this repo, a leaked env file or a
#: `docker run` of the published image gets for free.
_COPIED_CONTAINER_ENV = {
    "TINYASSETS_ALLOW_API_KEY_PROVIDERS": "0",
    "TINYASSETS_CLOUD_DAEMON_SUBSCRIPTION_ONLY": "1",
    "TINYASSETS_GOAL_POOL": "off",
    "TINYASSETS_AUTO_SHIP_RUBRIC_MODE": "enforce",
    "TINYASSETS_AUTO_SHIP_TRAJECTORY_MODE": "enforce",
    "TINYASSETS_MCP_CANARY_URL": "https://tinyassets.io/mcp",
    "TINYASSETS_ONBOARDING_APP": "1",
}

#: Container **labels**: the hostname a copied deployment can trivially claim,
#: and the service name. `socket.gethostname`/`getfqdn` are patched too, so the
#: lie is not only in the environment block — a guard that resolved the host
#: itself instead of reading provenance would still be fooled here.
_COPIED_CONTAINER_HOSTNAME = "mcp.tinyassets.io"
_COPIED_CONTAINER_NAME = "tinyassets-daemon"

#: Path-valued container env (`compose.yml`: `HOME=/app`,
#: `TINYASSETS_DATA_DIR=/data`, `CODEX_HOME=/data/.codex`,
#: `CLAUDE_CONFIG_DIR=/data/.claude`,
#: `TINYASSETS_REPO_ROOT=/data/community-pool`), keyed by the path *relative to
#: the data root* so it can be re-rooted. `None` means "the data root itself".
_COPIED_CONTAINER_PATH_ENV = {
    "HOME": None,
    "TINYASSETS_DATA_DIR": None,
    "CODEX_HOME": ".codex",
    "CLAUDE_CONFIG_DIR": ".claude",
    "TINYASSETS_REPO_ROOT": "community-pool",
}


def apply_copied_cloud_labels(monkeypatch, data_root: Path) -> None:
    """Apply the explicit non-secret compose environment and copied labels.

    Paths are deliberately rerooted into isolated pytest storage. This does
    not claim literal /data equivalence or reproduce secret env_file values.
    Admission comes from the injected process observation; paired admitted
    controls establish that these settings do not simply break the fixture.
    No real metadata request, provider child or production authority is used.
    """
    for name, value in _COPIED_CONTAINER_ENV.items():
        monkeypatch.setenv(name, value)
    for name, relative in _COPIED_CONTAINER_PATH_ENV.items():
        target = data_root if relative is None else data_root / relative
        monkeypatch.setenv(name, str(target))
    monkeypatch.setenv("HOSTNAME", _COPIED_CONTAINER_HOSTNAME)
    monkeypatch.setenv("TINYASSETS_CONTAINER", _COPIED_CONTAINER_NAME)
    monkeypatch.setenv("CONTAINER_NAME", _COPIED_CONTAINER_NAME)
    monkeypatch.setattr("socket.gethostname", lambda: _COPIED_CONTAINER_HOSTNAME)
    monkeypatch.setattr("socket.getfqdn", lambda *_a: _COPIED_CONTAINER_HOSTNAME)


#: Parameters for a positive control that must hold **both** bare and dressed.
#: Every admitted control below runs twice: once as a plain process, once
#: wearing the full copied-container label set. The `copied_labels` leg is the
#: positive half of matrix row 5 — it proves the label set is inert in the
#: admitted direction too, so the row-5 negatives are refusing on the injected
#: verdict rather than on some incidental breakage the labels introduce (a
#: re-rooted `HOME`, an unresolvable `CODEX_HOME`). Without it, every row-5
#: negative would be consistent with "the labels broke the fixture".
COPIED_LABEL_VARIANTS = [
    pytest.param(False, id="plain"),
    pytest.param(True, id="copied_labels"),
]


def maybe_copied_cloud_labels(copied_labels: bool, monkeypatch, data_root: Path) -> None:
    """Apply the copied-container label set only on the dressed leg."""
    if copied_labels:
        apply_copied_cloud_labels(monkeypatch, data_root)


def _ready_cloud_assignment(tmp_path: Path):
    """A valid, ready, pending `cloud` epoch-2 task plus a valid consumer lease.

    Same shape as the baseline single-winner claim test in
    `tests/test_branch_tasks_v2.py`, so the setup itself is known-claimable.
    """
    initialize_author_server(tmp_path)
    activations = AutomationActivationStore(tmp_path)
    active = activations.activate(
        expected=activations.create_stopped(
            universe_id="universe-a", automation_id="automation-a"
        ),
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_activation_subject("branch-version-a"),
        lease_id="activation-lease-a",
    )
    assert active is not None
    committed = _commit(tmp_path, automation_activation=active)
    adapter = Epoch2BranchTaskAdapter(tmp_path, clock=_MutableClock(NOW))
    candidate = adapter.get(committed["branch_task_id"])
    assert candidate is not None
    assert candidate.automation_executor_class == "cloud"
    lease = AssignedConsumerLease(
        consumer_id="assigned-consumer:boot-a",
        lease_id="lease-a",
        expires_at="2026-07-24T08:02:00+00:00",
    )
    return adapter, candidate, lease


def _claimed_row(tmp_path: Path, branch_task_id: str) -> tuple[str, str]:
    """Read the persisted claim state, not the return value."""
    with sqlite3.connect(db_path(tmp_path)) as conn:
        row = conn.execute(
            "SELECT status, COALESCE(claimed_by, ?) FROM branch_tasks_v2 "
            "WHERE branch_task_id = ?",
            ("", branch_task_id),
        ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def _cloud_worker_rows(tmp_path: Path) -> list[dict]:
    return [
        row
        for row in list_runtime_instances(tmp_path)
        if (row.get("metadata") or {}).get("runtime_registration") == "cloud_worker"
    ]


def _cloud_registration_args(tmp_path: Path, *, worker_id: str = "worker-cloud-1"):
    initialize_author_server(tmp_path)
    daemon = create_daemon(
        tmp_path,
        display_name="Admission regression daemon",
        created_by="acct_alice",
        soul_mode="soul",
        soul_text="Own one bounded cloud registration.",
    )
    return dict(
        daemon_id=str(daemon["daemon_id"]),
        universe_id="universe-a",
        provider_name="codex",
        model_name="gpt-5",
        created_by="cloud-worker",
        worker_id=worker_id,
        metadata={"automation_executor_class": "cloud"},
    )


def _register_cloud_worker(tmp_path: Path, *, worker_id: str = "worker-cloud-1"):
    return ensure_daemon_runtime(
        tmp_path, **_cloud_registration_args(tmp_path, worker_id=worker_id)
    )


# --- matrix 1: negative direct claim --------------------------------------


def test_unadmitted_direct_claim_assigned_claims_nothing(
    tmp_path: Path, bind_provenance
) -> None:
    """Matrix 1. No `authority_claim` callback is passed — that is the point.

    `transaction_check` returns the non-optional predicate's result unchanged
    when `authority_claim is None` (`branch_tasks_v2.py:489`), so a gate that
    lives only in the optional callback is opt-out by construction and a test
    that supplies one proves nothing. The setup is a valid ready assignment
    with a valid unexpired consumer lease; the only thing wrong with this
    process is that it is not admitted.
    """
    bind_provenance(UNADMITTED)
    adapter, candidate, lease = _ready_cloud_assignment(tmp_path)

    claimed = adapter.claim_assigned(candidate, consumer_lease=lease)

    status, claimed_by = _claimed_row(tmp_path, candidate.branch_task_id)
    assert claimed is None, "unadmitted process claimed an assigned cloud task"
    assert claimed_by == "", f"unadmitted process is recorded as claimer: {claimed_by}"
    assert status == "pending", (
        f"task left {status!r}, not pending, by an unadmitted claim"
    )


@pytest.mark.parametrize("copied_labels", COPIED_LABEL_VARIANTS)
def test_admitted_direct_claim_assigned_still_succeeds(
    copied_labels: bool, tmp_path: Path, bind_provenance, monkeypatch
) -> None:
    """Preserved behaviour, and the control for the negative above.

    Identical fixture, identical call, only the injected verdict differs. If
    this fails, the negative's red is setup breakage rather than a refusal.

    The `copied_labels` leg is additionally the positive control for
    `test_existing_registration_and_cloud_labels_do_not_authorize_claim`: the
    same process dressing that must not authorize an unadmitted claim must
    also not *block* an admitted one. A dressed process that could no longer
    claim would make that negative unreadable — a refusal and a broken fixture
    look identical from the persisted row.
    """
    bind_provenance(ADMITTED)
    maybe_copied_cloud_labels(copied_labels, monkeypatch, tmp_path)
    adapter, candidate, lease = _ready_cloud_assignment(tmp_path)

    claimed = adapter.claim_assigned(candidate, consumer_lease=lease)

    assert claimed is not None
    assert claimed.claimed_by == lease.consumer_id
    status, claimed_by = _claimed_row(tmp_path, candidate.branch_task_id)
    assert claimed_by == lease.consumer_id
    assert status != "pending"


# --- matrix 2: negative registration --------------------------------------


def test_unadmitted_ensure_daemon_runtime_writes_no_cloud_worker_row(
    tmp_path: Path, bind_provenance
) -> None:
    """Matrix 2. Asserted on the persisted row, never on source text.

    A permission refusal or a refusing return is acceptable. An arbitrary
    programming/setup error must fail the test, not count as admission safety.
    After an unadmitted call there must be no cloud-worker registration row.
    """
    bind_provenance(UNADMITTED)
    registration_args = _cloud_registration_args(tmp_path)

    try:
        ensure_daemon_runtime(tmp_path, **registration_args)
    except PermissionError:
        pass

    rows = _cloud_worker_rows(tmp_path)
    assert rows == [], (
        f"unadmitted process registered {len(rows)} cloud_worker runtime row(s)"
    )


def test_admitted_ensure_daemon_runtime_registers_cloud_worker(
    tmp_path: Path, bind_provenance
) -> None:
    """Preserved behaviour: the admitted path still registers."""
    bind_provenance(ADMITTED)

    runtime = _register_cloud_worker(tmp_path)

    assert runtime["metadata"]["runtime_registration"] == "cloud_worker"
    rows = _cloud_worker_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["instance_id"] == runtime["runtime_instance_id"]


# --- matrix 4/5 (bounded): a row and a cloud-looking host are not authority --


def test_existing_registration_and_cloud_labels_do_not_authorize_claim(
    tmp_path: Path, bind_provenance, monkeypatch
) -> None:
    """Matrix 4 + 5, bounded to what a local fixture can honestly show.

    The registration row is written while admitted, then the same data root is
    read by an unadmitted process wearing the full copied-container label set
    (`apply_copied_cloud_labels`): every compose env var, the
    `mcp.tinyassets.io` hostname and the service name. None of those is
    evidence, and an existing row
    is not permission — authority is re-resolved on read (`design.md`
    § Enforcement sites (B)). No real production path is set and no live state
    is touched: the data root is the pytest temp dir throughout.
    """
    bind_provenance(ADMITTED)
    registered = _register_cloud_worker(tmp_path)
    assert registered["metadata"]["runtime_registration"] == "cloud_worker"
    adapter, candidate, lease = _ready_cloud_assignment(tmp_path)

    # Now the same checkout, off cloud, dressed up.
    bind_provenance(UNADMITTED)
    apply_copied_cloud_labels(monkeypatch, tmp_path)

    claimed = adapter.claim_assigned(candidate, consumer_lease=lease)

    status, claimed_by = _claimed_row(tmp_path, candidate.branch_task_id)
    assert claimed is None, (
        "an existing registration plus cloud-looking labels authorized an "
        "unadmitted claim"
    )
    assert claimed_by == ""
    assert status == "pending"
    assert _cloud_worker_rows(tmp_path), "fixture lost the pre-existing registration"


def test_copied_cloud_labels_do_not_authorize_registration(
    tmp_path: Path, bind_provenance, monkeypatch
) -> None:
    """Matrix 5, registration site.

    The same dressed-up process that cannot claim also cannot *register*. This
    is the site row 5 names that row 1's claim case cannot reach: registration
    is where an unadmitted runtime would otherwise mint the row a later read
    could mistake for authority, so a spoof that stops at claim leaves the
    upstream door open.

    Asserted on the persisted row (`_cloud_worker_rows`), never on a return
    value or source text, and the admitted control below is what proves the
    fixture can register at all.
    """
    bind_provenance(UNADMITTED)
    apply_copied_cloud_labels(monkeypatch, tmp_path)
    registration_args = _cloud_registration_args(tmp_path)

    try:
        ensure_daemon_runtime(tmp_path, **registration_args)
    except PermissionError:
        pass

    rows = _cloud_worker_rows(tmp_path)
    assert rows == [], (
        f"a process wearing copied cloud labels registered {len(rows)} "
        "cloud_worker runtime row(s)"
    )


def test_copied_cloud_labels_leave_the_admitted_registration_intact(
    tmp_path: Path, bind_provenance, monkeypatch
) -> None:
    """Control for the negative above: the labels are not what breaks it.

    Identical labels, identical call, admitted verdict. If this failed, the
    negative would be a label-induced setup error rather than a refusal.
    """
    bind_provenance(ADMITTED)
    apply_copied_cloud_labels(monkeypatch, tmp_path)

    runtime = _register_cloud_worker(tmp_path)

    assert runtime["metadata"]["runtime_registration"] == "cloud_worker"
    assert len(_cloud_worker_rows(tmp_path)) == 1

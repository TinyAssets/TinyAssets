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






# --- matrix 2: negative registration --------------------------------------






# --- matrix 4/5 (bounded): a row and a cloud-looking host are not authority --







"""Negative regressions for the two provider lanes that never claim (task 9).

Test-matrix row 3 (`openspec/changes/cloud-only-runtime-admission/design.md`
§ Test matrix) for enforcement site **(C)** — the last provider-authority
boundary. `design.md` § Enforcement sites says this site "carries the whole
foreground/served surface, because those paths never call `claim_assigned` and
stamp the class as a literal (`foreground_run_provider.py:484,595`,
`background_served_provider.py:1336,1547`)". Neither lane traverses
`claim_assigned`, so the row-1/row-2 regressions in
`tests/test_cloud_only_admission_regressions.py` cannot cover them.

Red-before, on purpose: against the unfixed tree both negatives reach the
provider-dispatch fake and fail on the "never invoked" assertion, and both
positives pass. The positives are the discriminator — they run the *same*
fixture, the *same* per-universe authority and the *same* inputs, changing only
the injected verdict, so a negative that goes red is a missing refusal rather
than broken setup.

Nothing here is enforcement and nothing establishes a cloud fact. The verdict
is injected through the existing `ProcessProvenanceObservation` seam
(`platform_runtime_provenance.py` module docstring: "Tests inject a
reader/resolver"), so no metadata socket is opened. Every provider dispatch is
a local callable: the foreground lane runs on the `_CountingProvider` router
and the monkeypatched `text_sync` that `_run_branch` installs, the served
lane on the `raw_provider` callable `_BackgroundAssignedProviderSession` is
constructed with. No listener, no server, no subprocess, no network, no
production credential, and no production edit.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tinyassets.platform_runtime_provenance as provenance
from tests.test_cloud_only_admission_regressions import (
    ADMITTED,
    COPIED_LABEL_VARIANTS,
    UNADMITTED,
    _cloud_registration_args,
    apply_copied_cloud_labels,
    maybe_copied_cloud_labels,
)
from tests.test_cloud_only_admission_regressions import (
    bind_provenance as _bind_provenance_fixture,
)
from tests.test_run_provider_session import _branch, _run_branch
from tinyassets.daemon_registry import (
    ensure_daemon_runtime,
    runtime_matches_worker_provider,
)
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.platform_runtime_provenance import ProcessProvenanceObservation
from tinyassets.storage.provider_work_authority import db_path as authority_db_path

#: The only outcomes that count as a refusal. Anything else — a KeyError, an
#: AttributeError, an ImportError, a TypeError from a half-wired fixture —
#: propagates and fails the test. An arbitrary programming error is not
#: admission safety (same rule as the claim/registration regressions).
REFUSALS = (ProviderAuthorityHeldError, PermissionError)

#: Re-register the row-1/row-2 observation seam under its own name rather than
#: duplicating it. pytest discovers fixtures by module attribute, so the rebind
#: is enough; importing the name directly collides with the test parameters
#: that request it.
bind_provenance = _bind_provenance_fixture


@pytest.fixture
def unobserved_provenance(monkeypatch):
    """Install an observation that has **not** resolved, and never resolves.

    `bind_provenance` warms its cache, which models a process that already ran
    its startup observation. This models the other real state: a process that
    reached an admission site having never resolved at all. The resolver would
    raise if a guard tried to trigger it, so a site that quietly resolves its
    own evidence at the last moment — instead of refusing an unobserved
    process — fails loudly here rather than passing.
    """

    def _bind() -> ProcessProvenanceObservation:
        def _never() -> object:
            raise AssertionError(
                "an admission site resolved provenance from inside a guard"
            )

        observation = ProcessProvenanceObservation(resolver=_never)
        monkeypatch.setattr(provenance, "_PROCESS_OBSERVATION", observation)
        return observation

    _bind()
    return _bind


def _receipt_rows(base_path: Path) -> list[tuple[str, str]]:
    """Persisted foreground run receipts, read from storage rather than a return.

    `executor_class` is not a column — it lives inside the serialized receipt
    record (`storage/provider_work_authority.py`, `record_json`), so the class
    a run was actually stamped with is read out of the stored record.
    """
    with sqlite3.connect(authority_db_path(base_path)) as conn:
        return [
            (str(kind), str(json.loads(record).get("executor_class")))
            for kind, record in conn.execute(
                "SELECT work_item_kind, record_json FROM provider_work_receipts"
            )
        ]


# --- small local helpers (no production helper is introduced) --------------





def _reservations(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM provider_invocation_reservations").fetchone()[0]
    )


# --- lane 1: foreground run (foreground_run_provider.py:484,595) ----------


def test_unadmitted_foreground_run_never_reaches_the_provider(
    tmp_path: Path, monkeypatch, authenticate_request, bind_provenance
) -> None:
    """Matrix 3, foreground half.

    `_run_branch` is the existing vetted foreground harness: a real branch
    definition, a real founder home, real serving-assignment seeding and real
    run execution, with only the executor replaced by `_CountingProvider`.
    The authority here is valid and per-universe — `acct_alice` owns
    `universe_alice`, the branch is authored by `acct_alice`, and the serving
    assignment is seeded through the same path the passing baseline tests use.
    The single thing wrong with this process is that it is not admitted, and
    `_ForegroundRunProviderSession._admit()` stamps `executor_class="cloud"` as
    a literal without consulting any provenance.
    """
    bind_provenance(UNADMITTED)

    response, provider, _captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, _branch(node_count=1)
    )

    assert provider.calls == [], (
        f"unadmitted foreground run dispatched {len(provider.calls)} provider "
        "call(s) before any admission check"
    )
    assert response["terminal_status"] != "completed", (
        "unadmitted foreground run completed a cloud-class turn"
    )
    assert _receipt_rows(tmp_path) == [], (
        "unadmitted foreground run minted a cloud-class provider work receipt"
    )


@pytest.mark.parametrize("copied_labels", COPIED_LABEL_VARIANTS)
def test_admitted_foreground_run_still_launches_and_settles(
    copied_labels: bool, tmp_path: Path, monkeypatch, authenticate_request, bind_provenance
) -> None:
    """Preserved behaviour, and the control for the negative above.

    Identical branch, identical harness, identical authority — only the
    injected verdict differs. If this fails, the negative's red is setup
    breakage rather than a refusal.

    The `copied_labels` leg is the positive control for
    `test_copied_cloud_labels_never_reach_the_foreground_provider`: an empty
    `provider.calls` list is only evidence of a refusal if a dressed *admitted*
    process still fills it.
    """
    bind_provenance(ADMITTED)
    maybe_copied_cloud_labels(copied_labels, monkeypatch, tmp_path)

    response, provider, _captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, _branch(node_count=1)
    )

    assert response["terminal_status"] == "completed", response["terminal_error"]
    assert len(provider.calls) == 1
    assert _receipt_rows(tmp_path) == [("run", "cloud")]


# --- lane 2: background served turn (background_served_provider.py:1336,1547)






# --- the refusal itself: sanitized, and not an arbitrary crash ------------




def test_in_transaction_stamp_refuses_unobserved_without_resolving(
    unobserved_provenance,
) -> None:
    """The in-transaction stamp must refuse "we never looked" without looking.

    `admitted_cloud_executor_class()` is what both lanes call where the literal
    `executor_class="cloud"` used to be, and both call sites hold an open
    `BEGIN IMMEDIATE` transaction. Two properties matter there and neither
    follows from the other: it must refuse an unobserved process, and it must
    not resolve one — a resolve inside that block would put a bounded socket
    read under the SQLite write lock, which is the exact failure the design
    forbids. The injected resolver raises, so a stamp that quietly resolved its
    own evidence fails here instead of passing.
    """
    del unobserved_provenance

    with pytest.raises(PermissionError) as raised:
        provenance.admitted_cloud_executor_class()

    message = str(raised.value)
    assert provenance.PLATFORM_NOT_CLOUD_REASON in message
    assert "verdict=unknown" in message
    assert "reason=not_observed" in message


def test_admitted_stamp_returns_the_cloud_class(bind_provenance) -> None:
    """Control: the same stamp still produces `cloud` for an admitted process.

    Without this, a stamp that raised unconditionally would satisfy every
    negative above.
    """
    bind_provenance(ADMITTED)

    assert provenance.admitted_cloud_executor_class() == provenance.CLOUD


# --- registration-read eligibility (design.md § Enforcement sites (B)) ----


def test_existing_runtime_row_matches_nothing_for_an_unadmitted_process(
    tmp_path: Path, bind_provenance
) -> None:
    """A row an admitted process wrote grants an unadmitted reader nothing.

    The registration is created for real, by an admitted process, through
    `ensure_daemon_runtime` — so the row genuinely is the exact provider-bound
    worker and `runtime_matches_worker_provider` has every reason to say yes.
    Then the process verdict flips and the *same* row is read back. This is the
    replayed-registration shape from matrix row 4: a copied data volume, a
    restored backup or a restarted off-cloud process finds the row intact.

    The admitted read immediately before is the discriminator — it proves the
    identifiers match and the row is live, so the unadmitted `False` is the
    admission gate rather than a mismatched fixture.
    """
    bind_provenance(ADMITTED)
    args = _cloud_registration_args(tmp_path)
    runtime = ensure_daemon_runtime(tmp_path, **args)
    match_args = dict(
        universe_id=args["universe_id"],
        runtime_instance_id=str(runtime["runtime_instance_id"]),
        daemon_id=args["daemon_id"],
        worker_id=args["worker_id"],
        provider_name=args["provider_name"],
    )

    assert runtime_matches_worker_provider(tmp_path, **match_args) is True

    bind_provenance(UNADMITTED)
    assert runtime_matches_worker_provider(tmp_path, **match_args) is False, (
        "an existing cloud-worker registration row authorized an unadmitted process"
    )


def test_unobserved_process_matches_no_runtime_row(
    tmp_path: Path, bind_provenance, unobserved_provenance
) -> None:
    """Same row, a process that never resolved: still not eligible.

    The row is written under an admitted verdict, then the observation is
    replaced with an unresolved one. `runtime_matches_worker_provider` is
    cached-only by design, so this is also the assertion that the cached-only
    read refuses `None` rather than treating "no verdict" as permission.
    """
    bind_provenance(ADMITTED)
    args = _cloud_registration_args(tmp_path)
    runtime = ensure_daemon_runtime(tmp_path, **args)
    match_args = dict(
        universe_id=args["universe_id"],
        runtime_instance_id=str(runtime["runtime_instance_id"]),
        daemon_id=args["daemon_id"],
        worker_id=args["worker_id"],
        provider_name=args["provider_name"],
    )
    assert runtime_matches_worker_provider(tmp_path, **match_args) is True

    unobserved_provenance()
    assert runtime_matches_worker_provider(tmp_path, **match_args) is False


# --- matrix 5 at the provider sites ---------------------------------------
#
# Row 5 requires the copied-container label set to be refused "at all four
# sites". Rows 1 and 2 (claim, registration) carry it in
# `tests/test_cloud_only_admission_regressions.py`; these two carry it here,
# onto the exact foreground and served negatives above. The label set and the
# honest ``/data`` -> temp-root distinction are documented on
# `apply_copied_cloud_labels`.


def test_copied_cloud_labels_never_reach_the_foreground_provider(
    tmp_path: Path, monkeypatch, authenticate_request, bind_provenance
) -> None:
    """Matrix 5, foreground provider site.

    Identical to `test_unadmitted_foreground_run_never_reaches_the_provider`
    except that the process now wears the whole copied container config —
    every compose env var, the `mcp.tinyassets.io` hostname, the service name
    and a patched `socket.gethostname`. The refusal must not move, and the
    assertion is on the provider call list plus the persisted receipt rows.
    """
    bind_provenance(UNADMITTED)
    apply_copied_cloud_labels(monkeypatch, tmp_path)

    response, provider, _captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, _branch(node_count=1)
    )

    assert provider.calls == [], (
        f"a process wearing copied cloud labels dispatched {len(provider.calls)} "
        "foreground provider call(s)"
    )
    assert response["terminal_status"] != "completed"
    assert _receipt_rows(tmp_path) == [], (
        "copied cloud labels minted a cloud-class provider work receipt"
    )




def test_copied_cloud_labels_still_match_no_runtime_row(
    tmp_path: Path, bind_provenance, monkeypatch
) -> None:
    """Matrix 4 + 5 together at the eligibility read.

    `test_existing_runtime_row_matches_nothing_for_an_unadmitted_process`
    covers the replayed row; this adds the labels on top, so the combination a
    real copied deployment would actually present — an intact registration row
    *and* a matching container config — is the thing proven insufficient.
    """
    bind_provenance(ADMITTED)
    args = _cloud_registration_args(tmp_path)
    runtime = ensure_daemon_runtime(tmp_path, **args)
    match_args = dict(
        universe_id=args["universe_id"],
        runtime_instance_id=str(runtime["runtime_instance_id"]),
        daemon_id=args["daemon_id"],
        worker_id=args["worker_id"],
        provider_name=args["provider_name"],
    )
    assert runtime_matches_worker_provider(tmp_path, **match_args) is True

    bind_provenance(UNADMITTED)
    apply_copied_cloud_labels(monkeypatch, tmp_path)
    assert runtime_matches_worker_provider(tmp_path, **match_args) is False, (
        "an intact registration row plus copied cloud labels authorized an "
        "unadmitted process"
    )

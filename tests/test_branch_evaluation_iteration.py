"""Branch evaluation and iteration hooks.

Covers judge_run / list_judgments / compare_runs / suggest_node_edit /
get_node_output against the acceptance criteria in
``docs/specs/community_branches_phase4.md``.
"""

from __future__ import annotations

import importlib
import json

import pytest

import tinyassets.platform_runtime_provenance as platform_runtime_provenance
from tinyassets.platform_runtime_provenance import (
    CLOUD,
    ProcessProvenanceObservation,
    RuntimeProvenance,
)

#: One admitted verdict for this module, built from the production dataclass.
_ADMITTED_PROVENANCE = RuntimeProvenance(
    verdict=CLOUD,
    reason="instance_match",
    metadata_reachable=True,
    expected_identity_prepared=True,
)


@pytest.fixture(autouse=True)
def _module_local_cloud_admission(monkeypatch):
    """Admit this module's process through the real observation seam.

    These tests execute branches whose nodes make foreground provider calls,
    and those calls now require an admitted cloud runtime
    (`cloud-only-runtime-admission`). An unobserved process is refused, so
    without this `get_node_output` / `compare_runs` would be asserting the
    admission gate (`platform_not_cloud ... reason=metadata_http_error` on a
    CI runner) instead of the evaluation hooks they were written for.

    Module-local on purpose, matching `tests/test_run_provider_session.py`:
    a suite-wide admission would silently admit the negative regressions in
    `tests/test_cloud_only_*admission_regressions.py`. No metadata socket is
    opened and a green run establishes no cloud fact.
    """
    observation = ProcessProvenanceObservation(resolver=lambda: _ADMITTED_PROVENANCE)
    observation.observe()
    monkeypatch.setattr(
        platform_runtime_provenance, "_PROCESS_OBSERVATION", observation
    )
    return observation


# The universe these tests run branches through. Branches are executed BY a
# universe, so every run in this module routes via this one.
P4_UNIVERSE = "p4-universe"


@pytest.fixture
def p4_env(tmp_path, monkeypatch, authenticate_request):
    """Temp data root plus an authenticated request subject.

    `authenticate_request` is not optional decoration: branch mutation now
    requires a credential-derived subject, so without it every action here
    returns `{"error": "Authenticated branch subject required."}` and each
    test dies on the first `_build_trivial_branch` with `KeyError:
    'branch_def_id'`. Ordering matches `branch_env` in
    test_branch_authoring_actions.py — authenticate BEFORE reloading
    `universe_server`, since the reload rebinds module-level state.

    `costly` is requested explicitly because these tests judge and roll back
    RUNS, and `extensions.run_branch` carries
    `oauth_scope == "tinyassets.extensions.costly"` (asserted independently by
    test_action_scopes.py). It is not in the shared default so that suites
    asserting a costly refusal keep asserting something.
    """
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    authenticate_request(
        "tester",
        capabilities=[
            "tinyassets.extensions.read",
            "tinyassets.extensions.write",
            "tinyassets.extensions.admin",
            "tinyassets.extensions.costly",
        ],
    )

    # Branches are run BY universes now, so a run needs a registered universe
    # the actor may write. Without it `run_branch` returns
    # `branch_run_requires_universe`, and with an unregistered id it returns
    # `universe_access_denied` — the ACL grant is the part that is easy to miss.
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
    )

    udir = base / P4_UNIVERSE
    udir.mkdir(parents=True, exist_ok=True)
    ensure_universe_registered(base, universe_id=P4_UNIVERSE, universe_path=udir)
    grant_universe_access(
        base,
        universe_id=P4_UNIVERSE,
        actor_id="tester",
        permission="write",
        granted_by="p4_env",
    )

    from tinyassets import universe_server as us
    provider_calls = importlib.import_module("tinyassets.providers.call")

    importlib.reload(us)
    monkeypatch.setattr(
        provider_calls,
        "call_provider",
        lambda prompt, _system="", **_kwargs: f"fixture:{prompt}",
    )
    yield us, base
    importlib.reload(us)


def _call(us, action, **kwargs):
    return json.loads(us._extensions_impl(action=action, **kwargs))


def _wait(run_id: str, timeout: float = 20.0) -> None:
    from tinyassets.runs import wait_for

    wait_for(run_id, timeout=timeout)


def _build_trivial_branch(us) -> str:
    """A single-node branch that always completes cleanly via mock provider.

    Keeps evaluation tests focused on eval surfaces rather than graph shape.
    """
    spec = {
        "name": "Trivial",
        "description": "Single-node test branch",
        "entry_point": "n",
        "node_defs": [
            {"node_id": "n", "display_name": "N",
             "prompt_template": "Handle: {x}", "output_keys": ["n_out"]},
        ],
        "edges": [
            {"from": "START", "to": "n"},
            {"from": "n", "to": "END"},
        ],
        "state_schema": [
            {"name": "x", "type": "str"},
            {"name": "n_out", "type": "str"},
        ],
    }
    return _call(us, "build_branch", spec_json=json.dumps(spec))["branch_def_id"]


def _run(us, bid: str, inputs: dict | None = None) -> str:
    result = _call(us, "run_branch", branch_def_id=bid,
                   universe_id=P4_UNIVERSE,
                   inputs_json=json.dumps(inputs or {"x": "input"}))
    _wait(result["run_id"])
    return result["run_id"]


# ─────────────────────────────────────────────────────────────────────────────
# judge_run
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# list_judgments
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# compare_runs
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# suggest_node_edit
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# get_node_output
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Lineage + audit — wiring from other actions
# ─────────────────────────────────────────────────────────────────────────────


def test_run_branch_records_lineage(p4_env):
    """Every run writes a run_lineage row. Second run on the same
    branch + actor points at the first run as parent."""
    us, base = p4_env
    bid = _build_trivial_branch(us)
    rid1 = _run(us, bid)
    rid2 = _run(us, bid)

    from tinyassets.runs import get_lineage

    lin1 = get_lineage(base, rid1)
    lin2 = get_lineage(base, rid2)
    assert lin1 is not None and lin2 is not None
    assert lin1["parent_run_id"] is None
    assert lin2["parent_run_id"] == rid1
    assert lin1["branch_version"] == 1
    assert lin2["branch_version"] == 1


def test_run_branch_resume_from_records_explicit_source_run(p4_env):
    """resume_from chooses the source run even when it is not the latest run."""
    us, base = p4_env
    bid = _build_trivial_branch(us)
    source_run_id = _run(us, bid, {"x": "source"})
    latest_run_id = _run(us, bid, {"x": "latest"})

    resumed = _call(
        us,
        "run_branch",
        branch_def_id=bid,
        universe_id=P4_UNIVERSE,
        inputs_json=json.dumps({"x": "override"}),
        resume_from=source_run_id,
    )
    _wait(resumed["run_id"])

    from tinyassets.runs import get_lineage

    lineage = get_lineage(base, resumed["run_id"])
    assert lineage is not None
    assert lineage["parent_run_id"] == source_run_id
    assert lineage["parent_run_id"] != latest_run_id
    assert resumed["resume_from"] == source_run_id


def test_run_branch_resume_from_missing_source_returns_error(p4_env):
    us, _ = p4_env
    bid = _build_trivial_branch(us)

    result = _call(
        us,
        "run_branch",
        branch_def_id=bid,
        universe_id=P4_UNIVERSE,
        resume_from="missing-run-id",
    )

    assert "error" in result
    assert "resume_from" in result["error"]
    assert result["failure_class"] == "resume_from_not_found"


def test_run_branch_resume_from_carries_source_inputs_when_absent(p4_env):
    us, base = p4_env
    bid = _build_trivial_branch(us)
    source_run_id = _run(us, bid, {"x": "source-input"})

    resumed = _call(
        us,
        "run_branch",
        branch_def_id=bid,
        universe_id=P4_UNIVERSE,
        resume_from=source_run_id,
    )
    _wait(resumed["run_id"])

    from tinyassets.runs import get_run

    run_record = get_run(base, resumed["run_id"])
    assert run_record is not None
    assert run_record["inputs"] == {"x": "source-input"}


def test_run_branch_resume_from_cross_actor_returns_error(p4_env):
    # Run isolation is by owning universe: a branch run's actor is
    # `universe:<uid>` (permissions.branch_run_actor), so a run created by one
    # universe must not be resumable by another. The retired UNIVERSE_SERVER_USER
    # env-actor model no longer distinguishes callers (current_actor_id has no env
    # fallback), so cross-actor isolation is exercised via two universes.
    us, base = p4_env

    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
    from tinyassets.daemon_server import grant_universe_access

    class _Founder(AuthProvider):
        def __init__(self, ident): self.ident = ident
        def resolve_token(self, t): return self.ident if t == "ok" else None
        def is_auth_required(self): return False
        def resolve_always_writes(self): return True
        def register_client(self, m): return {"client_id": "t", **m}
        def create_authorization(self, *a, **k): return "c"
        def exchange_code(self, *a, **k): return None

    set_provider(_Founder(Identity(
        user_id="founder", username="founder",
        capabilities=["read", "write", "costly", "admin"],
    )))
    auth_middleware("ok")
    try:
        # Built by the founder who runs it: Branches are private by default, so a
        # Branch another subject built is "not found" to the founder, and the
        # refusal under test (resume across universes) is never reached.
        bid = _build_trivial_branch(us)
        for uid in ("uni-a", "uni-b"):
            grant_universe_access(
                base, universe_id=uid, actor_id="founder",
                permission="admin", granted_by="founder",
            )
        a_run = _call(us, "run_branch", branch_def_id=bid, universe_id="uni-a",
                      inputs_json=json.dumps({"x": "a"}))
        _wait(a_run["run_id"])

        result = _call(us, "run_branch", branch_def_id=bid, universe_id="uni-b",
                       resume_from=a_run["run_id"])
    finally:
        set_provider(DevAuthProvider())
        auth_middleware("dev")

    assert "error" in result
    assert "not visible" in result["error"]
    assert result["failure_class"] == "resume_from_forbidden"


def test_run_branch_resume_from_branch_mismatch_returns_error(p4_env):
    us, _ = p4_env
    source_bid = _build_trivial_branch(us)
    target_bid = _build_trivial_branch(us)
    source_run_id = _run(us, source_bid, {"x": "source"})

    result = _call(
        us,
        "run_branch",
        branch_def_id=target_bid,
        universe_id=P4_UNIVERSE,
        resume_from=source_run_id,
    )

    assert "error" in result
    assert "different workflow" in result["error"]
    assert result["failure_class"] == "resume_from_branch_mismatch"


# ─────────────────────────────────────────────────────────────────────────────
# Catalog + cross-phase smoke
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# #50: rollback_node + list_node_versions
# ─────────────────────────────────────────────────────────────────────────────

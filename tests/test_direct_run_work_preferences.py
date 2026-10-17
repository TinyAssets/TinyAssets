"""Saved work preferences reach a DIRECT run_graph run, with no test injection.

`tests/test_work_consumer_model_bridge.py` proves the captured-choice machinery
by monkeypatching `_ForegroundRunProviderSession.__init__` to hand it a
preference document. That stub is exactly where the proof leaked: production's
`new_foreground_run_provider_session` never passed one, so the owner's saved
default governed chat and NOT their workflow runs. Every test here drives the
real factory through the real store.
"""

import pytest

from tests import test_run_provider_session as work_session
from tests import test_workflow_http_agent as work_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_run_provider_session import (
    _branch,
    _run_branch,
    _seed_open_serving_assignment,
)
from tinyassets.provider_assignment_manifest import ModelAccess

http_wire = work_tests.http_wire
work_agent = work_tests.work_agent

OWNER, HOME = "acct_alice", "universe_alice"
PRIMARY, TAIL = "synthetic-model", "future-company/new-choice"


pytestmark = pytest.mark.usefixtures("cloud_runtime")


def _observe_sessions(monkeypatch):
    """Observe every real session a run builds, without changing its inputs."""
    from tinyassets.foreground_run_provider import _ForegroundRunProviderSession

    original = _ForegroundRunProviderSession.__init__
    built = []

    def init(session, base_path, **kwargs):
        original(session, base_path, **kwargs)
        built.append(session)

    monkeypatch.setattr(_ForegroundRunProviderSession, "__init__", init)
    return built


def _save_preference(base, connection, *, owner=OWNER, universe=HOME,
                     models=(PRIMARY, TAIL), mode="explicit", expected_generation=0):
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.storage.model_preferences import ModelPreferenceStore

    primary, *tail = models
    return ModelPreferenceStore(base).save(
        owner, universe, expected_generation=expected_generation,
        policy=ModelPreferences(
            mode, ModelRef(connection, primary),
            tuple(ModelRef(connection, model) for model in tail),
        ),
    )


def _seed(tmp_path, monkeypatch, authenticate_request, *, node_count=1):
    """Seed one owner-bound HTTP source ONCE and return its connection id.

    `_run_branch` seeds the same fixture itself, and the ledger refuses a
    duplicate connection id. Seed here so a preference can be saved against the
    real connection BEFORE the run, then make the helper's own call idempotent.
    """
    from tinyassets.daemon_server import set_founder_home

    authenticate_request(OWNER)
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id=HOME, platform_generated=True)
    connection = _seed_open_serving_assignment(
        tmp_path, monkeypatch, model_access=ModelAccess("discovered"),
    )
    monkeypatch.setattr(
        work_session, "_seed_open_serving_assignment", lambda *a, **k: connection,
    )
    branch = _branch(node_count=node_count)
    for node in branch.node_defs:
        node.tools_allowed = ["universe_self"]
    return branch, connection


def _run(tmp_path, monkeypatch, authenticate_request, branch, **kwargs):
    return _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch,
        open_provider=True, model_access=ModelAccess("discovered"), **kwargs,
    )


def _models(work_agent):
    return [wire["body"]["model"] for wire in work_agent.wires]


def test_direct_run_uses_the_owners_saved_default_with_no_injected_document(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    built = _observe_sessions(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection)

    result, _provider, _captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "completed", (result, work_agent.errors)
    assert set(_models(work_agent)) == {PRIMARY}
    assert built and built[0]._work_candidates is not None
    assert [ref.model_id for ref in built[0]._work_candidates.order] == [PRIMARY, TAIL]


def test_direct_run_falls_back_to_the_saved_tail_without_repeating_effects(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    _observe_sessions(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection)
    work_agent.mode = "later_model_capacity"

    result, _provider, captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "completed", (result, work_agent.errors)
    assert _models(work_agent)[-1] == TAIL
    assert _models(work_agent).count(TAIL) == 1
    assert len(captured["effects"]) == 1
    assert len(work_agent.tools) == 1


def test_direct_run_without_a_saved_preference_still_orders_the_owners_catalogue(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    """Nothing saved is AUTOMATIC, not "no order at all".

    This asserted `_work_candidates is None` -- the legacy single-provider path
    -- which is the defect, not the contract. That path resolves an unpinned
    node to `snapshot.default_model_id or definition.model`, the source's
    DECLARED default, and live 2026-09-30 a free account's declared model had
    left its own catalogue: chat ran, every run and automation failed
    `authority_held`. An owner who has chosen nothing gets the same automatic
    order their chat turn gets (tests/test_free_account_run_provider_parity.py).
    """
    built = _observe_sessions(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)

    result, _provider, _captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "completed", (result, work_agent.errors)
    assert built and all(session._work_candidates is not None for session in built)
    order = built[0]._work_candidates
    assert order.automatic is True, "nothing saved must not read as an explicit choice"
    assert built[0]._model_preference_data["saved"] is None
    assert {ref.connection_id for ref in order.order} == {connection}
    assert set(_models(work_agent)) == {PRIMARY}


def test_an_explicit_saved_choice_with_no_fallback_stays_exhausted(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    built = _observe_sessions(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection, models=(PRIMARY,))
    work_agent.mode = "all_models_full"

    result, _provider, captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "failed"
    assert set(_models(work_agent)) == {PRIMARY}
    assert captured["effects"] == []
    assert built[0]._work_candidates is not None
    assert [ref.model_id for ref in built[0]._work_candidates.order] == [PRIMARY]


def test_a_preference_saved_mid_run_does_not_change_the_captured_policy(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    """Capture once per run: a save landing mid-run cannot reorder admitted work."""
    built = _observe_sessions(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection)
    resaved = []

    def resave():
        if not resaved:
            _save_preference(tmp_path, connection, models=(TAIL,), expected_generation=1)
            resaved.append(True)

    # Fires between this run's two model rounds, with the order already admitted.
    work_agent.after_tool = resave

    result, _provider, _captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "completed", (result, work_agent.errors)
    assert resaved == [True]
    # Both rounds ran the generation-1 primary; the generation-2 save is invisible.
    assert set(_models(work_agent)) == {PRIMARY}
    assert [ref.model_id for ref in built[0]._work_candidates.order] == [PRIMARY, TAIL]
    # The generation-2 document IS what a NEW run would capture.
    from tinyassets.foreground_run_provider import captured_work_preference

    fresh = captured_work_preference(tmp_path, universe_id=HOME, principal_id=OWNER)
    assert fresh["observed_generation"] == 2
    assert fresh["saved"]["saved_default"]["model_id"] == TAIL
    assert built[0]._model_preference_data["observed_generation"] == 1


def test_a_sibling_run_inherits_the_captured_document_and_not_the_built_order(
    tmp_path, monkeypatch, authenticate_request,
):
    """A parallel sub-branch must not see a different policy version.

    `constructor_inputs` is the only thing a child session is built from. The
    captured DOCUMENT is inherited so the child cannot re-read a newer
    generation; the built order is not, because it carries the parent run's
    fitted allowance and exhaustion state and because rebuilding it is what
    rechecks current authority.
    """
    from tinyassets.foreground_run_provider import _ForegroundRunProviderSession

    document = {"version": 1, "saved": None, "observed_generation": 0, "current": None}
    session = _ForegroundRunProviderSession.__new__(_ForegroundRunProviderSession)
    monkeypatch.setattr(_ForegroundRunProviderSession, "_capture_choices", lambda self: None)
    _ForegroundRunProviderSession.__init__(
        session, tmp_path, universe_id=HOME, principal_id=OWNER,
        provider_call=lambda *a, **k: "", model_preference_data=document,
    )

    inputs = session.constructor_inputs()
    assert inputs["model_preference_data"] is document
    assert "_work_candidates" not in inputs and "_receipt" not in inputs
    child = _ForegroundRunProviderSession(**inputs)
    assert child._model_preference_data is document
    assert child._work_candidates is None


@pytest.mark.parametrize("scope", ["owner", "universe"])
def test_another_scopes_saved_preference_never_reaches_this_run(
    tmp_path, monkeypatch, authenticate_request, work_agent, scope,
):
    """This run reads its OWN scope: automatic, not the other scope's choice.

    The old assertion was `_work_candidates is None`, which a session that read
    nothing at all satisfies just as well as one that read the right row. What
    separates the two is the captured document and the resulting MODE: nothing
    saved for THIS owner and universe, so the order is automatic and the other
    scope's explicit primary/tail is nowhere in it.
    """
    built = _observe_sessions(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection,
                     **({"owner": "acct_bob"} if scope == "owner"
                        else {"universe": "universe_other"}))

    result, _provider, _captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "completed", (result, work_agent.errors)
    assert built and all(session._work_candidates is not None for session in built)
    assert all(session._model_preference_data["saved"] is None for session in built)
    assert all(session._model_preference_data["observed_generation"] == 0
               for session in built)
    assert built[0]._work_candidates.automatic is True


def test_a_revoked_source_refuses_the_saved_choice_rather_than_substituting(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    """Preferences grant nothing: fresh authority still decides every launch."""
    _observe_sessions(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection)
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    from tinyassets.providers.definition import list_definitions

    ledger.revoke_grant(list_definitions("universe_alice")[0].ref)

    result, _provider, captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "failed"
    assert work_agent.wires == []
    assert captured["effects"] == []


@pytest.mark.parametrize("home_case", ["home_rebound", "cross_universe"])
def test_a_moved_home_refuses_the_run_without_a_new_failure_shape(
    tmp_path, monkeypatch, authenticate_request, work_agent, home_case,
):
    """Reading preferences must not invent a failure a moved home did not have."""
    _observe_sessions(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection)

    result, _provider, captured = _run(tmp_path, monkeypatch, authenticate_request, branch,
                                       authority_case=home_case)

    assert result["terminal_status"] == "failed"
    assert work_agent.wires == []
    assert captured["effects"] == []


def test_an_unreadable_preference_row_is_held_not_treated_as_absent(
    tmp_path, monkeypatch, authenticate_request,
):
    """Hard Rule 8: a corrupt row must never quietly become "no preference".

    Falling back to legacy here would run the owner's workflow on a source they
    did not choose, and nothing would say so.
    """
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.foreground_run_provider import captured_work_preference
    from tinyassets.storage.model_preferences import PreferenceStoreUnavailable
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    authenticate_request(OWNER)
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id=HOME, platform_generated=True)
    with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO universe_model_preferences (owner_user_id, universe_id, "
            "generation, policy_json, updated_at) VALUES (?, ?, 1, ?, ?)",
            (OWNER, HOME, "{not json", "2026-09-20T00:00:00Z"),
        )
        conn.commit()

    with pytest.raises(PreferenceStoreUnavailable):
        captured_work_preference(tmp_path, universe_id=HOME, principal_id=OWNER)


def _observe_work_agent_failures(monkeypatch):
    """Record what the REAL tool-agent entry raises, without changing it.

    `inspect.getsource` on a raise site proves a line exists, not that a run
    reaches it. This wraps the real function the run session calls and lets
    every exception through untouched.
    """
    from tinyassets import workflow_agent

    original = workflow_agent.call_foreground_work_agent
    raised = []

    def wrapped(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except BaseException as exc:
            raised.append(exc)
            raise

    monkeypatch.setattr(workflow_agent, "call_foreground_work_agent", wrapped)
    return raised


def test_exhausting_the_tool_agent_order_is_typed_and_classified_as_exhaustion(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    """The universe_self path exhausts through `workflow_agent`, not the captured
    prompt loop; it must reach the run taxonomy as `work_model_exhausted`, never
    as "connect your provider" or a generic provider failure."""
    from tinyassets.api.runs import (
        _WORK_MODEL_EXHAUSTED_ACTION,
        _classify_run_error,
        _classify_run_outcome_error,
    )
    from tinyassets.exceptions import WorkModelExhaustedError

    raised = _observe_work_agent_failures(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection, models=(PRIMARY,))
    work_agent.mode = "all_models_full"

    result, _provider, captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "failed"
    assert captured["effects"] == [] and work_agent.tools == []
    # Typed on the actual path: the last thing the tool-agent entry raised.
    assert raised, "the tool-agent entry never raised"
    assert isinstance(raised[-1], WorkModelExhaustedError), raised
    assert _classify_run_error(raised[-1], branch.branch_def_id)["failure_class"] == (
        "work_model_exhausted"
    )
    # Through the stored run record, where only the string survives.
    stored = result["terminal_error"]
    assert _classify_run_outcome_error(stored) == (
        "work_model_exhausted", _WORK_MODEL_EXHAUSTED_ACTION,
    ), stored
    # Evidence the owner can act on, without the provider's response body.
    assert PRIMARY in stored and connection in stored
    assert "provider_overloaded" in stored
    assert "synthetic overload" not in stored


def test_an_authentication_failure_on_the_tool_agent_path_is_never_exhaustion(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    """A sign-in failure is not capacity: no second model, no exhaustion class."""
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.exceptions import WorkModelExhaustedError

    raised = _observe_work_agent_failures(monkeypatch)
    branch, connection = _seed(tmp_path, monkeypatch, authenticate_request)
    _save_preference(tmp_path, connection)
    work_agent.mode = "authentication"

    result, _provider, captured = _run(tmp_path, monkeypatch, authenticate_request, branch)

    assert result["terminal_status"] == "failed"
    assert captured["effects"] == [] and work_agent.tools == []
    assert set(_models(work_agent)) == {PRIMARY}
    assert raised and not any(isinstance(exc, WorkModelExhaustedError) for exc in raised)
    annotation = _classify_run_outcome_error(result["terminal_error"])
    assert annotation is None or annotation[0] != "work_model_exhausted", annotation

"""Recorded legacy stop reasons survive both owner-facing reads."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests import test_automations_api as automation_fixtures
from tests.cloud_automation_fixtures import _definition
from tests.test_custom_ui_bridge import _run
from tinyassets.api.automations import automations
from tinyassets.api.graph_reads import read_graph
from tinyassets.cloud_automation_control import CloudAutomationDesiredState
from tinyassets.consumer_reason_actions import RETIRED_FLEET_CONTROL_REASON
from tinyassets.storage.assigned_queue_refusals import AssignedQueueRefusalStore
from tinyassets.storage.cloud_automation_control import CloudAutomationControlStore

UNIVERSE = "universe_alice"
AUTOMATION = "automation_spec_drain"
OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)
automation_env = automation_fixtures.env
pytestmark = pytest.mark.usefixtures("automation_env")


def _stopped(base):
    store = CloudAutomationControlStore(base, clock=lambda: OLD)
    control = store.create_control(
        _definition(), automation_id=AUTOMATION, cadence_seconds=300,
    )
    return store, store.set_desired_state(
        expected=control, desired_state=CloudAutomationDesiredState.STOPPED,
    )


def _record(base, *, universe=UNIVERSE, observed=OLD, reason=RETIRED_FLEET_CONTROL_REASON):
    AssignedQueueRefusalStore(base).record(
        branch_task_id=f"automation:{AUTOMATION}", universe_id=universe,
        reason=reason, observed_at=observed.isoformat(), consumer_id="worker_retirement",
    )


def _listed():
    return json.loads(read_graph(target="automations", graph_id=UNIVERSE, limit=100))


def test_old_retirement_reason_reaches_agent_and_app(tmp_path):
    store, stopped = _stopped(tmp_path)
    _record(tmp_path)
    assert AssignedQueueRefusalStore(tmp_path).fresh_reasons(
        universe_id=UNIVERSE, max_age_seconds=60,
    ) == {}
    listed = _listed()
    row = listed["automations"][0]
    assert row["stopped_because"] == RETIRED_FLEET_CONTROL_REASON
    assert row["universe_id"] == UNIVERSE
    assert row["legacy"] is True
    assert store.get_control(universe_id=UNIVERSE, automation_id=AUTOMATION) == stopped

    # Feed the actual API response through the shipped JavaScript picker.
    checks = r'''
(async()=>{
AppUI.home=HOME;
AppUI.readWhole=async()=>DOCUMENT;
const result=await AppUI.listAutomations();
assert.equal(result.automations.length,1);
const row=result.automations[0];
assert.equal(row.state,'stopped');
assert.equal(row.stopped_because,REASON);
assert.equal(row.legacy,true);
assert.equal(row.status,'retired_fleet_era');
assert(!('principal_id' in row));
assert(!('definition_json' in row));
console.log('stop reason reaches app');
})().catch(err=>{console.error(err);process.exit(1);});
'''
    # The harness HOME differs from the fixture universe; translate just scope.
    extra = "const DOCUMENT=" + json.dumps(listed) + ";\n"
    extra += "DOCUMENT.universe_id=HOME;DOCUMENT.automations[0].universe_id=HOME;\n"
    extra += "const REASON=" + json.dumps(RETIRED_FLEET_CONTROL_REASON) + ";\n"
    assert "stop reason reaches app" in _run(tmp_path, "stopped.js", checks, extra)


@pytest.mark.parametrize("evidence", ["missing", "foreign", "before_stop", "empty"])
def test_missing_matching_evidence_does_not_invent_a_cause(
    tmp_path, evidence,
):
    _stopped(tmp_path)
    if evidence == "foreign":
        _record(tmp_path, universe="universe_elsewhere")
    elif evidence == "before_stop":
        _record(tmp_path, observed=OLD - timedelta(days=1), reason="prior refusal")
    elif evidence == "empty":
        _record(tmp_path, reason="")
    assert _listed()["automations"][0]["stopped_because"] == "Stop reason was not recorded."


def test_real_retirement_path_records_the_reason_the_read_uses(tmp_path):
    from tinyassets.runtime.assigned_queue_consumer import AssignedQueueConsumer

    store = CloudAutomationControlStore(tmp_path)
    store.create_control(_definition(), automation_id=AUTOMATION, cadence_seconds=300)
    consumer = AssignedQueueConsumer(tmp_path, max_concurrency=1)
    try:
        consumer._retire_fleet_controls()
        consumer._retire_fleet_controls()
    finally:
        consumer.stop()
    row = _listed()["automations"][0]
    assert row["desired_state"] == "stopped"
    assert row["stopped_because"] == RETIRED_FLEET_CONTROL_REASON


def test_current_owner_pause_reason_still_reaches_both_reads(tmp_path):
    created = automations(
        action="create", universe_id=UNIVERSE, payload=automation_fixtures.CREATE_PAYLOAD,
    )
    current = created["automation"]
    automations(
        action="pause", universe_id=UNIVERSE,
        automation_id=current["automation_id"], expected_revision=current["revision"],
    )
    listed = _listed()
    assert listed["automations"][0]["pause_reason"] == "owner_paused"
    checks = r'''
(async()=>{
AppUI.home=HOME;AppUI.readWhole=async()=>DOCUMENT;
const row=(await AppUI.listAutomations()).automations[0];
assert.equal(row.state,'paused');assert.equal(row.paused_because,'owner_paused');
assert(!('stopped_because' in row));
console.log('pause reason reaches app');
})().catch(err=>{console.error(err);process.exit(1);});
'''
    extra = "const DOCUMENT=" + json.dumps(listed) + ";\n"
    extra += "DOCUMENT.universe_id=HOME;DOCUMENT.automations[0].universe_id=HOME;\n"
    assert "pause reason reaches app" in _run(tmp_path, "paused.js", checks, extra)


@pytest.mark.parametrize("recorded", [False, True])
def test_legacy_pause_reason_is_recorded_or_explicitly_unknown(tmp_path, recorded):
    from tests.test_cloud_automation_control import _active

    definition, _activations, activation = _active(tmp_path, clock=lambda: OLD)
    store = CloudAutomationControlStore(tmp_path, clock=lambda: OLD)
    store.schedule_initial(
        definition, automation_id=AUTOMATION, activation=activation,
        cadence_seconds=300, due_at=OLD,
    )
    paused = store.set_desired_state(
        expected=store.get_control(universe_id=UNIVERSE, automation_id=AUTOMATION),
        desired_state=CloudAutomationDesiredState.PAUSED,
    )
    if recorded:
        _record(tmp_path, reason="owner_paused")
    row = _listed()["automations"][0]
    assert row["pause_reason"] == (
        "owner_paused" if recorded else "Pause reason was not recorded."
    )
    assert "stopped_because" not in row
    assert store.get_control(universe_id=UNIVERSE, automation_id=AUTOMATION) == paused

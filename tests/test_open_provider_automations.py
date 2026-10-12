"""An owner's automation runs on their own api_key_http source.

Registration refused an open (api_key_http) assignment from #2693, citing a
foreground rule that 0f96c04d removed on 2026-09-03; the runtime check never
refused one. So a free account -- whose only source is an API key -- could not
create an automation, a once-wake (#4067) or an event trigger (#4079) at all.

Drives the real path: ``register_automation`` on a real open assignment, then
``run_due_automation`` on a fresh thread with no request identity, as the
consumer runs it, through the real session, router and ``ApiKeyHttpProvider``.
Only the remote transport is synthetic (tests/test_work_model_selection.py).
"""

import threading
from datetime import datetime, timezone

import pytest

from tests import test_work_model_selection as work_model_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_run_provider_session import _branch, _seed_open_serving_assignment

http_wire = work_model_tests.http_wire
pytestmark = pytest.mark.usefixtures("cloud_runtime")

OWNER = "acct_alice"
UNIVERSE = "universe_alice"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _owner_on_an_open_source(tmp_path, monkeypatch):
    from tinyassets.daemon_server import (
        grant_universe_access,
        initialize_author_server,
        save_branch_definition,
        set_founder_home,
    )
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import _is_open_provider

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    initialize_author_server(tmp_path)
    grant_universe_access(tmp_path, universe_id=UNIVERSE, actor_id=OWNER,
                          permission="admin", granted_by=OWNER)
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id=UNIVERSE,
                     platform_generated=True)
    source = _seed_open_serving_assignment(
        tmp_path, monkeypatch, model_access=ModelAccess("discovered"),
    )
    assignment = load_provider_assignment(tmp_path, universe_id=UNIVERSE)
    assert assignment is not None and assignment.state == "ready"
    assert _is_open_provider(assignment.provider), "the fixture must be an OPEN source"
    branch = _branch(node_count=1)
    branch.node_defs[0].llm_policy = {
        "preferred": {"provider": source, "model": "synthetic-model"}, "fallback_chain": [],
    }
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    return branch


def _run_on_a_fresh_thread(tmp_path, automation, due):
    """As the consumer does: no request identity is bound on this thread."""
    from tinyassets.automations import run_due_automation

    out = {}

    def go():
        try:
            out["reason"] = run_due_automation(tmp_path, automation, due, now=NOW)
        except BaseException as exc:  # noqa: BLE001 - reported below, never swallowed
            out["exc"] = exc

    worker = threading.Thread(target=go)
    worker.start()
    worker.join(120)
    assert not worker.is_alive(), "the due run did not finish"
    if "exc" in out:
        raise out["exc"]
    return out["reason"]


def test_an_open_assignment_registers_and_its_due_run_launches(
    tmp_path, monkeypatch, http_wire, authenticate_request,
):
    from tinyassets.automations import AutomationStore, register_automation

    branch = _owner_on_an_open_source(tmp_path, monkeypatch)
    # Registration is the owner's own request; the due run below binds nothing.
    authenticate_request(OWNER)
    automation = register_automation(
        tmp_path, universe_id=UNIVERSE, owner_principal_id=OWNER, name="nightly",
        branch_def_id=branch.branch_def_id,
        interval_seconds=600, inputs={}, now=NOW,
    )
    assert AutomationStore(tmp_path).get(automation.automation_id) is not None

    _reads, writes, errors = http_wire
    reason = _run_on_a_fresh_thread(tmp_path, automation, "2026-09-29T12:10:00+00:00")

    assert reason.startswith("ok:ran:"), (reason, errors[:1])
    assert len(writes) == 1, "the run did not reach the owner's own source"
    assert writes[0][1]["body"]["model"] == "synthetic-model"
    row = AutomationStore(tmp_path).get(automation.automation_id)
    assert row.desired_state == "active" and row.last_run_id

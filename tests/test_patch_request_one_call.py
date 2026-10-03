"""One engine call uses real receiver, consent, link and delivery storage."""
# ruff: noqa: F811 -- pytest resolves imported fixtures by parameter name

import json

import pytest

from tests.engine_authority_helpers import mock_engine_admission
from tests.test_receiver_links import env  # noqa: F401
from tinyassets import engine_mcp_server as engine
from tinyassets import patch_intake
from tinyassets.api import receiver_links
from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.daemon_server import list_branch_definitions, save_branch_definition
from tinyassets.storage import deliveries


@pytest.fixture
def world(env, monkeypatch):
    base, auth = env
    for owner in ("receiver", "sender", "outsider"):
        (base / ("u-" + owner)).mkdir(exist_ok=True)
    monkeypatch.setattr(engine, "_ACTOR_ID", "sender")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-sender")
    mock_engine_admission(monkeypatch, {"u-sender"})
    # Acceptance is real; leave receiver execution to the existing delivery tests.
    monkeypatch.setattr(
        "tinyassets.delivery_runtime.dispatch_accepted_delivery", lambda *a, **kw: None,
    )
    auth("receiver")
    receiver = receiver_links.save_receiver(
        universe_id="u-receiver", branch_def_id="b-receiver", node_id="entry",
        input_keys=["topic"], allowed_senders=["sender"],
    )
    monkeypatch.setenv(patch_intake.RECEIVER_ID_VAR, receiver["receiver_id"])
    monkeypatch.setenv(patch_intake.LABEL_VAR, "Test intake")
    auth("outsider")  # The engine pins, not this ambient identity, own the report.
    return base, auth, receiver


def _send(**payload):
    return json.loads(engine.write_graph(
        target="patch_request", operation="send", payload_json=json.dumps(payload),
    ))


def _grant(base, receiver):
    patch_intake.grant_send_consent(
        base / "u-sender", receiver_id=receiver["receiver_id"], granted_by="sender",
    )


def _rows(base, table):
    assert table in {"graph_deliveries", "graph_output_links"}
    with deliveries.transaction(base) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM " + table)]


def test_one_call_delivers_and_reuses_private_source(world):
    base, _, receiver = world
    _grant(base, receiver)
    first = _send(title="Missing feature", details="I tried this.\nIt was missing.")
    assert first.get("sent") is True, first
    assert first == {"sent": True, "delivery_id": first["delivery_id"], "to": "Test intake"}
    second = _send(title="Another gap", details="More details")
    assert second["sent"] is True
    assert second["delivery_id"] != first["delivery_id"]
    rows = _rows(base, "graph_deliveries")
    assert len(rows) == 2
    assert {row["receiver_id"] for row in rows} == {receiver["receiver_id"]}
    assert json.loads(rows[0]["inputs_json"])["topic"] == (
        "Missing feature\n\nI tried this.\nIt was missing."
    )
    assert len(_rows(base, "graph_output_links")) == 1
    branches = [b for b in list_branch_definitions(base, author="sender", viewer="sender")
                if b["name"] == "Report to TinyAssets"]
    assert len(branches) == 1
    assert branches[0]["visibility"] == "private"


@pytest.mark.parametrize("field", ["title", "details"])
def test_missing_field_names_the_payload_field(world, field):
    payload = {"title": "Gap", "details": "Missing"}
    del payload[field]
    result = _send(**payload)
    assert result["error"] == "patch_request_field_missing"
    assert result["field"] == field
    assert set(result["example"]) == {"title", "details"}


@pytest.mark.parametrize("field,value", [
    ("title", ""), ("title", "a" * 121), ("title", "one\ntwo"), ("title", "one\n"),
    ("details", ""), ("details", "a" * 8001), ("details", 5), ("title", []),
])
def test_invalid_field_names_the_payload_field(world, field, value):
    payload = {"title": "Gap", "details": "Missing", field: value}
    result = _send(**payload)
    assert result["error"] == "patch_request_field_invalid"
    assert result["field"] == field


def test_consent_required_delivers_nothing(world):
    base, _, _ = world
    result = _send(title="Gap", details="Missing")
    assert result["error"] == "patch_intake_consent_required"
    assert "is waiting in their rail" in result["how"]
    assert _rows(base, "graph_deliveries") == []
    assert _rows(base, "graph_output_links") == []


def test_unconfigured_intake(world, monkeypatch):
    base, _, _ = world
    monkeypatch.delenv(patch_intake.RECEIVER_ID_VAR)
    assert _send(title="Gap", details="Missing") == {"error": "patch_intake_unavailable"}
    assert _rows(base, "graph_deliveries") == []


def test_payload_cannot_select_sender_or_receiver(world):
    base, _, receiver = world
    _grant(base, receiver)
    result = _send(title="Gap", details="Missing", receiver_id="another-receiver",
                   actor="outsider", universe_id="u-outsider", principal_id="outsider")
    assert result.get("sent") is True, result
    row, = _rows(base, "graph_deliveries")
    assert row["receiver_id"] == receiver["receiver_id"]
    assert row["sender_id"] == "sender"
    assert row["sender_universe_id"] == "u-sender"
    assert json.loads(row["inputs_json"]) == {"topic": "Gap\n\nMissing"}


def test_named_inputs_use_receiver_contract(world, monkeypatch):
    base, auth, _ = world
    auth("receiver")
    branch = BranchDefinition(
        branch_def_id="named-intake", name="Named intake", author="receiver", visibility="private",
        entry_point="entry",
        node_defs=[NodeDefinition(
            node_id="entry", display_name="Intake", input_keys=["subject", "description"],
            output_keys=["result"], prompt_template="{subject}: {description}",
        )],
        graph_nodes=[GraphNodeRef(id="entry", node_def_id="entry")],
        edges=[EdgeDefinition("START", "entry"), EdgeDefinition("entry", "END")],
        state_schema=[{"name": name, "type": "str"}
                      for name in ("subject", "description", "result")],
    )
    save_branch_definition(base, branch_def=branch.to_dict())
    receiver = receiver_links.save_receiver(
        universe_id="u-receiver", branch_def_id="named-intake", node_id="entry",
        input_keys=["subject", "description"], allowed_senders=["sender"],
    )
    monkeypatch.setenv(patch_intake.RECEIVER_ID_VAR, receiver["receiver_id"])
    _grant(base, receiver)
    assert _send(title="Gap", details="Missing")["sent"] is True
    row, = _rows(base, "graph_deliveries")
    assert json.loads(row["inputs_json"]) == {"subject": "Gap", "description": "Missing"}


def test_an_intake_with_its_own_field_names_still_receives_the_whole_report():
    from tinyassets.patch_intake import _report_outputs

    contract = [{"name": "what_happened", "type": "str", "required": True},
                {"name": "notes", "type": "str", "required": False}]
    assert _report_outputs(contract, "Can't chart", "Tried the chart node") == {
        "what_happened": "Can't chart\n\nTried the chart node"}
    titled = [{"name": "title", "type": "str", "required": True},
              {"name": "details", "type": "str", "required": True}]
    assert _report_outputs(titled, "T", "D") == {"title": "T", "details": "D"}


def test_the_intake_this_platform_offers_is_satisfiable():
    """Live 2026-10-03: every patch request came back `invalid_patch_request`.

    The intake in docs/host-actions.md declares THREE required inputs. No name
    matched, so the old mapper put the whole report in one field and then
    refused the contract because the other two required inputs were empty --
    which the engine surfaced as `invalid_patch_request` on every send. The
    agent's one channel for telling us something is broken was itself broken.
    """
    from tinyassets.patch_intake import _report_outputs

    contract = [{"name": "what_they_tried", "type": "str", "required": True},
                {"name": "what_was_missing_or_broken", "type": "str", "required": True},
                {"name": "request_type", "type": "str", "required": True}]
    outputs = _report_outputs(contract, "Cannot publish", "Asked to publish; no action offered.")

    assert set(outputs) == {f["name"] for f in contract}, (
        "a required input left empty makes the receiver refuse the whole send")
    assert all(value.strip() for value in outputs.values()), "an input carries nothing"
    # The named ones get the body they ask for; the unnamed required one gets the
    # whole report rather than an invented value.
    assert outputs["what_they_tried"] == "Asked to publish; no action offered."
    assert outputs["what_was_missing_or_broken"] == "Asked to publish; no action offered."
    assert outputs["request_type"].startswith("Cannot publish")


def test_every_required_text_input_is_filled_whatever_it_is_called():
    from tinyassets.patch_intake import _report_outputs

    contract = [{"name": f"q{n}", "type": "str", "required": True} for n in range(4)]
    outputs = _report_outputs(contract, "T", "D")
    assert set(outputs) == {"q0", "q1", "q2", "q3"}
    assert set(outputs.values()) == {"T\n\nD"}


def test_a_required_non_text_input_is_refused_by_name():
    """It cannot be filled from a text report, so say which one and why."""
    import pytest

    from tinyassets.patch_intake import _report_outputs

    contract = [{"name": "details", "type": "str", "required": True},
                {"name": "severity", "type": "int", "required": True}]
    with pytest.raises(ValueError, match="severity"):
        _report_outputs(contract, "T", "D")


def test_an_optional_input_is_left_alone():
    """Filling every optional field too would make the owner's tab unreadable."""
    from tinyassets.patch_intake import _report_outputs

    contract = [{"name": "title", "type": "str", "required": True},
                {"name": "details", "type": "str", "required": True},
                {"name": "stack_trace", "type": "str", "required": False}]
    assert _report_outputs(contract, "T", "D") == {"title": "T", "details": "D"}


def test_a_contract_the_old_mapping_satisfied_keeps_its_exact_output_keys():
    """Changing the key set permanently breaks a channel that already worked.

    The sender branch is created once per (command center, principal), and a
    later send refuses outright when the stored output keys no longer match
    (`set(owned.node_defs[0].output_keys) != set(outputs)`). So the fix for the
    refused contracts had to leave every satisfied one untouched -- otherwise
    the people for whom patch requests DID work would be the ones broken, with
    no way back (Codex refute of this PR, finding D).
    """
    from tinyassets.patch_intake import _report_outputs

    # Required `report` + optional `title`: the earlier draft of the fix added a
    # second key here, which an existing branch would then reject forever.
    assert _report_outputs(
        [{"name": "report", "type": "str", "required": True},
         {"name": "title", "type": "str", "required": False}], "T", "D",
    ) == {"report": "T\n\nD"}


def test_no_half_of_the_report_is_ever_dropped():
    """A name matching only the title must not leave the details unsent.

    The earlier draft returned `{"title": "T"}` for a required `title` plus an
    optional `notes` -- the body of the report silently gone, which is worse
    than the loud rejection it replaced.
    """
    from tinyassets.patch_intake import _report_outputs

    for contract in (
        [{"name": "title", "type": "str", "required": True},
         {"name": "notes", "type": "str", "required": False}],
        [{"name": "title", "type": "str", "required": False},
         {"name": "notes", "type": "str", "required": False}],
    ):
        outputs = _report_outputs(contract, "T", "D")
        sent = "\n".join(outputs.values())
        assert "T" in sent and "D" in sent, (outputs, "half the report was dropped")


@pytest.mark.parametrize("optional", [
    "what_they_tried", "what_tried", "tried", "what_was_missing_or_broken",
    "what_was_missing", "missing", "broken",
])
@pytest.mark.parametrize("separate_details", [False, True])
def test_optional_new_aliases_preserve_working_legacy_outputs(optional, separate_details):
    contract = [{"name": "title", "type": "str", "required": True}]
    if separate_details:
        contract.append({"name": "details", "type": "str", "required": True})
    contract.append({"name": optional, "type": "str", "required": False})
    expected = {"title": "T", "details": "D"} if separate_details else {"title": "T\n\nD"}
    assert patch_intake._report_outputs(contract, "T", "D") == expected


@pytest.mark.parametrize("optional", ["what_they_tried", "what_was_missing_or_broken"])
@pytest.mark.parametrize("separate_details", [False, True])
def test_existing_sender_still_delivers_with_optional_new_alias(
    world, monkeypatch, optional, separate_details,
):
    """Reuse the actual persisted sender and link made by the legacy mapping."""
    base, auth, _ = world
    auth("receiver")
    required = ["title", "details"] if separate_details else ["title"]
    inputs = required + [optional]
    branch = BranchDefinition(
        branch_def_id="optional-intake", name="Optional intake", author="receiver",
        visibility="private", entry_point="entry",
        node_defs=[NodeDefinition(
            node_id="entry", display_name="Intake", input_keys=inputs,
            output_keys=["result"], prompt_template="{title}",
        )],
        graph_nodes=[GraphNodeRef(id="entry", node_def_id="entry")],
        edges=[EdgeDefinition("START", "entry"), EdgeDefinition("entry", "END")],
        state_schema=[{"name": name, "type": "str"} for name in required]
        + [{"name": optional, "type": "str", "default": ""}, {"name": "result", "type": "str"}],
    )
    save_branch_definition(base, branch_def=branch.to_dict())
    receiver = receiver_links.save_receiver(
        universe_id="u-receiver", branch_def_id="optional-intake", node_id="entry",
        input_keys=inputs, allowed_senders=["sender"],
    )
    assert [f["name"] for f in receiver["contract"] if not f["required"]] == [optional]
    monkeypatch.setenv(patch_intake.RECEIVER_ID_VAR, receiver["receiver_id"])
    _grant(base, receiver)
    expected = {"title": "T", "details": "D"} if separate_details else {"title": "T\n\nD"}
    # Seed the persisted sender with the exact formerly successful mapping.
    with monkeypatch.context() as legacy:
        legacy.setattr(patch_intake, "_report_outputs", lambda *_: dict(expected))
        first = _send(title="T", details="D")
    assert first.get("sent") is True, first
    before = list_branch_definitions(base, author="sender", viewer="sender")
    second = _send(title="T", details="D")
    assert second.get("sent") is True, second
    assert second["delivery_id"] != first["delivery_id"]
    assert list_branch_definitions(base, author="sender", viewer="sender") == before
    rows = _rows(base, "graph_deliveries")
    assert len(rows) == 2
    assert all(json.loads(row["inputs_json"]) == expected for row in rows)
    assert len(_rows(base, "graph_output_links")) == 1

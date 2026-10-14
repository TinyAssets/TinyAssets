from copy import deepcopy

import pytest

from tests.native_room_fixture import prepare, reply_payload


def fixture():
    return (
        {
            "room_id": "fixture-room",
            "messages": [
                {
                    "id": "fixture-message",
                    "seq": 1,
                    "kind": "message",
                    "participant": "you",
                    "depth": 0,
                    "reply_to": None,
                    "text": "Fixture question",
                    "requested_responders": ["village_elder"],
                }
            ],
        },
        {"saved": True, "operation": "post", "duplicate": False, "message_id": "fixture-message"},
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("saved", False),
        ("operation", "reply"),
        ("operation", "introduce"),
        ("operation", "read"),
        ("duplicate", True),
        ("message_id", "foreign-message"),
    ],
)
def test_non_posts_do_not_dispatch(field, value):
    room, receipt = fixture()
    receipt[field] = value
    assert prepare(room, receipt) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_responders", []),
        ("requested_responders", ["spot"]),
        ("participant", "village_elder"),
        ("kind", "reply"),
        ("depth", 1),
        ("reply_to", "some-parent"),
    ],
)
def test_only_addressed_owner_roots_dispatch(field, value):
    room, receipt = fixture()
    room["messages"][0][field] = value
    assert prepare(room, receipt) is None


def test_native_identity_correlation_and_no_followup():
    room, receipt = fixture()
    dispatch = prepare(room, receipt)
    assert dispatch["agent_id"] == "main"
    reply = reply_payload(dispatch, "Fixture native result")
    assert reply["participant"] == "village_elder"
    assert reply["data"]["reply_to"] == "fixture-message"
    assert reply["data"]["requested_responders"] == []
    assert reply["data"]["allow_followup"] is False
    assert prepare(room, receipt) == dispatch
    room["messages"].append(
        {
            "participant": "village_elder",
            "client_message_id": reply["data"]["client_message_id"],
            "id": "reply-id",
        }
    )
    assert prepare(room, receipt) is None


def test_room_binding_and_bounded_context():
    room, receipt = fixture()
    other = deepcopy(room)
    other["room_id"] = "other-fixture-room"
    assert (
        prepare(room, receipt)["client_message_id"] != prepare(other, receipt)["client_message_id"]
    )
    room["messages"] = [
        {**room["messages"][0], "id": f"fixture-{n}", "seq": n} for n in range(1, 21)
    ]
    receipt["message_id"] = "fixture-20"
    assert len(prepare(room, receipt)["context"]) == 10


@pytest.mark.parametrize("value", [None, "", " ", "x" * 4001])
def test_invalid_native_output_is_not_fabricated_or_truncated(value):
    room, receipt = fixture()
    with pytest.raises(ValueError):
        reply_payload(prepare(room, receipt), value)

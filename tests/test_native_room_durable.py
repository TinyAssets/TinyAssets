from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.native_room_fixture import Dispatch, Room, atomic


@pytest.fixture
def setup(tmp_path):
    room = Room(tmp_path)
    room.init()
    receipt = room.post(
        {
            "client_message_id": "fixture_owner_01",
            "text": "Fixture question",
            "requested_responders": ["village_elder"],
        }
    )
    receipt["message_id"] = receipt["id"]
    dispatcher = Dispatch(tmp_path, clock=lambda: 86400)
    return dispatcher, room, receipt


def test_disabled_by_default(setup):
    dispatcher, _, receipt = setup
    assert dispatcher.claim(receipt) == {"route": "stop", "reason": "disabled"}
    assert not dispatcher.path.exists()


def test_one_claim_concurrent_and_no_uncertain_generation_retry(setup):
    dispatcher, _, receipt = setup
    dispatcher.configure(enabled=True)
    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(lambda _: dispatcher.claim(receipt), range(2)))
    assert sorted(a["route"] for a in answers) == ["generate", "stop"]
    assert dispatcher.claim(receipt) == {"route": "stop", "reason": "generating"}
    assert len(dispatcher._load()["records"]) == 1


def test_real_result_and_replayed_commit_are_one_correlated_reply(setup):
    dispatcher, room, receipt = setup
    dispatcher.configure(enabled=True)
    claim = dispatcher.claim(receipt)
    answer = dispatcher.finish(claim["ticket"], claim["token"], "Actual fixture model output")
    assert dispatcher.finish(claim["ticket"], claim["token"], "different") == answer
    doc = room.load()
    assert len(doc["messages"]) == 2
    reply = doc["messages"][-1]
    assert reply["text"] == "Actual fixture model output"
    assert reply["reply_to"] == receipt["id"]
    assert reply["requested_responders"] == [] and reply["allow_followup"] is False
    assert reply["attribution_level"] == "owner_relay"
    assert dispatcher.claim({**answer["receipt"], "message_id": reply["id"]})["route"] == "stop"


def crash():
    raise RuntimeError("Fixture crash")


@pytest.mark.parametrize("boundary", ["after_save", "after_post"])
def test_crash_recovery_reuses_saved_result_without_another_model(setup, boundary):
    dispatcher, room, receipt = setup
    dispatcher.configure(enabled=True)
    claim = dispatcher.claim(receipt)
    with pytest.raises(RuntimeError, match="Fixture crash"):
        dispatcher.finish(
            claim["ticket"], claim["token"], "Actual model result", **{boundary: crash}
        )
    recovered = Dispatch(room.home)
    assert recovered._load()["records"][claim["ticket"]]["phase"] == "ready"
    replay = recovered.claim(receipt)
    assert replay["route"] == "commit"
    recovered.finish(replay["ticket"], replay["token"])
    assert len(room.load()["messages"]) == 2


@pytest.mark.parametrize("phase", ["before_claim", "generating", "ready"])
def test_revocation_stops_queued_and_inflight_reply(setup, phase):
    dispatcher, room, receipt = setup
    dispatcher.configure(enabled=True)
    if phase != "before_claim":
        claim = dispatcher.claim(receipt)
        if phase == "ready":
            with pytest.raises(RuntimeError):
                dispatcher.finish(
                    claim["ticket"], claim["token"], "Actual result", after_save=crash
                )
    dispatcher.configure(enabled=False)
    if phase != "before_claim":
        assert (
            dispatcher.finish(claim["ticket"], claim["token"], "late result")["status"] == "revoked"
        )
    assert dispatcher.claim(receipt)["route"] == "stop"
    dispatcher.configure(enabled=True)
    if phase != "before_claim":
        assert dispatcher.claim(receipt)["reason"] == "revoked"
    assert len(room.load()["messages"]) == 1


def test_invalid_claim_and_output_cannot_commit(setup):
    dispatcher, room, receipt = setup
    dispatcher.configure(enabled=True)
    claim = dispatcher.claim(receipt)
    with pytest.raises(PermissionError):
        dispatcher.finish(claim["ticket"], "wrong", "forged")
    with pytest.raises(ValueError):
        dispatcher.finish(claim["ticket"], claim["token"], "")
    assert len(room.load()["messages"]) == 1
    assert dispatcher.claim(receipt)["reason"] == "generating"


def test_deleted_source_refuses_commit(setup):
    dispatcher, room, receipt = setup
    dispatcher.configure(enabled=True)
    claim = dispatcher.claim(receipt)
    doc = room.load()
    doc["messages"] = []
    atomic(room.path, doc)
    with pytest.raises(PermissionError):
        dispatcher.finish(claim["ticket"], claim["token"], "actual result")


def test_changed_source_refuses_commit(setup):
    dispatcher, room, receipt = setup
    dispatcher.configure(enabled=True)
    claim = dispatcher.claim(receipt)
    doc = room.load()
    doc["messages"][0]["text"] = "Changed intent"
    atomic(room.path, doc)
    with pytest.raises(PermissionError, match="changed"):
        dispatcher.finish(claim["ticket"], claim["token"], "actual result")


def test_quota_counts_uncertain_turn_and_survives_toggle(setup):
    dispatcher, room, receipt = setup
    dispatcher.configure(enabled=True)
    dispatcher.claim(receipt)
    second = room.post(
        {
            "client_message_id": "fixture_owner_02",
            "text": "Second",
            "requested_responders": ["village_elder"],
        }
    )
    second["message_id"] = second["id"]
    assert dispatcher.claim(second)["reason"] == "turn_limit"
    dispatcher.configure(enabled=False)
    dispatcher.configure(enabled=True)
    assert dispatcher.claim(second)["reason"] == "turn_limit"


@pytest.mark.parametrize(
    "field,value", [("enabled", "yes"), ("max_total", True), ("max_daily", 0), ("max_total", 2001)]
)
def test_invalid_activation_limits_refused(setup, field, value):
    dispatcher, _, _ = setup
    args = {"enabled": True, field: value}
    with pytest.raises(ValueError):
        dispatcher.configure(**args)

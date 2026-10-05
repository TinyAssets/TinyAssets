"""Run actual browser send/history functions against deterministic transport."""

import time

import pytest

from tests.test_onboarding_app import _run_app

NOTICE = (
    "The turn did not complete. Actions may already have occurred. "
    "Check progress before sending again."
)
FAILURE = {"version": 1, "kind": "turn_failed", "code": "unknown"}


def payload(saved=True):
    return {
        "error": "raw legacy live error",
        "turn_failure": FAILURE,
        "failure_notice": NOTICE,
        "history_saved": saved,
    }


def pair(original="original", **updates):
    stamp = time.time()
    return [
        {"speaker": "founder", "text": original, "truncated": False, "ts": stamp, **updates},
        {"speaker": "platform", "text": NOTICE, "ts": stamp, "failure": FAILURE},
    ]


@pytest.mark.parametrize("kind", ["send", "voice"])
def test_live_saved_failure_clears_local_slot_without_fake_answer(tmp_path, kind):
    result = _run_app(
        tmp_path, {"kind": kind, "message": "original", "payload": payload(), "expectFailure": True}
    )
    assert result["inflight"] is None
    assert [m["role"] for m in result["messages"]] == ["founder", "platform"]
    assert result["messages"][-1]["text"].startswith(payload()["error"])
    assert "Check progress before sending again" in result["messages"][-1]["text"]
    assert not result["executionDetails"] and not result["observedModels"]
    assert result["converseCalls"] == ["original"]


def test_saved_failure_retry_is_a_new_explicit_turn_and_keeps_old_failure(tmp_path):
    result = _run_app(
        tmp_path,
        {
            "kind": "send",
            "message": "original",
            "clickResend": True,
            "payloads": [payload(), {"reply": "new answer"}],
        },
    )
    assert result["converseCalls"] == ["original", "original"]
    assert result["notesRemoved"] == 0
    assert [m["role"] for m in result["messages"]] == ["founder", "platform", "founder", "universe"]


def test_unsaved_failure_keeps_recovery_and_says_it_is_unsaved(tmp_path):
    result = _run_app(tmp_path, {"kind": "send", "message": "original", "payload": payload(False)})
    assert result["inflight"]["message"] == "original"
    assert "not saved" in result["notes"][0]["text"]
    assert result["notes"][0]["buttons"] == ["Send it again"]
    assert result["converseCalls"] == ["original"]


def test_restored_saved_failure_does_not_repeat_owner_or_resend_automatically(tmp_path):
    result = _run_app(tmp_path, {
        "kind": "restore", "pending": "original", "pendingSendId": "failed-send",
        "history": pair(client_send_id="failed-send"),
    })
    assert [m["role"] for m in result["messages"]] == ["founder", "platform"]
    assert result["inflight"] is None and result["converseCalls"] == []
    assert result["notes"][0]["buttons"] == ["Send it again"]
    assert not result["observedModels"]


def test_restored_notice_can_resend_full_original_only_after_click(tmp_path):
    original = "  exact Ω\r\noriginal\n"
    result = _run_app(
        tmp_path,
        {
            "kind": "restore",
            "history": pair(original),
            "clickAfterRestore": "Send it again",
            "payload": {"reply": "answer"},
        },
    )
    assert result["callsAfterRestore"] == []
    assert result["converseCalls"] == [original]
    assert result["notesAfterClick"] == [NOTICE]


@pytest.mark.parametrize("case", ["truncated", "missing", "wrong_speaker", "wrong_time"])
def test_incomplete_original_never_gets_a_partial_or_guessed_retry(tmp_path, case):
    history = pair()
    if case == "truncated":
        history[0]["truncated"] = True
    elif case == "missing":
        history = history[1:]
    elif case == "wrong_speaker":
        history[0]["speaker"] = "platform"
    else:
        history[0]["ts"] -= 1
    result = _run_app(tmp_path, {"kind": "restore", "history": history})
    assert all(not row["buttons"] for row in result["notes"])
    assert "full original" in result["notes"][-1]["text"]
    assert result["converseCalls"] == []


def test_legacy_platform_row_is_not_owner_or_answer_even_without_metadata(tmp_path):
    history = pair()
    history[1].pop("failure")
    result = _run_app(tmp_path, {"kind": "restore", "history": history})
    assert [m["role"] for m in result["messages"]] == ["founder", "platform"]
    assert not result["observedModels"]


def test_platform_text_cannot_falsely_confirm_an_inflight_owner_message(tmp_path):
    result = _run_app(
        tmp_path,
        {
            "kind": "restore",
            "pending": "same text",
            "history": [{"speaker": "platform", "text": "same text", "ts": time.time()}],
        },
    )
    assert result["inflight"]["message"] == "same text"
    assert [m["role"] for m in result["messages"]] == ["platform", "founder"]


def test_setup_hold_keeps_a_connection_recovery_button_without_fake_answer(tmp_path):
    held = {
        "status": "held",
        "reason": "setup_required",
        "history_saved": True,
        "turn_failure": {**FAILURE, "code": "setup_required"},
        "failure_notice": NOTICE,
        "note": "Connect your own model to begin; no other user's account will be used.",
    }
    result = _run_app(tmp_path, {"kind": "send", "message": "hello", "payload": held})
    assert result["inflight"] is None
    assert result["notes"][0]["buttons"] == ["Send it again", "Connect a model"]
    assert [m["role"] for m in result["messages"]] == ["founder", "platform"]
    assert result["messages"][-1]["text"] == held["note"]


def test_live_failure_without_richer_copy_uses_fixed_notice(tmp_path):
    response = payload()
    response["error"] = ""
    response["status"] = "held"
    response["note"] = {"unexpected": "not a sentence"}
    result = _run_app(tmp_path, {"kind": "send", "message": "hello", "payload": response})
    assert result["messages"][-1]["text"] == NOTICE

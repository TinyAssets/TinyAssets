"""Every consumer reason a user can see carries a next step they can act on."""

from __future__ import annotations

import pytest

from tinyassets.consumer_reason_actions import consumer_next_action


def test_provider_mismatch_names_both_providers_and_both_remedies():
    """The cold desktop test 2026-08-25 relayed this reason with no remedy."""
    action = consumer_next_action("provider_mismatch:automation=claude-code,serving=codex")
    assert "claude-code" in action and "codex" in action
    assert "rebind" in action.lower()
    assert "serving provider" in action
    assert "will not switch providers for you" in action


@pytest.mark.parametrize(
    "reason",
    [
        "no_due_trigger",
        "production_declined",
        "no_prepared_continuation",
        "provider_binding_missing",
        "no_daemon_for_principal",
        "no_serving_runtime",
        "not_an_automation_task",
        "consumer_not_applicable:assigned_cloud_automation",
        "consumer_not_applicable:automation_branch_version_missing",
        "refusal_unexplained",
        "requires_executor_class:tray",
        "reconcile_error:PermissionError:cloud background attempt is unavailable",
        "activate_error:CloudContinuationActivationError:assignment mismatched",
        "produce_error:PermissionError:x",
        "prepare_error:PermissionError:x",
        "claim_error:RuntimeError:x",
        "explain_error:RuntimeError:x",
        "binding_runtime_missing",
        "attempt_not_reserved",
    ],
)
def test_every_known_reason_has_an_actionable_step(reason):
    action = consumer_next_action(reason)
    assert action, reason
    assert len(action) > 40, reason


def test_unknown_and_empty_reasons_are_blank_not_invented():
    assert consumer_next_action("") == ""
    assert consumer_next_action("something_new_we_have_not_seen") == ""
    assert consumer_next_action(None) == ""  # type: ignore[arg-type]


def test_requires_executor_class_names_the_executor():
    assert "tray" in consumer_next_action("requires_executor_class:tray")


def test_served_deposit_guidance_matches_the_shipped_form():
    """Cold desktop test 2026-08-25: the universe told the founder to paste the four
    OAuth keys 'one per line' (the form has had four labelled boxes since #2536) and
    named an older button label it had learned. Served guidance is the UI copy the
    agent relays, so it must match the shipped form.

    Rewritten 2026-08-27 to pin the INVARIANT rather than a snapshot. The original
    hardcoded a control that has since been cut, so it failed the moment the UI
    moved — which is the opposite of what a drift test is for. It now compares
    the two files against each other: whatever the form ships, the guidance must
    agree, and a control that is gone must not be named.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "tinyassets" / "engine_mcp_server.py"
    text = source.read_text(encoding="utf-8")
    app_path = source.parent / "onboarding" / "app.html"
    from tinyassets.onboarding.app_modules import shell_source

    app = shell_source(app_path)

    # The original defect: four labelled boxes described as one field.
    assert "one per line" not in text

    # As long as the four-box OAuth form ships, the guidance about it must too —
    # a hand deposit is still reachable, so the copy is still load-bearing.
    if "http-oauth1a-api-key" in app:
        assert "FOUR\n    LABELLED BOXES" in text or "FOUR LABELLED BOXES" in text

    # Never name a control the app does not have. The nav button was cut on
    # 2026-08-27; asking is the primary route now.
    label = "Connect / add API connection"
    if label in app:
        assert label in text, "the app has the button; the guidance must name it"
    else:
        assert label not in text, (
            "the guidance names a button the app no longer has - that is the "
            "exact drift this test exists to catch"
        )

    # And the agent must be told to ask rather than to send the user hunting.
    assert "ASK THEM FOR IT" in text
    assert "never send" in text or "never send them" in text

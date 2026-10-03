"""What a user should DO about each consumer-pump / task-refusal reason.

Live cold test 2026-08-25 (desktop app, founder's own universe): asked "why isnt my
background loop running?", the universe correctly relayed
`provider_mismatch:automation=claude-code,serving=codex` from its own status - and
then offered to investigate, because the reason string carries no remedy. Machine
reasons need a human next step next to them, exactly as the run-failure taxonomy
does (#2550). These strings ARE the words the agent relays to the user.
"""

from __future__ import annotations

#: The recorded disposition of a fleet-era cloud-automation control when its
#: pump was retired (plan C1, 2026-09-28). Written by the consumer, carried on
#: the legacy row the owner's automation list shows.
RETIRED_FLEET_CONTROL_REASON = (
    "retired_fleet_era: this automation ran on the retired cloud-automation "
    "layer and has been stopped; recreate it with write_graph target=automation"
)

_STATIC: dict[str, str] = {
    "no_due_trigger": (
        "Nothing is due yet - the next slice is scheduled by this automation's "
        "cadence. Nothing to fix."
    ),
    "production_declined": (
        "The next slice passed every check but was still not produced. Read the "
        "automation's current trigger and prepared continuation; if its trigger is "
        "held by a claim that has expired, resume the automation to re-arm it."
    ),
    "no_prepared_continuation": (
        "This automation has no prepared continuation, so there is nothing to run. "
        "Re-create or rebind the automation to prepare one."
    ),
    "provider_binding_missing": (
        "The compute authority this automation was created with no longer exists. "
        "Rebind the automation to a provider you currently have."
    ),
    "no_daemon_for_principal": (
        "No runtime is registered for this owner in this command center, so nothing can "
        "execute. Select a serving provider for the command center to register one."
    ),
    "no_serving_runtime": (
        "This command center has no serving provider selected, so no runtime exists to "
        "run background work. Choose one (registering a provider is not selecting "
        "it)."
    ),
    "legacy_control_tasks_parked": (
        "This command center IS serving: chat, runs, and your automations and schedules "
        "fire on your selected provider. One kind of queued item left from before "
        "the host workers were removed (a cloud control task) has no runner on "
        "this release and stays parked; nothing you build depends on it. Nothing "
        "to select and nothing to reconnect."
    ),
    "not_an_automation_task": (
        "This queued item is not an automation task, so the automation worker will "
        "never run it. Re-queue the work as an automation, or cancel it."
    ),
    "consumer_not_applicable:assigned_cloud_automation": (
        "This queued item is a plain owner-queued run, not a cloud automation, so "
        "the automation worker is not its executor. Re-queue it as an automation, "
        "or cancel it."
    ),
    "consumer_not_applicable:automation_branch_version_missing": (
        "This queued item names no pinned workflow version, so nothing can execute "
        "it safely. Re-queue it from the automation."
    ),
    "refusal_unexplained": (
        "The worker declined this item and could not say why - please report it; "
        "the run itself is untouched and still pending."
    ),
}


def consumer_next_action(reason: str) -> str:
    """One plain-language next step for a consumer reason ('' when unknown)."""
    if not isinstance(reason, str) or not reason:
        return ""
    if reason in _STATIC:
        return _STATIC[reason]
    if reason.startswith("provider_mismatch:"):
        automation = serving = ""
        for part in reason.split(":", 1)[1].split(","):
            key, _, value = part.partition("=")
            if key.strip() == "automation":
                automation = value.strip()
            elif key.strip() == "serving":
                serving = value.strip()
        automation = automation or "another provider"
        serving = serving or "nothing"
        return (
            f"This automation runs on {automation}, but this command center currently "
            f"serves {serving}, and background work only runs when the two match. "
            f"Either rebind the automation to {serving}, or make {automation} the "
            "universe's serving provider. Both are your choice - TinyAssets will "
            "not switch providers for you."
        )
    if reason.startswith("requires_executor_class:"):
        executor = reason.split(":", 1)[1] or "another executor"
        return (
            f"This item is for a {executor} executor, which is not the cloud "
            "automation worker. Run it on that executor, or re-queue it as a cloud "
            "automation."
        )
    if reason.startswith("reconcile_error:"):
        return (
            "A finished run's receipt could not be reconciled. It no longer blocks "
            "the loop, but the receipt stays unresolved - report it if the same "
            "automation keeps stalling."
        )
    if reason.startswith(("activate_error:", "produce_error:", "prepare_error:")):
        return (
            "The worker hit an error preparing this automation's next slice (the "
            "message after the exception name is the cause). Nothing was changed; "
            "resume the automation to retry, and report it if it repeats."
        )
    if reason.startswith("claim_error:") or reason.startswith("explain_error:"):
        return (
            "The worker errored while claiming this item; it is still pending and "
            "will be retried on the next poll."
        )
    if reason.startswith("binding_") or reason.startswith("attempt_"):
        return (
            "The stored authority for this item no longer matches the workflow it "
            "pins. Resume or rebind the automation to mint a current one."
        )
    return ""


__all__ = ["consumer_next_action"]

"""Names of retired writers whose existing home entries still need classification.

C7 removed the old subscription and idle-cycle writers. The node-bid package
and the wiki uptime probe are gone too. These names remain platform state so an
existing home entry is never guessed to be agent-editable content.
"""

PLATFORM_NAMES = frozenset({
    "subscriptions.json", ".idle_cycle_stamp.json", "bid_ledger.json",
    "settlements", "uptime-probe",
})
PLATFORM_LOCK_NAMES = frozenset({
    ".subscriptions.json.lock", ".branch_tasks.json.lock",
    ".bid_execution_log.json.lock",
})

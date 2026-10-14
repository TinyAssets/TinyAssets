"""Names of retired writers whose existing home entries still need classification.

C7 removed the old subscription and idle-cycle writers. These names remain
platform state during the command-center cutover so old data is never guessed
to be agent-editable content.
"""

PLATFORM_NAMES = frozenset({"subscriptions.json", ".idle_cycle_stamp.json"})
PLATFORM_LOCK_NAMES = frozenset({
    ".subscriptions.json.lock", ".branch_tasks.json.lock",
})

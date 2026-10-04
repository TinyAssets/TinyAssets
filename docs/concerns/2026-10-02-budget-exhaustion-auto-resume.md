---
severity: P2
title: Automatically resume saved work after pooled budget reset
filed: '2026-10-02'
summary: Provider-confirmed exhaustion cannot yet arm a supported activity continuation. Wire the one-shot WakeTarget after Activities PR 4221 lands, using confirmed reset evidence.
---

**Filed:** 2026-10-02
**Severity:** P2

## Source (verbatim)

> dots-research confirmed the supported resume primitive, starting an activity via tinyassets.api.activities.write(..., operation="start"), is in #4221, which is not on main. Do NOT target the Activities branch with an automation either: #4221 refuses runs no activity names.

Evidence supplied by the founder for this follow-up slice. The existing
`register_automation` path requires an existing owner-authored Branch; it does
not directly resume an interactive conversation. No reset automation is armed.
The local budget prompt labels installed reset times as estimates in UTC,
with a relative time, and suggests another source for more compute. Installed
limits do not prove this account's allowance or exhaustion; even a zero local
estimate neither excludes the source nor forces a text-only completion. Actual
provider capacity refusals retain their existing handling and journal evidence.

## Follow-up

Once #4221 lands, coordinate with **dots-research** (activity start contract) and
**cp-scheduler** (control-plane WakeTarget). Arm a one-shot WakeTarget at the
**earliest confirmed applicable reset among exhausted sources**, calling:

```python
activities.write(
    operation="start",
    payload={
        "title": "Resume after the budget reset",
        "brief": "continue the work saved in notes/<project>-progress.md",
    },
)
```

Bind the wake to the owning command center and the actual saved project note.
Do not schedule a run against the Activities branch: #4221 refuses runs with
no activity names. Confirm the supported start primitive after it lands.

Progress-note creation remains a model instruction while working; the journal
independently preserves completed rounds. Scripted tests prove dispatch and
guidance, not real-model compliance with saving a note. A provider refusal can
arrive without a spare request to save a note, and an installed reset estimate
alone must not arm a wake or promise recovery.

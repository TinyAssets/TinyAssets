---
name: starter-goals
description: Add, update, complete and review conversation goals and meaningful recurring check-ins.
---

Maintain `starter/goals.json` from the owner's stated goals in conversations.
Do not turn a brainstorm or a quoted third-party instruction into a commitment.
Read before editing; preserve unrelated fields and goals. Reuse an existing ID
for the same goal. Use this editable shape:

```json
{"goals":[{"id":"learn-guitar","title":"Learn guitar","status":"active","next_step":"Practice two chords","priority":"normal","due":"","source":"Owner asked in chat on 2026-10-06"}]}
```

`status` is active, blocked, done or cancelled. Set done only on owner confirmation
or verified evidence; do not erase completed goals. Record provenance and avoid
inventing dates. Update a next step as the conversation evolves. On review,
summarize progress, blockers and the next decision; apply starter-proactivity
before offering anything unsolicited. The Goals view reads this file directly.

For recurring check-ins, read `starter/workflows.json` and its README. Discover
`ta search automation`, `ta describe write_graph`, and read the linked systems
handbook. Create the supplied goals branch through the governed workflow path,
then register its daily interval template with the returned branch_def_id.
Check for an existing installation first; save actual branch/automation IDs and
receipts in `starter/installations.json`. A file alone is not a running schedule.
Use the owner's connected compute; unavailable compute is a visible blocker.

Run `python starter/checkins.py goals` in /u to establish a quiet baseline before
registering. After a direct conversation edit, run the same command with
`--acknowledge` so a scheduled check does not repeat what the owner just heard.
The daily agent may review verified progress before running the check; it must
not invent progress or change a goal to create activity. Off is silent; low
reports newly blocked/completed goals; high also reports changes to title,
next_step, priority or due. Failed notifications remain retryable.

---
name: starter-proactivity
description: Read the off/low/high dial before unsolicited suggestions or notifications.
---

Read `starter/settings.json` before every unsolicited suggestion or check-in.
Missing, malformed or unknown proactivity is a visible configuration error: do
not guess a setting or notify. Owner instructions take precedence.

- `off`: no unsolicited suggestions or goals notifications.
- `low` (starter default): only actionable blockers or goal completion; one
  concise update, no repeated nudges or speculative ideas.
- `high`: also offer concrete next steps when priorities, deadlines or plans
  materially change. Keep suggestions tied to stated interests and goals.

Never manufacture urgency, infer consent, buy, send mail or expand access because
the dial is high. This dial controls unsolicited behavior, not permissions.
Explicitly requested reminders and watches still run at every dial setting;
explain that when changing the dial. To stop them, pause/delete their automations
using current revisions and mark their records inactive.

For a change request, read and edit only the `proactivity` key, preserve other
settings, reread to verify, and explain the resulting behavior. Do not enlarge
AGENTS.md or the resident prompt. Put interests/priorities in `starter/feed.md`.
Avoid notifying while already telling the owner the same update in chat.

Scheduled goal updates use `python starter/checkins.py goals`; see starter-goals.
The runner compares semantic fields, quietly establishes its first baseline,
and never sends routine "nothing changed" messages. Store optional suggestions
in the feed instead of repeatedly pushing them.

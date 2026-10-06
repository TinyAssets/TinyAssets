Still reviewing — now checking the app_ui bridge response shapes the starter UI relies on.

Checking how notify dedup handles a repeated identical notification.

Verifying the app_ui operations and automation fields the skills name.

The package-only draft is correct and honest about what it doesn't do. Two bugs need small fixes before merge.

**What's sound (checked against the code on main):**
- **Bridge contract:** the UI calls `read_file`, `list_files`, `read_conversation`, `open_chat` and `send_message`. Each matches what the parent page actually returns (`tinyassets/onboarding/app_ui.js:807-816` and `:1015-1044`), including the newest-first chat order, the pagination fields and the "binary file" signal. Everything renders as plain text (`textContent`), not HTML.
- **Notify path:** `ta write_graph --json` with `pending_request`/`notify` ends at `agent_notifications.notify` (`tinyassets/engine_mcp_server.py:3407`). If the same notification is already waiting, it returns the existing one with `created:false` plus its `request_id`. So the check-in script dedups as the README describes and doesn't claim exactly-once delivery. A failed notify raises before the state file is written, so the change is retried next time; the write is atomic.
- **Dial:** the first goals run records a silent baseline. `off` sends no goal updates; `low` reports only newly blocked or done goals; `high` reports any change to the tracked fields. Requested watches and reminders ignore the dial, as the skills, README and spec all say. Edits to `updated_at` alone don't trigger anything.
- **Skills describe real operations:** `add_ui`/`replace_ui` (`tinyassets/custom_agents.py:1884`), `app_ui_preview`, `next_due_at`, `write_graph` branch/automation create, and the scheduler's overlap policies all exist.
- **No overclaiming:** no platform runtime, permission or provisioning changes. `AGENTS.md` and the hooks are byte-identical (tested). The concern file, `verification.md` and `tasks.md` 2.4–2.5 say plainly there's no installer, no `ta` skill route, no deployment and no real-user pass yet. No collision with D10 or K2: there's no second installer.

**Findings**

1. **A muted notification causes a failure every run, forever** (correctness, medium). See `tinyassets/starter/muse/checkins.py` (raise after `send(...)`) and `tinyassets/storage/pending_requests.py:370-380`.
   - If the owner has said "don't notify me again" for that exact title and body, notify returns `{"settled": True, "decision": "declined", ...}` with no `request_id`. The script raises "Notification not acknowledged" and never writes its state.
   - So every later run fails the same way. With the 60-second reminder template, that's about 1,440 failing agent runs a day on the owner's compute, until a file is edited by hand.
   - Fix: treat `receipt.get("settled")` as the owner's answer. Write the state and return `{"notified": False, "suppressed": receipt["decision"], ...}`. Add one test with a send stub that returns a settled receipt.

2. **Garbled characters in the shipped UI** (correctness, low). See `tinyassets/starter/muse/command-center.json` (and its plugin mirror).
   - The script literally contains `say('Loading?')` and `' ? Due '`. A non-ASCII character (probably `…` and `·`) was lost when the file was written.
   - Every goal card with a due date shows "active ? Due 2026-10-07", and the loading line reads "Loading?".
   - Fix: use `'Loading…'` and `' · Due '` (or plain ASCII like `' - Due '`), then rebuild the mirror.

3. **One test proves nothing** (test honesty, low). `test_publisher_does_not_touch_owner_edits_or_deleted_files` (`tests/test_starter_muse_package.py:191`) calls a pure function that never writes files, so it can't fail.
   - Its name suggests edit and deletion preservation is covered, but that is D10's install policy and is still open (task 2.4).
   - Fix: delete it, or rename it to say the publisher doesn't write to the filesystem.

4. **Advisory, not blocking:**
   - Notify rejects bodies over 8,000 characters. Many updates batched into one notification would fail on every run in the same way as finding 1. Truncating the body or splitting it into several notifications fixes it.
   - The reminder template's 60-second default means one agent run per minute while any reminder is active. The skill says to pause when none remain, but a coarser default (for example 300 seconds) would cost less.

I didn't run any tests; this verdict comes from reading the diff and the code it relies on.

VERDICT: ADAPT

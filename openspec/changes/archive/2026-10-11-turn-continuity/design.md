## Context and evidence

Read-only production inspection found both mortgage exports under the persistent Docker volume `tinyassets-data`, in `<home>/.agent-workspace/exports/`. XLSX: 5,632 bytes; CSV: 468 bytes; both modified 2026-10-05 07:24:36Z. The saved request/table are transcript rows 2130/2131; chart request/denial are 2157/2158 in the same principal session. Times in the store are 00:24–00:52 PDT, not the reported 12:24–12:52.

`load_recent` loads 20 messages each turn; `format_history` drops oldest messages to fit 7,000 characters and clips a single oversized message. No summarizer replaces discarded context. Both incident receipts report claude-code / claude-opus-5-5. That adapter starts a fresh `claude -p` without a resume handle; the stored native thread record is an older Codex session, not the incident's Claude conversation. The mortgage exchange is outside the 20-message window by the chart turn, independently of deployment.

The tool jail binds `<home>/.agent-workspace` as read-write `/u` and starts there, with allowed platform-root entries overlaid. This survives fresh tool processes and container replacements through `/data`. The harness inventory instead reads only platform-root notes/prompts/workflows, omitting exports. Provider-jail files are a different view with hidden workspace masked. Exact historical search arguments are not available from the transcript; do not claim that a particular search command ran or that files were deleted. `.agent-sessions/history.db` is harness-edit undo history, not the conversation store.

## Decisions

Reconstructing the chart turn with the production formatter selected rows 2137-2156 (20 messages). The character ceiling then retained only rows 2149-2156, rendering 6,597 characters; no mortgage text survived. This is deterministic prompt-window loss, not a session-key reset.

1. Keep the existing storage shape. No copy/move, backfill, new database, or provider registration is required. Production `/cc` provider configuration is unrelated to the served `/u` tools used here.
2. Extend bounded inventory to the agent workspace using the existing no-follow metadata reader. Show logical `/u` paths, respect root overlays, and never follow symlinks. A preview is not an exhaustive filesystem search; include instructions to inspect `/u` recursively when something is absent.
3. Reuse `read_graph target=conversation` and its verified owner/session binding. Add literal case-insensitive substring search via existing `query`, bounded previews and keyset paging; full message reads remain lossless character chunks. The internal owner history door forwards the same optional query. No caller-controlled session/store selector is introduced.
4. Teach every served harness that recent context is a window, and give the concrete retrieval route before claiming prior work never existed. Preserve transcript-as-untrusted-evidence and current-turn consent rules.

## Risks and verification

Regression tests first reproduce omitted exports and unavailable topic search after 26 newer messages. Verify exact retrieval, pagination, foreign-session exclusion, root overlays, links, restart persistence with the real Linux jail, Windows tests, Linux oracle, ruff, mirrors and test hygiene. Search scans the retained session without a new index; returned data stays bounded. Deploy and real-user proof remain delivery steps after the requested draft PR; no shipped claim before deployed-SHA assertion.

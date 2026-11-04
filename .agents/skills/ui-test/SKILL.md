---
name: ui-test
description: Drive the live TinyAssets MCP connector as a real Claude.ai or ChatGPT user would, in a rendered tab the host can watch. Use when a change needs live end-user proof. No MCP bypass, no DOM tricks.
---

# ui-test

You are a naive, curious person chatting with Claude.ai or ChatGPT through the
TinyAssets connector at `https://tinyassets.io/mcp`: you type into the chat box and
read the rendered reply. If the chatbot does not understand you, that is a finding.

**The only proof is a rendered chatbot conversation** through the live installed
connector, in a tab the host can watch. Scripts, canaries, screenshots and direct
MCP calls are navigation aids, never proof.

## Routes

- **Claude Code:** `python scripts/claude_chat.py` (CDP on `localhost:9222`,
  visible Chrome profile). `ask "<prompt>"` types and returns the reply; `read`
  re-reads the last message; `new-chat`; `dismiss-dialogs`; `tabs`; `status`.
- **ChatGPT:** `python scripts/chatgpt_chat.py`, same CDP and profile, for
  Developer Mode with the connector installed.
- **Codex / other:** any route that keeps the same live tab visible to the host.

`claude_chat.py` **launches Chrome itself** — the host does not need to.
`status` is a reporter, not a gate: it connects with auto-launch off, so
"Cannot connect to Chrome CDP" means "not running this instant", not "ask the
host". Run `new-chat` (auto-launches, sends nothing), then re-check. The Chrome
binary is auto-detected as the newest Playwright chromium, overridable with
`TINYASSETS_CHROME_BIN` — never re-pin a build number; the old pin
(`chromium-1208`) crashes on this host and auto-launch then fails with a bare CDP
error that hides the cause.

Preflight: `references/preflight-and-setup.md`.

## Both clients must accept every tool shape

Rule: cross-client MCP alignment is a project prerequisite. ChatGPT (Apps SDK strict surface)
requires `structuredContent` + `content` + `_meta` on a substrate-changing call;
Claude.ai (Anthropic MCP) tolerates `content` alone. Divergence is OUR bug. Before a
door tool's shape (`platform/doors`) changes, run the same call through both clients: no wedge,
no 424, no silent timeout. "Direct MCP call works fine" is INSUFFICIENT; so is one
client. No both-client verification, no merge: stop and say so.

## Identity: incognito is not anonymous

Every mission runs in **Claude.ai's own Incognito chat** (the UI toggle, not a
browser incognito window), on before the first prompt: a long-lived chat carries
context and approvals no new user has. Connector state is part of the test — do not
pre-wire it and then claim the flow works.

**Incognito only stops chat persistence. It does not clear cookies or give you a
fresh identity.** On 2026-07-21 a mission concluded an anonymous caller could read
the host's data and escalated it as P0 twice; the profile held live
`*.authkit.app` / `*.workos.com` cookies the whole time and three findings were
retracted. Before any claim about anonymous or unauthenticated behaviour:

1. Check the cookie jar first — `browser.contexts[0].cookies()`, filtered for
   `authkit` / `workos` / `tinyassets`. Non-empty means you are NOT anonymous.
2. A clean profile is not enough either: Claude.ai connectors are **account-level**,
   so a fresh `--user-data-dir` on the same account shows the connector attached and
   authorized with zero cookies. An empty jar can only disprove anonymity, never
   establish it. Genuine first contact needs the connector REMOVED from the account
   (a change to the host's settings — ask first), then re-added.
3. Corroborate out of band: an unauthenticated `curl` getting 401 while the chat
   succeeds proves the chat is authenticated. Any such mismatch means your premise
   is wrong; stop before escalating.

**Prove the resolved principal from status,** never from cookies or UI state. Ask
the bot in user language to check the connector's status; the rendered result must
carry `request_identity.bearer_present` and a versioned
`request_identity.principal_fingerprint`. `identity_fingerprint_unavailable`, a
missing fingerprint, or disagreement between `get_status` and
`read_graph target=status` is a hard acceptance failure. A two-founder proof needs
two distinct fingerprints through ordinary connector OAuth. Record only the
fingerprint or an approved alias — never the raw subject, bearer, refresh token,
cookie, email or credential. `bearer_present=false` proves only that this request
carried no bearer.

## Tab hygiene, every step

One visible chatbot tab, start to end: if a second exists the host cannot see what
you are doing, and you should be the one who notices. Check before every prompt and
after anything that might have navigated — links, OAuth, redirects and Claude.ai's
own UI all spawn tabs. Never call `new_tab` / `open_tab` / `window.open`. Seeing
more than one: stop, close the others, log how it appeared, resume. Log each check:
`## [...] TAB HYGIENE: 1 tab, incognito=ON, URL=...`.

## The per-tool approval dialog

The connector pops an approval dialog the FIRST time the bot invokes each tool
name, which can be mid-mission — prompt 4, when it first reaches for a tool it has
not used this session. **Check "Always allow" / "Don't ask again" BEFORE clicking
Approve**, or every later call to that tool re-prompts and the mission stalls.
`ask` calls `dismiss-dialogs` automatically, and dismissing without the toggle
defeats the purpose, so set it yourself on each new tool's first dialog. Log
`## [...] USER NOTE always-allowed <tool>`. No progress for >30s after an `ask`
that should have called a tool usually means a hidden dialog is waiting;
`status` does not report dialog state.

## When the bot shows options

`ask` always types free text, so **answer options in words** ("go with option B
please"). Never abandon a chat because a picker appeared.

For the clarifying-question widget that replaces the input box: **never click
Skip** — the model reads it as "no preference" and answers for your persona.
`read` strips the widget, so read the options over CDP with the locator
`[id^="ask-user-option-question-"]`, writing output as UTF-8 (Windows codecs fail
otherwise). Reply by number AND paraphrase ("full research paper, option 2 — i want
the thorough one"); bare "2" is ambiguous. Re-scan to confirm it cleared.
`input_not_found ... selection_widget=visible` means Escape and reload failed: send
the persona-voice answer anyway, because posting a message re-mounts the input.

## Sending is verified, so trust the exit code

Long messages once truncated or silently failed to send. `_type_message_verified`
enters newlines as `Shift+Enter` and reads the composer back. **Exit 7 = nothing was
sent, re-run the same `ask`; 6 = not submitted; 0 = sent and a reply captured.**
Confirm from the reply that your full message landed — a truncated user turn is a
recurrence worth reporting. Send one coherent message, never a half-draft plus its
rewrite.

## Anchor every chat in the connector

Without an opening prompt that names the connector, the bot answers as a general
assistant and never touches our MCP. Open with something like "i added the workflow
builder connector — can you use it to help me make something new?" If the first
reply invokes no tool, nudge once; after two, log `BOT-WONT-USE-CONNECTOR` as a bug
and move on. Stay anchored afterwards, and redirect general chat back.

**Test domains must be complex-output work** — a paper, a screenplay, a
meta-analysis: multi-step, stateful, memory-heavy, evaluation-bound. Trackers a
chatbot already handles stress nothing this architecture was built for.

## Sound like a user

Good: "whats my agent doing", "is anything broken", "why
isnt it writing". Cheating: naming tools, actions, parameters, or internals ("work
targets", "bounded reflection", "ledger") the bot has not used first. Status and
activity are user-facing and fine. Never coach the bot
around a UX failure; log it.

Judge each reply: understood, right tool, useful, no hallucinated state, no leaked
internals. Wrong tool, hallucination or leaked internals is a BUG.

## Running, logging, stopping

A real user does not wait idle while a run cooks: poll every 30-60s and keep
iterating between polls (inspect a node, judge a partial output, try a variation).
"Stand by" from the lead overrides this.

Every per-turn tool-use-limit hit is an architectural signal: log `USER TOOL_LIMIT`
with the tools observed and the bot's reason, tell the lead, continue. Three
continues for one prompt is a serious surface problem.

`ask` appends both sides in full to `output/claude_chat_trace.md`; you append one to
three lines per action to `output/user_sim_session.md`, the shared log with the lead,
and read its tail for `LEAD DIRECTION` or `LEAD STOP` first. Every `ask` spends the
host's quota: one prompt, one new question.

Read-only is the default. Write intents (live until the cutover: `write_graph`,
`write_page`, `run_graph`; from it: `file_write`, `room_post`, `agent_register`,
`run_graph`) need authorization in
`output/mcp_test_plan.md` or a `LEAD DIRECTION`, still phrased as a user would.

Stop only for `LEAD STOP`, no route that keeps the live tab visible, an unavailable
connector, or the bot failing across multiple probes. Otherwise: log it, switch
lane, keep working.

## Never

- Never drive the MCP surface with a script and call it proof.
- Never inject JavaScript or use selectors to read what a user cannot see.
- Never treat hidden DOM state, direct MCP output, or an isolated profile as proof.
- Never claim an outcome you did not see in the rendered reply.

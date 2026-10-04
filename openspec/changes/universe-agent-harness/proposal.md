## Why

The founder, 2026-10-01: "really wish the app was more like this cli and that my
agent acted more like you do, getting him to do anything is like dragging a dead
horse around ... how long it keeps working should be like you. tools should be
like a mix of openclaw, claude code and pi.dev and hermes. authority should be
broad encouraging proactivity. tone like yours. feedback loop like yours. a
proper lean clean efficient self improving agent harness".

Production measurements of the founder's universe (`u-01kxm1vszd8hwp7em418asq8h9`,
`/data/.tinyassets.db` and its own folder, read-only, 2026-10-01) show the drag in
numbers, measured on its 251 chat turns with its owner (the background agent it
built is excluded; it is a user-built agent, not the harness). Every turn is a
brand-new session: Codex runs `--ephemeral`, and memory is a 20-message,
7,000-character text block with every tool result dropped. The median chat turn
spends 61,862 input tokens. 26% of replies contain an ask, 54% contain
blocked/can't language, and 29% carry "I haven't verified" disclaimers. Of 225
replies, 7 report an effect without negating it in the same reply, and 10.4% of
turns end indeterminate or abandoned. The design records the full audit.

The tools exist (S1 of `universe-harness-four-tools` shipped `read`/`write`/
`edit`/`bash` in a jail), so the problem is no longer missing tools. The harness
around them drops working state between turns and resends a persona prompt
that teaches warmth, curiosity and asking. Each round also carries about 7k
tokens of tool descriptions. Its tools have no network and no browser, and every
outward step needs per-turn consent.


**Revised the same day to the founder's direction:** "a new users universe
should act like an always on dot agent from chatgpt. but with the customization
of openclaw and the clean powerfull tool list of pi.dev and user design
shareabliaty for harness and on ui interface command center designs for agent
management". Then: "it should work how ever dots work for chatgpt … pi's 4
tools, users should have full customization of the harness layer for any
configeration of agents". ChatGPT dots, launched 2026-09-29, are always-on
agents with their own computer and a responsibility. They research proactively
with read-only tools, act under per-action Custom Rules with an auto-review, and
are managed from a profile with Activity, Take over and Pause (design §1.2).

## What Changes

A new user's universe **is** a dot. Each item below copies what dots do, except
where noted:

- **Onboarding.** The universe asks for a name and one responsibility: what it
  owns, where it learns, its quality bar, what needs approval, and how often it
  reports. It writes the answers into its own files.
- **Its own computer.** It has a workspace it fully owns, including its wiki
  and brain (the mechanism is left open), bash with public egress, and its own
  browser.
  - The browser is driven through a restricted broker: no raw CDP, no
    evaluation, no cookie export, and a profile kept out of every agent
    environment.
  - The profile page has a live view, plus *Take over* / *Return control*.
    Recording is suspended while the owner has control.
- **Always on, several projects at once.** Work becomes **activities**: child
  sessions that run in parallel on the user's seats and keep going after the
  chat closes.
  - They report into the main thread and resume after a deploy without
    repeating an external effect.
  - An activity releases its seat while waiting on the owner.
  - Scheduled activities are an explicit target kind on user-owned automations.
  - Schedules and activities are read completely, paged and never truncated.
- **Proactive research while idle.** Defaults: after 30 min idle, at most
  every 4 h, 08:00–22:00, single-flight, with events coalesced. It is shown and
  editable in the Scheduled view, and says so plainly when no compute is
  connected.
  - It is read-only by capability: a research flag in the platform-minted
    execution context, and a positive allowlist of side-effect-free reads.
  - In the jail, writes are refused, bash has no network, and there is no
    browser, extensions, hooks or flush.
  - Its output is proposals only, through a dedicated path.
- **Blocked work becomes a grant.** A block becomes a proposal naming the
  exact rule, connection or capability that would unblock it, or a patch
  request, or a scheduled recheck. It never stays a static blocked list.
- **Custom Rules (authority).** Each action class gets one of four behaviours:
  do without asking, do if pre-approved, ask first, or hand off.
  - Every execution carries a platform-minted **execution context** naming the
    initiating agent. It propagates through workflows, automations,
    activities, extensions, MCP calls and effectors.
  - Delegation never increases authority.
  - Semantic classes come only from trusted integration declarations.
  - Rules are owner-edited. The agent can read them and propose changes, but
    cannot write them.
  - Seed rules reproduce dots and the first version's "act inside, ask
    outside" default.
- **Auto-review.** On by default before consequential actions, with a per-class
  off switch.
  - It is a tool-free call on the **universe's own model**, admitted like any
    agent call (re-entering the activity's seat when it holds one).
  - It treats action content as untrusted, binds to the exact action, can only
    tighten, and fails closed.
- **Hand-backs on by default, as editable rules.** Credential or security
  changes, moving money, and granting others access hand back to the owner, as
  in dots.
  - The owner may change them, and the app says what that allows.
  - Only the cross-user floor is locked.
- **Profile and command center.** The profile has these parts:
  - an Activity tab: Waiting on you, In progress, Scheduled, Completed with
    receipts;
  - a Computer tab, plus Memory, Rules and Harness tabs;
  - Pause, and push notifications when work is done, waiting or proposed.

  A roster view across agents is the command center. Both are built on the
  custom-UI layer, so users can redesign them.
- **Reachable everywhere.** The app, `converse`, and chat apps through channel
  extensions all reach one session.
- **Exactly pi's four tools.** Every agent gets `read`/`write`/`edit`/`bash`
  and nothing else resident. Breadth (platform verbs, connected apps,
  extensions, attached MCP servers) sits behind `ta search` / `ta describe` in
  bash. The deferred-MCP route for the agent is dropped.
- **Memory item by item.** Every memory item has a stable id, and the owner can
  edit or delete each one (dots cannot).
- **Harness layer for any roster.** Any number of agents is supported. Each has
  its own instructions, identity, memory, skills, extensions, settings, rules,
  sessions and profile.
- **Sharing.** A harness bundle and command-center layouts are exported
  through the `universe-custom-agents` definition shape.
  - Export is PII-scrubbed and owner-confirmed, and never includes memory,
    sessions, credentials or browser state.
  - Imports land in an inert quarantine until the owner activates them, only as
    written or stricter.
- **Kept from the first version:**
  - sessions and compaction;
  - steering;
  - the truthful journal;
  - result-first tone;
  - self-improvement with file history and Undo;
  - the egress floor.
- **BREAKING.** In addition to the first version's deletions, the first
  version's "inside acts without asking; outside asks once" requirement is
  replaced by Custom Rules. The S1 `AGENTS.md` authority text becomes a
  description of the user's rules.

The change is designed vendor-neutral. Claude, Codex, OpenRouter and any
OpenAI-compatible or command-adapter source drive the same assembled harness,
and adapter-specific behaviour is a declared capability (`resume`,
`deferred_tools`, `self_compacts`), never platform policy.

## Capabilities

### New Capabilities

- `universe-agent-harness`: the dot experience. It covers:
  - onboarding, activities, proactive research and grantable blocks;
  - the execution context, Custom Rules, auto-review and the hand-back
    defaults;
  - the profile and roster, take-over, memory items, the harness roster and
    sharing;
  - sessions and compaction, steering, the four-tool surface, harness files
    with versioning, and truthful tool results.

### Modified Capabilities

- `deferred-turn-learning`: D7 alone retires cursor-driven extraction/nudges and
  proposed background stages, after IDs, history, Undo and memory non-regression
  proof, preserving unresolved sources verbatim with source IDs.
- `universe-personification-and-relay`: `converse` continues the thread's
  session instead of running one stateless turn. The separate learning
  extraction and the WebFetch-only denylist sandbox are removed. The OS tool
  jail and git-versioned files replace them.

## Impact

Ownership clarified 2026-10-04: `starter-agent-out-of-plumbing` solely owns editable
starter content, consumer/renderer cutover wiring and all-center/dormant-center
proof (task 2.3); `starter-seed-lifecycle` is D10's prerequisite transaction API
and single installation/upgrade/receipt mechanism, with its owner/center-bound
sidecar SQLite storage contract. Resident hooks and skills arrive alongside
untouched custom AGENTS.md with visible former-defaults diagnostics. D7 keeps both learning-removal
deltas and backlog preservation here. D6 keeps the platform-versioned read-only
handbook, with owner skills taking precedence over workflow recommendations.
No duplicated adoption or learning-retirement implementation is assigned to the
starter slice, and no owner review holds a center on legacy plumbing. Both the
starter move and D7 require N>=10 paired write/recall trials per model-family x
stock/customized-AGENTS fixture cell; the factual no-tools line stays in plumbing.

Code: `tinyassets/universe_intelligence.py` (persona and learning paths deleted),
`conversation_memory.py` (replaced by the session log),
`agent_turn_coordinator.py` and `storage/agent_turn_journal.py` (keyed by
session), `providers/codex_provider.py` and `providers/claude_provider.py` (resume
handle as a declared capability, journaled tool events), `universe_tools.py`
(egress, browser, writable root), `engine_mcp_server.py` (served as `ta` plus
deferred), `served_tools.py`, `shared_self.py` and `background_served_provider.py`
(agent nodes resume their session), and `effectors/*` consent (standing grants).

Storage: session logs (shipped in S1, outside the universe) and the file
history store are new storage shapes. How the agent's own workspace is made is
left open. Each gets its slice's own storage proposal before code.

Authority: per-action Custom Rules evaluated for the initiating agent through a
platform-minted execution context, with auto-review and hand-back defaults. This
replaces a fixed default. The cross-user floor, credential blindness and the
standing-grant checks are unchanged.

Storage: also new are activity records with pending effects, the rules store,
auto-review results, proposals, import quarantine with activation records, and
the browser profile. All of them sit outside every agent-controlled environment,
following the pattern S1 used for session records. This does not depend on
#4175. The activity store opens its own storage proposal in D2. The rules store
is specified here as part of the authority design.

Supersedes the S2–S8 slice plan in
`universe-harness-four-tools/design-notes/universe-harness-design.md`. Its S1
is built and that change is archived after its delta syncs.

Owner: Claude. Branch: `universe-agent-harness`. This PR is design only. Each
slice ships as its own PR and is proven live. S1 (#4173) has shipped. S3a
(#4174) is in review. The S3c proposal (#4175) is paused, and this design does
not depend on it. Design §5 maps all three onto the dot.

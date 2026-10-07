# Outreach & Content Engine

*The platform doing its own outreach — and a forkable template any project can copy.*

Built 2026-06-17 in the `patch-loop-live` universe.

---

## What this is

A general, **forkable** engine for an AI project to do its own outreach and
content creation: it wakes up, looks at its own real state, decides whether it
has something true to say, drafts it **in the project's own voice with each
channel's feel**, runs a safety gate, and emits a draft (or, once authorized,
publishes).

The platform (Workflow itself) is just the **first project** to run it. Anyone
with their own project can fork the same branch, swap in their own soul/voice,
and get the same behavior.

We started with **one channel working (X / Twitter)**, built so that adding
Mastodon, LinkedIn, and more later is a clean extension rather than a rewrite.

---

## Where it lives — one universe, one soul, one brain

Everything the platform does *for itself* lives in the **`patch-loop-live`**
universe — the platform's own universe. Different Goals and Branches, but one
soul and one brain, so all branches work from the same intelligence:

- the **patch-request loop** (soul-declared dispatch → `backlog-driver-v0` → canonical patch loop)
- the **outreach / content engine** (this — `outreach_content_engine_v1`)
- any future self-directed loop

The platform's identity + voice now lives as **canon** in `patch-loop-live`
(`canon/sources/platform-soul-and-voice.md`). That is the single source of
truth. When the voice should change, it changes **there**, and every branch
picks it up.

---

## The branch

**`outreach_content_engine_v1`** — `branch_def_id = 84a978709e75`
(published version `84a978709e75@9b379dc8`), bound to Goal
`d1424d86cb5f` ("Workflow speaks for itself"). 5 nodes, all runnable, all
source nodes approved.

```
gather_self_context → read_channel → voice_brain → safety_gate → emit
```

| Node | Kind | Role |
|---|---|---|
| `gather_self_context` | code (approved) | Reads the platform's real state: activity-log tails across universes, recent wiki pages, recent BUG-* ids |
| `read_twitter` (read_channel) | code (approved) | Reads the channel's world (X API v2 search today; mentions/timeline need user-OAuth) |
| `voice_brain` | prompt | **Decides intelligently whether to speak** and drafts in the project's voice + channel feel. Emits POST_ORIGINAL / REPLY / SKIP |
| `safety_gate` | prompt | Hard privacy/security floors + "would this project, in this voice, actually post this?" → SHIP / DRAFT_ONLY / SKIP |
| `write_twitter` (emit) | code (approved) | draft_mode → writes a draft to `/data/wiki/drafts/workflow-voice/`. Live → publishes (needs OAuth, see below) |

### Parameters (what makes it general & forkable)

Passed as run inputs / schedule template:

- `project_name` — the project speaking (`"Workflow"`)
- `voice_premise` — the project's soul/voice text (today: the platform soul canon)
- `channel` — `x` | `mastodon` | `linkedin` | …
- `channel_persona` — that channel's feel (cohesive mission, distinct voice)
- `draft_mode` — `true` (draft + review) / `false` (publish; needs authority + creds)

---

## Cadence — wakes up several times a day, decides for itself

Scheduled (`schedule_id d4ad124d-1bc3-4fcd-b05a-23b70598e6a0`) on cron
`0 */4 * * *` — every 4 hours, in **draft mode**. Most wake-ups should SKIP:
the soul rule is *"if I have nothing true to say, I say nothing."* It only
drafts when something concrete has actually changed in the platform's state.

---

## Self-iteration from feedback (the core discipline)

Feedback **corrects the process, not the output.** When a draft is off:

1. Do **not** hand-edit the draft.
2. Change the thing that produced it — the soul/canon (voice), `voice_brain`
   (judgment), `safety_gate` (taste floor), or `channel_persona` (feel).
3. Re-run. The next draft reflects the corrected process.

Approving a draft is the other path: an approved draft is cleared to publish
(once live posting is wired). This mirrors the patch loop — outputs are proof,
feedback drives the engine.

---

## Adding the next channel (Mastodon, LinkedIn, …)

The pipeline is channel-pluggable. To add a channel:

1. Set `channel` + write its `channel_persona` (its feel; same mission).
2. Add a `read_<channel>` adapter (its read API) and an `emit_<channel>`
   publisher — same shape as the X nodes.
3. The voice stays cohesive because every channel reads the **same soul**; only
   the persona (feel + length + audience) differs.

Each channel we wire teaches the next: same five-node spine, swap the two
channel-specific code nodes.

---

## Forking it for a different project

Anyone can fork `outreach_content_engine_v1`, then:

- swap `voice_premise` for **their** project's soul,
- write their own `channel_persona`(s),
- run it on a schedule.

The platform doing its own outreach is just instance #1.

---

## Going live (not yet enabled — safe by default)

Today everything is **draft-only**. To publish for real:

- grant the engine **effect_authority** (the universe currently runs effects dry-run), and
- wire **channel credentials** (for X: OAuth user-context — `TWITTER_API_KEY/SECRET`,
  `TWITTER_ACCESS_TOKEN/SECRET` — replacing the `post_path_not_wired` stub in `emit`).

Until then it drafts to `/data/wiki/drafts/workflow-voice/` for your review.

---

## Status / proof (2026-06-17)

- Branch built, validated, runnable; all source nodes approved.
- Ran live against **real** platform state — `gather_self_context` pulled
  dormant universes, recent bugs (BUG-014, BUG-034…), recent wiki pages;
  pipeline reached the generation step on every run.
- Draft-text generation is currently **gated only by the shared `codex`
  provider's 115s quota/cooldown** (the dev daemon is holding the quota). Not a
  branch defect — the scheduled runs will emit drafts as soon as the provider
  frees up.

### Next refinement
Have `gather_self_context` read the voice **directly** from the `patch-loop-live`
soul/canon at runtime (instead of passing `voice_premise` as a static input), so
there is one literal source of truth and editing the soul updates every branch.
(Small code-node change → needs host source approval.)

---

## ID reference

- Universe: `patch-loop-live`
- Platform soul canon: `canon/sources/platform-soul-and-voice.md`
- Branch: `outreach_content_engine_v1` — `84a978709e75`
- Goal: `d1424d86cb5f` ("Workflow speaks for itself")
- Schedule: `d4ad124d-1bc3-4fcd-b05a-23b70598e6a0` (`0 */4 * * *`, draft mode)
- Forked from the shape of: `tiny_voice_5node` (`b684b0512aaf`)

---

## Learning loop: discover → brain → reflect (LIVE + native, 2026-06-20)

The engine is no longer just "generate + post." It now has the outward
**sensor** and the **reflection** half of a self-improving loop, both running on
the shared daemon's own `codex` (subscription-billed; `get_status` confirms
`llm_endpoint_bound=codex`, `api_key_providers_enabled=false`, all provider
cooldowns 0, daemon idle/free). No subagent simulation needed — the daemon runs
its own LLM nodes.

### The sensor (the input goviralbro had and we lacked)

`outreach_discover_sensor_v2` (`7901b1d12e46`) — our analog of goviralbro's
`/viral discover`:

- `discover_fetch_b` pulls what is getting traction this week in our space from
  **Hacker News** (Algolia search; points filtered client-side) and **GitHub**
  (recent high-star repo search). Reddit is attempted but 403s from the cloud
  datacenter IP — surfaced honestly as warnings, not silently dropped.
- `discover_analyze_b` (LLM) distills it into an honest swipe brief: *what's
  landing*, *hooks worth borrowing*, *what I could ride (only if true for me)*.
- `discover_write_b` writes a clean `_outreach_signal.md` swipe file.
- Scheduled **daily 10:00 UTC** (`967f9e54-6b7d-450d-9928-a96cf7562ad4`).

A live run pulled 31 real items — e.g. `ponytail` (40k★), the Claude Code source
leak (2.1k HN pts), "AI agent bankrupted their operator" (1.5k), "betting
against agents / what actually works in production" (427).

### The reflection (turns signal + history into strategy)

`outreach_reflect_v2` (`2c8e32a9dc91`):

- `load_reflect_ctx_d` reads the outreach **ledger** + current **strategy page**
  + the **discover signal**.
- `reflect_strategy_d` (LLM) rewrites the strategy honestly — own results ONLY
  from the log (no invented engagement), external signal ONLY as *other people's*
  borrowable hooks, carrying forward only angles that are true for us.
- `write_strategy_d` publishes `pages/projects/platform-outreach-strategy.md`.
- Scheduled **daily 11:30 UTC** (`71eed760-3c37-430f-8958-79fa5a908c0d`).

The per-tick `outreach_content_engine_v3` gather reads that strategy page, so the
sensor's learning flows into every post decision. This is the full loop:
**sense the world → update the brain → let it shape the next post** — capability,
not a hand-written schedule.

### Substrate notes (audited 2026-06-20 — all retracted as user-buildable)

A host audit ("don't file as a patch what the connector can build") found all
three things I'd flagged were buildable through the connector, not substrate gaps:

- **LLM raw-output wrapper** — a composition, not a missing primitive: the LLM
  node emits raw markdown + a normalize node strips any leaked `field:` /
  `|`-block / fence / quote. Already shipped in `write_strategy_d` and
  `discover_write_b`. (PR-176 retracted.)
- **In-place node editing works** — `update_node` changes source_code /
  prompt_template / display_name in place (re-approve after a source edit);
  `patch_branch` works with `changes_json` ordered ops. The three reflect
  rebuilds were unnecessary; the live reflect is `2c8e32a9dc91`. (BUG-124 retracted.)
- **Schedule lifecycle works** — `list_schedules` / `unschedule_branch` /
  `pause_schedule` / `unpause_schedule`. Retired the superseded reflect_v1 and
  content_engine_v1 schedules directly. (PR-174 retracted.)

The one genuine substrate gap remaining from this work is **PR-173** — a
`twitter_post` effector. External writes need the effector framework's managed
credentials + authority/consent, which is registered substrate code, not a
branch node.

---

## Publish switch + go-live staging (2026-06-20)

The engine's emit node (`write_twitter` on `outreach_content_engine_v3` /
`d001a70acf2e`) now has the human-in-the-loop publish control, built in place via
`update_node`. A `publish_mode` state field (default **`approval`**) selects:

- **`draft`** (or `draft_mode=true`, or safety verdict `DRAFT_ONLY`) — writes an
  audit draft only. This is what the scheduled runs still do today.
- **`approval`** — writes the draft, queues the proposed post to
  `_pending_approval.jsonl`, and writes a `_notify.jsonl` record (your phone-push
  hook). Nothing posts; `external_write_packet` stays empty. (Verified:
  `status=pending_approval`, `posted=false`.)
- **`auto`** — writes a `_notify.jsonl` record with a `not_before` delay (default
  30 min, your veto window) and emits a well-formed `external_write_packet`
  (`sink=twitter_post`, payload, `idempotency_hint`, `expected_evidence_keys`).
  (Verified: `status=emitted_packet`, `posted=false`, packet populated.)

The packet is **inert** until all four of these exist, so nothing can post early:

### Go-live checklist (one short step each, once the effector lands)

1. **`twitter_post` effector** merges to `main` (PR-173 — substrate; not branch-buildable).
2. **X API credentials** on the droplet (`/etc/workflow/env`) — your touchpoint,
   like the codex auth.
3. **Soul effect-authority** grant for `(twitter_post, x:self)` +
   `grant_effector_consent` — authority lives in the universe soul, the token in env.
4. **Declare `effects`** on `write_twitter` (one `update_node`) so the framework
   reads the packet, then flip the schedule inputs to `draft_mode=false` +
   `publish_mode=approval` (recommended first) or `auto`.

Until step 1 + 2 are done, the loop keeps drafting/queuing safely.

# ADR-039: The Founder's Answers of 2026-11-21; ADR-001 to ADR-031 Are History

## Status

Accepted (founder, 2026-11-21: "as you recommend for all those questions",
`docs/decisions/untangle-2026-11/founder-answers.md`). Supersedes ADR-001 to ADR-031 under
ADR-032 (call 18). Amends ADR-032 decisions 2 and 5, ADR-034 decisions 1.2, 2, 4.1, 8, 11
and 12, and ADR-035 decision 5. Per decision 1, those ADRs' bodies are not edited: only
their Status lines point here.
Amended by ADR-040 (founder, 2026-11-28): decision 2.14, a sleeping device pauses its run;
decision 2.20, its written-approval line is replaced by research of each vendor's published
terms; decision 6, the promise is the tier's simultaneous sandboxes, and a run past them is
placed elsewhere or waits.

## Date

2026-11-21

## Context

- The platform's decisions are ADR-032 to ADR-038. ADR-001 to ADR-031 decided the retiring
  Python stack. ADR-032 and ADR-034 each kept some of them in a "What stands" list, by number
  only, so a reader had to open a superseded ADR to learn a live rule. The platform's own code
  cited ADR-006, ADR-018, ADR-025, ADR-026, ADR-028 and ADR-031 as its authority.
- The founder's 18 calls of 2026-11-21 changed decisions of ADR-032, ADR-034 and ADR-035. They
  were recorded only in the untangle log, which the cutover deletes (call 18). Call 18 asked
  that the answers which amend an ADR go into an ADR, so they outlive the log.

## Decision

1. **ADR-001 to ADR-031 are superseded by ADR-032** (call 18). They are history. Nothing in
   them is authority except what decision 2 keeps, in this ADR's words. The ADR rule stands
   (from ADR-005): an accepted ADR's body is never edited, only its Status line; a changed
   decision is a new ADR, and the ADR it changes says so in its Status line. `INDEX.md` lists
   the platform's set first and the superseded ADRs after it.

2. **What stands of them**, each with where the platform holds it:
   1. **One home per kind of fact** (ADR-005). The Direction is `AGENTS.md` § Direction,
      which the founder owns. Why a choice stands is the platform's ADRs. What is built is
      `openspec/changes/clean-sheet-platform/specs/`, and the queued work its `tasks.md`.
      The always-loaded files keep a byte budget (`scripts/check_context_budget.py`).
   2. **The platform makes no model call of its own** (ADR-006). Every model call is a run
      of the owner's agent on the owner's connected source. No platform, host or shared
      credential serves an owner, even as a fallback. The platform's own reads, searches
      and checks are deterministic (`owner/src/file-search.ts`; the gateway serves no model
      call of its own).
   3. **No vendor code** (ADR-007): Direction 2, and ADR-034 decision 5 (engines are data).
   4. **No host of ours** (ADR-008, ADR-012): Direction 8, and ADR-032 ("There is no host of
      ours"). A person's own computer serves only its owner's work (2.14). Foundational
      patches come forward (Direction 5).
   5. **Every execution has one owner** (ADR-009). Each run is admitted and recorded by its
      owner's object (ADR-034 decision 4), and its owner sees it and can cancel it. The
      platform runs no actor of its own. Infrastructure (the canary, the sweeps, the relay of
      2.17) never calls a model or acts as an owner. A registration that can never fire is
      refused when it is made.
   6. **Private by default** (ADR-010). Everything an owner creates is private. Exposure is
      a separate, explicit act of the owner. A visibility the platform does not enforce on
      every reader is not offered.
   7. **The owner's own reads are complete** (ADR-011). Only what enters a model's context
      is bounded, at the MCP door. Paging is a cursor the client drives, never a silent cut.
   8. **Ship the missing primitive, not a special case** (ADR-013): Direction 3 and the
      untangle skill. A test drives the product as a user would (the `ui-test` skill). What
      an owner builds has no structural cap (Fact 15).
   9. **Only the floor blocks a deploy** (ADR-015; its review line is ADR-038's). The floor
      is a cross-owner read or effect, credential exposure, unrecoverable loss of owner
      data, wrong money, an irreversible external act without consent, and the public
      connector down. Everything else ships and is judged after live use (Direction 6).
      Every outage is of equal severity.
   10. **Identity follows the account** (ADR-016): ADR-032 decision 2. WorkOS AuthKit is the
       upstream sign-in and its `sub` the owner id. Only the doors turn a verified
       credential into an owner, so there is no unauthenticated principal. A capability on
       one main agent ships on the others, and a bug on one is never answered "use another".
   11. **Knowledge is files** (ADR-017). The owner's files are canonical and any index over
       them is derived (ADR-034 decision 2, ADR-032 decision 5). OKF is the starter's
       default form (`platform/starter/`), not a mandate.
   12. **The owner can leave with everything** (ADR-018). One export streams everything the
       owner holds, never a credential (`owner/src/export.ts`, the owner-object spec's
       export requirement).
   13. **One sandbox per owner, and everything in it untrusted** (ADR-019, ADR-023, ADR-030),
       the engine's loop included. The trusted side runs outside it (ADR-034's change to
       ADR-032's "What stands"). Code that must run as the owner runs in it, and trusted
       code outside calls in. Owner policy is written only by the owner and enforced outside
       the sandbox (ADR-032 decision 1).
   14. **A paired device runs only its owner's work** (ADR-024). It runs only work admitted
       for its owner, never another's and never as a fallback for a platform path. It
       connects outbound only. Vendor sign-ins stay on it. Each local tool is off until the
       owner grants it. Every hosted surface works with no device online, and a device's
       in-flight work then becomes `unknown`, never replayed.
   15. **The trusted ledger lives outside the sandbox** (ADR-025's placement). The owner's
       object holds the ledger, bodies, board and rooms (ADR-032 decision 1). A run posts
       only as its own agent, under its run's grant (ADR-034 decision 6). Status comes only
       from receipts. Wakes are ID-only doorbells, and unknown work is never replayed
       (ADR-032's "What stands"). Reading or posting never wakes a sandbox.
   16. **Zero idle tail** (ADR-026): the sandbox pauses when a run ends (ADR-034's "What
       stands").
   17. **A shared real-time relay is infrastructure** (ADR-027). One relay object a match
       forwards opaque frames between ticket holders. It never simulates, decides or calls a
       model. Players' devices do the work, and the publisher's own state stays the record.
   18. **Scoped keys on MCP** (ADR-028), built on `/mcp` and `/api` (#5735). The owner mints
       one key per outside agent, bound to its registry row. A key calls `/mcp` and `/api`
       as that agent, held to its grant, and never becomes an app session, approves, or
       writes owner policy. It is hashed at rest, revocable alone, and shown once. Muse
       connects both ways: by a key and by OAuth (call 6). No per-agent rate limit is built
       (Fact 15).
   19. **One app manifest** (ADR-029). `apps/{slug}/app.json`, with its `multiplayer` block,
       is the one manifest. The owner customizes the owner app through it after the cutover
       (S6, Step 20). App serving comes before the cutover, so published apps never go dark
       at `routes` (call 7).
   20. **Each vendor's sanctioned paths** (ADR-031 as amended by ADR-033, which holds the
       rest).
       - TinyAssets never holds a consumer login token. It never leaves the sandbox or the
         device where the vendor's own sign-in wrote it: each engine record's
         `secret_paths` never sync. A vendor's documented per-task callback token (Claude's
         `/fire`) is the one exception, held sealed.
       - Wakes reach an agent through its vendor's own door, each a record (data).
       - The device runner launches only the user's own signed-in, unmodified CLI.
       - A subscription sign-in feature waits for that vendor's written approval.
       - A hosted CLI is unmodified. Vendor names are never a product's or a feature's name.
         The founder accepts Anthropic's Commercial Terms before Claude Code is hosted.
   21. **A required check reports on every pull request and merge group, unfiltered**
       (ADR-003, ADR-020): `platform/ci/guards/required-checks.test.ts` over
       `docs/ops/merge-queue-ruleset.json`. A skip is not a pass (`AGENTS.md`).
   22. **Merges enter the queue on a user's token** (ADR-004). An auto-merge the default
       `GITHUB_TOKEN` arms never enqueues, so enrollment uses `MERGE_ATTRIBUTION_TOKEN`
       (`.github/workflows/auto-enroll-merge.yml`).

   Everything else in them lapses with the old stack: `PLAN.md` and `STATUS.md`, sealed
   boxes, cells and Postgres, the agent node (an agent is files, ADR-034 decision 2), gVisor
   and the droplet, the loop-worker pool, size-class admission, and ADR-031's human-started
   rule (ADR-033).

3. **One agent definition reaches every engine** (calls 1, 2, 3 and 9). This amends ADR-034
   decisions 1.2, 2 and 11.
   - The user's own main agent loads the same definition. The MCP door serves the owner's
     `AGENTS.md` and skills as MCP prompts and resources, which the client loads, not as
     tool text that steers (call 1).
   - Hooks are one definition in the agent's files. Each engine's record renders it into
     that engine's format, as data. R5's root-owned hooks always win (call 2). Hooks are no
     longer "the engine's own"; subagents still are.
   - The agent's own MCP servers run, merged with TinyAssets' door and held to the hosts the
     egress allowlist permits (call 3).
   - A package is the starter format generalized: a folder of agent files plus a manifest.
     Any owner can publish one, and installing it copies it (call 9).

4. **Code mode is off by default until its isolation probe (H2) passes** (call 10). This
   amends ADR-034 decision 12. A later change turns it on, only on H2's pass.

5. **Limits are Fact 15's kinds only** (call 11). This amends ADR-032 decision 5's kept
   versions and supersedes ADR-035 decision 5's collection-history retention.
   - Every retention is an owner setting, where off means keep forever, unless a ceiling is
     recorded with its derivation.
   - `client_share` and `owner_reserve` are off by default.
   - The Clean-Sheet doc's per-agent run, sandbox-second and wake caps are dropped. Only
     vendors' limits apply there.
   - The shares of the 10 GB guard are technical ceilings, each with its reason.
   - Caps outside Fact 15's kinds become one of them: `openPerRun`, code mode's limits taken
     from pi's defaults, and the 300 s idle cut, made data per engine.

6. **The tier's simultaneous runs are a promise** (call 12). This amends ADR-034 decisions
   4.1 and 8. A run past the ceiling waits, with a clear message, and is never refused for
   it. Raising E2B's tier is spending, which the founder decides when the canary's E2B
   metrics near the ceiling.

7. **An unreadable control record is served from its last good copy** (call 13). This
   amends ADR-032 decision 2's fail-closed `control` record. Only the writes that need a
   fresh copy are refused.

## What changes in the other ADRs, exactly

- ADR-001 to ADR-031 each gain the Status line "Superseded by ADR-032 (founder,
  2026-11-21)", and, where something stands, "except:" with the item of decision 2 that
  holds it.
- ADR-032's and ADR-034's Status lines name this ADR instead of the founder's answers, and
  say that their "What stands" lists are held by decision 2. ADR-033's Status line says the
  same for ADR-031's decisions it left standing.
- ADR-035's Status line names decision 5 here. ADR-036's says that decision 5 here answers
  its decision 1's open retention of collection history.

## Consequences

- The platform's code and specs cite this ADR, not a superseded one, for a rule decision 2
  keeps.
- When the cutover deletes the untangle logs, the answers that amend an ADR stand here. The
  answers that only schedule work stand in `tasks.md` and, once built, in the code.

## Sources

- `docs/decisions/untangle-2026-11/founder-answers.md` (the 18 calls, 2026-11-21)
- ADR-001 to ADR-038

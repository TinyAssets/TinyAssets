# fact15-defaults as built (2026-11-21)

The founder's answers of 2026-11-21, call 11 (`founder-answers.md`, #5730): each retention
is an owner setting, off meaning keep for ever, unless a ceiling is recorded with its
derivation; `limits-audit.md`'s "Chosen retentions" list goes; `client_share` and
`owner_reserve` are off by default. Fact 15: the only limits are the tier's runs and
storage, the owner's own settings (off by default), vendors' limits and recorded
technical ceilings.

## Research: every retention in `platform/` (read at `f3fe84ec5`)

A retention is what the platform lets go of on its own, by its age or by a count. The search
read every `DELETE` by age, count or expiry in each Worker's source and the runner, every
`*_KEPT`, `*TTL*`, `retention` and `Kept` constant, the source records' pinned settings,
the deploy kit's copies and the Workers' configs.

- **Owner settings, off already:** the files' `retention_days`, `keep_versions` and
  `keep_recent` (`FILE_DEFAULTS`, untangle F1). All off, every version is kept until the
  owner deletes it. This is ADR-032 decision 5's kept versions, which call 11 supersedes.
- **Ceilings of the object's 10 GB:** each keeps rows in the owner's SQLite, which the
  owner-bound guard holds under half, so keeping them for ever would pass it. Each already
  had a row naming its guard term, but for the revoked connections (added here):
  `maxHistoryCharge` (the collection history; ADR-035 decision 5's quota retention is not in
  the code, since F1: nothing is let go at the storage figure), `RUNS_KEPT`, `NOTICES_KEPT`,
  `USAGE_LIMITS.receiptsKept`, `EGRESS_LIMITS.receiptsKept` and `refusalsKept`,
  `receiptsPerBucket` (policy and action receipts), `appliedProposals`, `maxSettledWakes`,
  `CONVERSATIONS_KEPT`, `maxFreeClients`, `maxSessions`, `maxRevokedKept`, `EXPORTS_OPEN`,
  `maxAgentRows` (a live agent's new row forgets the oldest retired one; its row said only
  "refused" until the review of #5738),
  `SPANS_KEPT`, `BILLED_KEPT`, `LIVE_KEPT`. Where the rows are periods, an age is the count:
  the spend counters' `daysKept` 40 and `monthsKept` 3 (`SPEND_KEPT` is a hundred sources
  times those 43 periods, and refuses past it, so keeping them for ever would refuse
  spending) and the sandbox's `DAYS_KEPT`. Why each term takes the share it does is
  `untangle/fact15-caps`'s.
- **A chosen age under a count ceiling: `LEDGER_LIMITS.settledWakeRetentionS`, a week.**
  The settled wakes are already bounded by `maxSettledWakes`, the guard's `ledger_wakes`
  term, so the week bounded nothing the ceiling does not. Nothing reads a settled wake's row
  but the owner debugging a doorbell.
- **Recovery copies:** `RECOVERY_WINDOW_S` (30 days) and the off-site copy's `HIDE_NIGHTS`
  and `KEEP_DAYS` (`deploy/offsite.ts`), `GC_GRACE_S`, `RESTORABLE_LIFE_S`,
  `MANIFEST_PROTECT_S`, `EXPORT_LIFE_S`. Each is derived from Durable Objects' 30-day
  point-in-time recovery. Kept for ever, a copy would outlive the erasure the owner is
  promised. The first two had no row; they get one (vendor limit).
- **Not retentions:** credentials' and flows' lives (sessions, tickets, tokens, OAuth flows,
  `pendingTtlS`, upload and placement intents), bookkeeping shipped and then dropped (the
  change log, the sandbox Worker's meter log, closed sandbox spans no run needs), live
  memory (`SIGNED_OUT_KEPT`, the relay's tails), the dedupe window `BILLED_WINDOW_S`, the
  scratch cache's let-go under disk pressure (`LOW_WATER_SHARE`, which loses no record) and
  the sync's slow-match report (`SLOW_MATCH_MS`, which lets nothing go). The operator log's
  365-day lock (`deploy/platform.json`) holds the platform's own record, not the owner's,
  and lets nothing go. Workers Logs are Cloudflare's own retention of the platform's logs.
- **Not yet in a kind, each owed below:** `TEMP_TTL_S`, the control record's
  `scratch_retention_days`, Claude Code's pinned `cleanupPeriodDays`, and the lives of the
  requests held for the owner (`CARD_TTL_S`, `cardTtlS`, `approvalTtlS`, `proposalTtlS`).

## Decided

1. **`client_share` 100 and `owner_reserve` 0 by default** (`contract/src/file-defaults.ts`).
   Off is each one's range end, where it binds nothing the owner's whole does not, as
   `max_file_bytes` is off at its most. This is not a null for off, because a null would be
   a second spelling of what 100 and 0 already mean, and each check would then need a branch
   for it. The one reader is unchanged: `owner/src/policy.ts` `fileSettings` merges the
   owner's `files` table over the defaults, so the files plane, the placed bodies and the
   collections all read the same values.
   - **No migration.** A stored document holds only what its owner wrote, so a value the
     owner set stays, and an unset one takes the new default. Nothing writes the defaults
     into a document: neither the importer (an imported owner has no `files` table) nor the
     app.
2. **The settled wakes' week goes.** A settled wake row is kept until the newest
   `maxSettledWakes` push it out, whatever its age. That is the ceiling's off. No setting is
   added for it: call 11's rule takes the ceiling branch here, and a setting that shortens a
   telemetry window nobody asked for would be a feature with no reader.
3. **`limits-audit.md`:**
   - "Chosen retentions" and "Flagged for the founder" go. A short "Retentions" section gives
     the rule and lists the items not yet in a kind, each with its branch.
   - The prose each removed entry carried moves into its row: the `Runs.resume` reader, the
     policy buckets and the unexported action decisions, the egress receipts' charge.
   - New rows: the revoked connections, the spend counters' days and months (in
     `SPEND_KEPT`'s row), and the recovery copies.
   - `SLOW_MATCH_MS` and `LOW_WATER_SHARE` move to "Not listed", since neither refuses
     nor truncates anything.
   - The summary is recounted by its own rule (it was 198 from 2026-11-04 against 207 rows;
     now 210, recounted in review after the merge of main).
4. **The spec** (`owner-object` "An outside client holds a share") says the shares are off
   by default, and adds the scenario for that.

## Owed

- [ ] `TEMP_TTL_S` and the whole temporary area (`/.ta/tmp`, `file_temps`, `tempDir`,
  `dropTemps`, its triggers, export and restore entries and guard terms) have no product
  caller, so it is deleted with a migration, its retention with it (`untangle/drop-temp-area`).
- [ ] The control record's `scratch_retention_days` leaves the record, since the record holds
  only the tier's runs and storage: scratch is a cache that only disk pressure lets go, and
  the sandbox Worker's `known_retention` goes with it. This follows `untangle/control-last-good`,
  which owns `contract/src/control.ts` and `sandbox/src/control.ts`
  (`untangle/fact15-defaults-2`).
- [ ] Claude Code's pinned `cleanupPeriodDays: 30` (`sources/records/claude-code.json`
  `cli.decide.pins`) is the engine's own transcript retention at its default. Research comes
  first: what value keeps every session, and what the held-call card's 30 days (`cardTtlS`)
  rests on then; the same research reads whether any other engine record's CLI (Codex,
  Gemini, Kimi, pi) lets its sessions go at its own unpinned default (`untangle/fact15-defaults-2`).
- [ ] The lives of requests held for the owner (`CARD_TTL_S` 7 days, `ACTION_LIMITS.cardTtlS`
  30 days and `approvalTtlS` a day, `POLICY_LIMITS.proposalTtlS` 90 days) each go into one of
  Fact 15's kinds (`untangle/fact15-caps`).
- [ ] Each ceiling's share of the 10 GB room gets its reason (`untangle/fact15-caps`), and a
  guard holds every cap and retention in source to `limits-audit.md` (`untangle/limits-guard`).
- [ ] `tasks.md` Step 5's call-11 line (#5730) is ticked by whichever of
  `untangle/fact15-defaults-2`, `untangle/fact15-caps` and `untangle/limits-guard` lands last,
  since it names all three.

# store-probe: the store probe is answered, and its subject is deleted (2026-11-23)

`untangle/store-probe`, owed by `model-catalog.md` (#5834, "Left"): `.github/workflows/store-probe.yml`
and `scripts/probes/storeprobe/` import `@earendil-works/pi-durable` and `@earendil-works/chord`,
which the platform lockfile has not installed since slice D, so the probe cannot bundle.

## Research (Fact 14)

- **What it measured.** Live probes P1, P2, P3, P4 and P7 of storage design (a′): one pi Session
  per agent thread, its bytes in a per-thread `ThreadStore` Durable Object in the owner Worker's
  own script, reached over the same-script `PISTORE` binding. Four throwaway Workers stood in for
  the owner and its door; `owner.js` ran pi-durable 1.4.0's `SqliteStorage` and real pi `Harness`
  Sessions on pi-ai's faux provider (`openspec/changes/clean-sheet-platform/store-probe.md`,
  Method).
- **It ran, and its results are recorded.** Run 1 (41029757222, 2026-10-30, commit `2c4a7d404`,
  Workers Paid) answered all five; cleanup deleted the four `ta-storeprobe*` scripts and left no
  namespace. The readings and what they set (`limits.subrequests`, `OPEN_MAX` 64, the retention
  times, one `RpcTarget` session per open Session, no pre-warm) are `store-probe.md`'s "Found"
  and "Results". S1-S3 (#5373) built the thread stores from them.
- **Its subject is gone.** ADR-034 (founder, 2026-11-01): pi stops being the platform's engine;
  decision 13 deletes `ThreadStore`, `RemoteStorage`, `ThreadHost`, the owner's `ThreadStore`
  namespace and the dependencies pi-durable, chord and pi-telemetry (`engines-replan.md` slice D).
  The owner Worker binds no Durable Object namespace (`platform/owner/wrangler.jsonc`). Direction
  2: an agent is files run by an unmodified engine, never pi in the owner's object.
- **What of it still stands.** Only P1's reading that the platform counts an object's calls per
  invocation, reset as work moves across events: `platform/owner/wrangler.jsonc` cites it for
  `limits.subrequests`. The live check at 10,000,000 that comment owes is measured on the stage
  (`tasks.md`, "Measured on the stage as it runs"), not by this probe, whose owner stand-in is
  the deleted design.
- **Its two "still owed" probes.** P8 (a populated store's `deleteAll()` frees its billed bytes)
  and P5 (a store reset mid-run) are about `ThreadStore`, so they lapsed with it.
- **The rule for an answered probe.** Direction 6 (delete stale docs, tests and notes), and the
  precedent of #5426 (the cap and facet probes): the code and the manual workflow are deleted,
  and the probe's record stays as the results, citing the code at a commit by URL
  (`check_shapes.py`'s deleted-path check refuses a record that cites a deleted path by name).
- **Who else names it.** `git grep` over the tree: no workflow, script, guard, `cutover.json`
  entry, ruleset or `tasks.md` line runs or lists it. Mentions: `store-probe.md` itself,
  `facet-probe.md` (method lineage), `owner/wrangler.jsonc` (P1), `ci/guards/isolation.test.ts`
  (a synthetic workflow that `require`s `scripts/probes/storeprobe/door.js`), and dated decision
  logs (`ci-secrets.md`, `pass-owner.md`, `b24-manifest.md`, `model-catalog.md`), which stay as
  records (ADR-005).

## Decided

1. **Delete the probe and its workflow**, not repair it: it measures a design ADR-034 deleted,
   so on current packages there is no `ThreadStore` or pi Session to probe, and no live question
   left for it. Its results live in `store-probe.md` ("Found", "Results"); the code is at
   `14ec3e8f6`, the last commit that held it (run 1 ran `2c4a7d404`).
2. **`store-probe.md` stays as the record**, as `cap-probe.md` and `facet-probe.md` do: its
   header says the probe is answered and deleted and links the code at `14ec3e8f6`; P8 and P5
   read as lapsed with the thread stores; the lines that ran the deleted scripts name them at
   that commit.
3. **The isolation guard's synthetic workflow** requires `scripts/incident_issue.js`, the
   script `deploy-prod.yml` requires the same way, instead of the deleted probe's `door.js`.
4. **`model-catalog.md`'s owed line is ticked.**

## Problems, under the fix that removes each

- Delete the probe, its workflow and the notes that run them.
  - `scripts/probes/storeprobe/` (dead code): it imports packages the lockfile does not hold,
    so it cannot bundle, and its subject (`ThreadStore`, pi Sessions) no longer exists.
  - `.github/workflows/store-probe.yml` (dead code): a manual workflow that can only fail at its
    harness step; its comment names pins (pi-durable, chord 1.4.0) the platform no longer has.
  - `store-probe.md` "Still owed, live: P8 ... P5" (a stale note): owed probes of a deleted class.
  - `store-probe.md` cites the deleted paths as runnable (`node .../local.mjs`, `delete.sh` by hand).
- Tests.
  - `ci/guards/isolation.test.ts`'s "allows a step the repository's scripts" named a script this
    change deletes (a hidden link from a fixture to a probe); it names a script a workflow
    really requires.

## Found and left

- `store-probe.md`'s Method, Reading key and Running it describe the deleted harness. They stay,
  as in `cap-probe.md` and `facet-probe.md`: they are what the recorded readings mean, and
  `facet-probe.md` cites them for its own method.

## Owed

None.

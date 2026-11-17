# O-owed as built (2026-11-17)

Three lines of lane O's "Owed to lane O by merged PRs" (`tasks.md` §10): S8 (`s8.md` item 6), S9 (`s9.md` item 7) and C5b (`c5b.md` item 5). Together about 500 changed lines, one PR.

## The plan (research, before building)

**S8: what the owner reads.** The owner reads a source record's CLI in ten places, each through `bundledSources().get(id)?.cli`: `runs.ts` `choose` (three), `answering` and `events`; `signins.ts` `deadline`, `frames` (with `cli!.login!`), `switchedOn` and `signinStart`; `reports.ts` `signinRequest`. Four of them test whether the record signs in, each its own way: two by `cli.credential.includes("vendor_login_in_sandbox")`, one by `cli.login !== undefined`, one by both. `cliSpec` makes them one fact (`s8.md` item 2).
- `served()` is not the owner's read: it checks every record whole, about 2.5 ms on this host against 0.1 ms for one record (measured, 500 calls each), and the owner reads one engine at each run admission, events batch and sign-in frame. The module-state rule refuses a map kept at load (`s8.md` item 1).
- **Chosen:** `sources/src/served.ts` gains `servedRecord(id)`, one record of `served()`'s view, read and checked alone, and `served()` and `engineHosts` are built on it, so a record "is served" is decided in one place. `signsIn` takes `Served | undefined`, so a reader narrows an id's record in one step. Each owner read takes them, and the `!` assertions go.
- `runEnv`, `signinsOn`, `signinsRead` and `signinShape` stay as they are: they take a catalog because the doors call them with the one they serve (`doors/src/index.ts`), and each tests its record itself.

**S9: what is left.** O1d built it (`o1d.md` item 5): `RunHost.stop` and the lists that feed it carry run ids. `LiveRun` stayed for `liveRuns`, whose two readers each want less than it gives: the launcher's `leaseFreed` asks whether a live run holds a lease, and the erasure's `ended` maps it to ids. No reader reads its `epoch`.
- **Chosen:** `Runs.holds(leaseId)`, the runs' twin of `Signins.holds`, and `liveRuns()` answering ids, which `endAll` reads too (it had its own copy of the query). `lost` names its own row type. `LiveRun` is deleted.

**C5b: the owner's config guard.** Every Worker's top level is held by the kit's `configProblems` over a `CONFIG` in its `rules.ts`, but the owner's: its guard had its own checker for the same rules, with `controlOnly` holding `CONTROL`'s id to the doors' placeholder, an agreement that checks nothing live (the deploy kit fills every namespace's id by binding name, `deploy/deploy.ts` `deployedConfig`). It also restated the erasure queue's name (`OWNER_QUEUES`), a value whose one home is the config (`c5b.md` item 1).
- **Chosen:** `owner/guards/rules.ts` `CONFIG`, a `ConfigRules` like every other Worker's: its keys, flag, secret, class, `kv: ["CONTROL"]`, `queues` by binding (`ERASURES`, no consumer), and `limits` exactly. `config.test.ts` runs `configTests` over it, with the owner's own checker as its `problems`: the kit's for the top level, then each environment and every service binding, which only the owner's guard holds. `controlOnly`, `OWNER_QUEUES`, `TOP_KEYS`, `SECRETS` and `CLASSES` go.

## As built

1. **`servedRecord`** (`sources/src/served.ts`): `bundledSources().get(id)` when it has a CLI. `served()` lists each id through it; `engineHosts` reads it. `signsIn(record: Served | undefined)`.
2. **The owner's reads:**
   - `runs.ts` `choose`: `allows` and the named engine read `servedRecord`; the auto branch's plans are `signsIn` records. A key's source (`keyFor`, `engines`) is not a CLI record and stays a catalog read. `answering` and `events` read the engine's events from `servedRecord`.
   - `signins.ts`: `deadline`, `switchedOn` and `signinStart` read `signsIn(servedRecord(…))`. `frames` throws (Fact 8) when a sign-in row's engine does not sign in, which `signinStart` refused before the row was made; it was a `!`.
   - `reports.ts` `signinRequest`: `signsIn(servedRecord(engine))`.
3. **`LiveRun` deleted** (`runs.ts`): `liveRuns(): string[]`, ordered by `seq`; `holds(leaseId)`; `endAll` reads `liveRuns`. `launch.ts` `leaseFreed` reads `holds`; `owner-do.ts`'s erasure hands `liveRuns()` to `stop`.
4. **`CONFIG`** (`owner/guards/rules.ts`), declared `satisfies ConfigRules`, so its `classes` and `flags` are known to the environments' check. `ENV_KEYS` is a list. `ruleLists("CONFIG")` now finds the owner's too; it has no bucket, so the R2-type guard reads it as before.
5. **`config.test.ts`** on `configTests`: the kit's five tests (holds, known keys, every other key refused with the named ones, the probes, the only config) replace the owner's own "is the only wrangler config", "declares OwnerDO a SQLite-backed class" and the top half of "allows only keys wrangler knows". The namespace, migration, store and `base` probes are the kit's probes over the owner's config, with a second producer, a consumer and a second secret added. The environment half now refuses every key wrangler offers an environment beyond `ENV_KEYS` and `services`, not a list of 19.

## What a caller sees

Nothing changes. The recorders (`npm run record`, base `6409bb7cb`): see the PR body. A sign-in row whose engine does not sign in throws an `Error` where it threw a `TypeError`; no door makes one.

## Left

- `launch.ts` `runEnv(bundledSources(), …)` and `control-gate.ts` `signinsRead(bundledSources(), …)`: sources' functions over any catalog (above), each testing its records itself. Not the owner's reads.
- The doors' CLI-record check (`doors/src/app.ts`, `s8.md` item 6) is lane D's.

# pass-ci-deploy as built (2026-11-21)

d2 §E steps 4 and 5 (`untangle-2026-11-wave-2.md` §E) for two areas, `platform/ci/` (the
repository's gates: the guard kit, the Worker kit and the ci guards) and `platform/deploy/` (the
deploy kit and its guards), both passes in one PR (`tasks.md`, the passes line). The two share
this PR to save CI runs: each touches only comments and tests, and one run of the `ci` and
`deploy` legs covers both. On the model of `pass-sandbox.md`, `pass-files.md`, `pass-doors.md`
and `pass-gateway.md`. The listings they start from are `list2_cross.md` §2.1 to §2.5 and
§2.10 (the deploy kit and the ci guards), taken at `fdac25ba4`, so every item was re-read at
`origin/main` `70a45fc49`. `list2_services.md` lists nothing of these two areas beyond the
budgets record (§2.7 `V2-ST5`). Lanes A, B, C, R, T and X had moved most of it since: the
isolation guard is the layer table over every file (B1), the environment guard no longer reads
`deploy/facts.ts`, `KNOWN_PRODUCTION_WORKFLOWS` names the platform's own, the workflow's job
`if`s are evaluated (`runsIf`), not matched as text.

Behaviour is unchanged. Every non-test source of the two areas (`ci/**` and `deploy/**` but
their `guards/` and `test/`), each minified with esbuild, has the same digest on base and head,
so no line of code changed, only comments. Two data files differ in prose alone: the
deploy guards' budget record names its slowest test by its title now, and one of
`platform.json`'s `why` texts loses a tag; no code reads a `why`. The deploy kit runs live
deploys, so it changes in comments and tests only, and `platform/cutover.json` is untouched
(its guard passes).

## 1. Comments

**Kept:** vendor facts (Cloudflare's API, its route matching, its Queues and KV limits,
wrangler's behaviour, E2B's webhook API and its read date, GitHub's Actions variables and
workflow commands, workerd's cancelling of a request held on another's promise, npm's hidden
lockfile), spec and decision pointers (ADR-032, ADR-037, AGENTS.md's Facts and Direction, d3's
sections, the decision files that hold a design: `deps.md`, `budgets-variance.md`,
`r8-erased-orphans.md`, `r8-locate.md`), the incident that shaped the production-environment
rule (run 38484527257) and the concern behind the pull-request rule (`2026-07-27`), the
measured figures with their source, and the reason comments: why an order, a wait, a refusal
or a check is what it is.

**Removed or rewritten:**
- **Provenance tags,** about 40 across the two areas: `untangle B1`, `B2`, `B7`, `B10`, `B11`,
  `B14`, `B18`, `B23`, `B24`, `B25`, `A1`, `untangle wave 2`, `slice L`, `slice P`,
  `tasks 2`, `tasks 5`, `R8`, `audit D4`, `audit D6`, `round 8, R8-2`, the review ids
  (`review M3`, `M5-2`, `D3`, "Slice L's review (H2, research 3b and 3c)") and the
  reviewer's-miss notes over the module-state, timeouts and locks cases. Two deploy test
  titles carried one (`(review M5-2)`, `(review D3)`) and are named for what they test. The
  sentence each tag hung on stays where it says why. Git holds where a line came from.
- **Pointers to working files never in the repository:** `b23_research.md` (`ci/gate.ts`,
  `ci/gate-worker.ts`) and `operator_research.md` (`deploy/offsite.ts`, `deploy/operator.ts`).
  The vendor fact each carried stays (workerd cancels a held call as hung); d3 §G is the
  operator path's design home.
- **The old stack:** the environment and legs guards' "moved from the Python suite's
  `tests/...`" lines and the old host's box-preflight note. Those Python files leave with the
  old stack; these guards are the lasting ones.
- **Stale:**
  - `deploy/facts.ts` "the environment guard ... read them here". It no longer does; the
    import job does (`--import`).
  - `deploy/deploy.ts`'s usage block listed four of the nine own options and not `--plan`. It
    now names `--plan`, the deploy options and the mode's own (`OWN`, which says each).
  - `deploy/deploy.ts` "The R2 buckets the configs bind" and "The queues the configs send
    to": the kit also makes the unbound buckets and the dead-letter queues. Both docs are
    gone; `BUCKETS` and `QUEUES` in `facts.ts` say what each holds.
  - `deploy/guards/deploy.test.ts`'s world faked "python and the migrations guard". It fakes
    the canary, the pair's checks, the migration locks' command line, H2's probes and rclone;
    no kit source runs Python (`canary.test.ts` holds it).
  - `ci/classes.ts` "from a leg's Vitest JSON report". `budgets.mjs` reads one run's whole set.
  - `operator.ts` "`backup.ts`", which two folders hold. It is the owner's.
- **Restating the code:** `ci/paths.ts`'s two docs (`REPO`, `PLATFORM`), `ci/workflows.ts`
  `workflowNames`, and the deploy kit's `e2b` step doc (a copy of `Values.e2b`'s).
- **Run-on lines,** past 100 characters or broken mid-sentence, reflowed: `ci/timeouts.ts`'s
  header, `ci/deps.ts`, `ci/kit/kernels.ts`'s SHA-256 item, `ci/guards/legs.test.ts`'s header,
  `deploy/deploy.ts`'s secret rule, `deploy/cloudflare.ts`'s header, `deploy/offsite.ts`'s
  recovery item and `deploy/guards/canary.test.ts`'s header, and the paragraphs a removed tag
  left short.

**Left as they are, each with its reason:**
- `deploy/deploy.ts`'s one-line step and field docs (`list2` §2.10 counted about 70 at
  1,832 lines; the file is 2,956 now). Each re-read says what a step makes, reads or refuses
  in the row's terms (the values it hands on), which the step's body does not name, and the
  `MODES` rows are read through them. Its `// --- ... ---` banners part a 3,000-line file.
- `deploy/deploy.ts`'s header, the modes one by one. It is the founder's one telling of what a
  run of each mode does, and `platform-deploy.yml` points to it.
- The probe mode's comments (`deploy.ts` `PROBE_*`, `facts.ts` `PROBE_ROUTES`): the
  `untangle/b24-manifest` branch deletes the mode, so they go with it.
- `deploy/checklist.md`. It is the founder's runbook for the live steps, not a comment of
  the code, and `untangle/b24-manifest` edits it now.
- `ci/budgets.json`'s owner title with `(review N3)`. It is the owner test's own title, which
  the owner area's pass renames.

## 2. Tests

The ci guards are the repository's gates, so a guard that could never fail is the first thing
this pass looks for. Each guard of `ci/guards/`, `ci/test/` and `deploy/guards/` was read for
the shapes that make one: no assertion, assertions only under a branch or a loop over what may
be empty, a plant that fails for a reason other than the one it names, and a check of source
text that a comment can satisfy. A scan of every test's syntax tree found none of the first
two (each guarded loop runs over a literal or a list another test holds non-empty). Every
guard that reads the tree also holds plants that must fail (the layers, the kernels, the
refusals, module state, the literal guard, the timeouts and waits, the classes, the locks, the
legs and the merge queue's checks). Two checks could never fail:

| Check | Why it could never fail | Decision |
|---|---|---|
| `legs.test.ts` "run the owner's and the gateway's workerd memory legs": `toContain("--expose-gc")` | Each leg's header comment names the flag, so the check passes with the flag gone from its workerd config. Shown: `v8Flags = []` in `owner/memory/leg.mjs`, and the guard passed. | **Removed.** Each leg's own run fails without it: the owner leg's `/gc` answers 500 (`gc()` throws) and `get` throws on it; the gateway leg's `run(name, "gc()")` throws on the exception. The comment beside the check says so. |
| `own-workerd.test.ts` "has every request helper of each leg confirm its own workerd": the last `toContain("own.check();")` | Implied by the lines before it: at least one helper requests, and each must call `own.check()` after its request. | **Removed.** |

The memory legs' `toContain(control)` stays: the control's text is the problem it pushes, so
taking the control out fails it. It is tied to text, but only a run of the leg shows the control
working, and the leg is that run.

| Item | At this head | Decision |
|---|---|---|
| `x2:2.3` `LIVE`, a hand copy of the old proxy's routes | The fake zone's starting routes | **Kept.** The kit reads the live routes; `LIVE` is the world a test starts from, not a copy anything must change with. |
| `x2:2.3` four tests run Python | One: the founder's keyring maker (`scripts/platform_setup.py`) against the kit's `keyring()` | **Kept.** It is the one round trip between the setup that makes the ring and the kit that reads it. The other three are gone. |
| `x2:2.3` the workflow's job `if`s held by `toContain` | `runsIf` evaluates each `if` as Actions reads it, for every mode and ref | Done. |
| `x2:2.3` the six Worker folders pinned | Once, in `deploy.test.ts`, on purpose (a new Worker joins the deploy only by a reviewed change) | **Kept.** The other copies are gone. |
| `x2:2.3` `servedBy` re-implements the route matching | The test's oracle: the contract's `routeTakes` and Cloudflare's rule that the most specific pattern serves | **Kept.** The kit's `handover` and `narrower` are what it checks; an oracle built from the vendor's rule is independent of them. |
| `x2:2.3` the E2B event list, a third literal | `deploy.test.ts`'s `EVENTS`, beside `deploy.ts` `E2B_HOOK.events` and `doors/src/e2b-hooks.ts` `TYPES` | **Kept** in the test, as E2B's wire. **Found and left** in the code: the kit registers its list and the doors handle theirs, and nothing holds the two equal. One list in the contract is a code change, so not this pass: a sub-line of `tasks.md`'s re-check loop line (`untangle/e2b-hook-events`). |
| `x2:2.3` `REPO` defined again | From `ci/paths.ts` | Done. |
| `x2:2.5` `KNOWN_PRODUCTION_WORKFLOWS`, `PRODUCTION_SECRETS`, the `ci -> deploy` edge | The list names the platform's four; the harmless allowlist replaced the production list; the guard imports nothing of `deploy/` | Done. |
| `x2:2.5` the root `test` script the legs guard demands | Present | **Kept.** `npm test` in `platform/` runs every workspace, and the guard holds that it does; no leg runs it, so it costs nothing. |
| `x2:2.5` `timeouts.test.ts` pins the owner's setup files | It holds that a project's own setup files follow the class check | Done. |
| `x2:2.5` `TimeoutLiteral.call`, `.node`, `budgets.mjs`'s report mode | Gone | Done. |
| `v2:V2-ST5` `ci/budgets.json` names tests that no longer exist | Each names a test that exists, but the deploy guards' record, which names its test by an older title | **Fixed:** it names the test's title. |

The deploy kit's guards were read in full for the same shapes. Each mode's row runs whole
against the fake world and its plan and calls are read back, and each refusal is asserted with
what it changed (nothing). Nothing else could never fail. Two lines of "the deploy kit's facts"
look like copies of `facts.ts` and are kept: `[...WORKERS].sort()` against the folders is the
order's one check that it drops no Worker, and `MIGRATES` against the configs' migrations is
what anchors the `--migrate` refusals the same test drives from it.

## 3. Proof

- Every non-test source of `ci/` and `deploy/`, minified with esbuild, the same digest on base
  `70a45fc49` and head; the JSON data files differ in the two prose strings named above.
- The recorders: not run. The diff touches no `platform/*/src` and no code.
- Mutations:
  - `owner/memory/leg.mjs` with `v8Flags = []`: the removed check passed it (the proof it could
    never fail); the leg itself fails on it at its first `/gc`.
  - Each kept guard named in §2 was read against its plants; the plants are its mutations.

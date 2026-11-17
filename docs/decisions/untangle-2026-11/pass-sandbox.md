# The comments and tests passes for `platform/sandbox/` (2026-11-17)

d2 §E steps 4 and 5 for the sandbox area, both passes in one PR (`tasks.md`, the passes line).
The listing they start from is s2 (§2h tests, §2i comments), taken at an older head, so every
item was re-read at `origin/main` `5ec1105a7`: lane S (S2 to S9) and the C, X and test-waits
PRs had already fixed most of §2h and many of §2i's lines. Behaviour is unchanged. Every
non-test source in the workspace, bundled and minified with esbuild, has the same digest on
base and head, so no line of code changed, only comments. The recorders match.

## 1. Comments

**Kept:** vendor facts (E2B's wire, envd's proto3 zero omission, Node's and workerd's fetch),
spec pointers (ADR, `engines-replan.md`), measured limits with their sources, and the reason
comments: why an order, a retry or a refusal is what it is.

**Removed or rewritten:**
- **Provenance tags,** about 80 across the sources and the tests: `slice S`, `slice SIGNIN`,
  `slice P`, `slice R`, `untangle B11`, `(B16)`, `(B18)`, the migration notes' `(E2)` and
  `(S2)`, and the review and verification ids (`review H-1` to `M-8`, `L1`, `N2`, `N3`, `M3`,
  `R3-1`, `R4-3`, `R7-3`, `R8-1`, `F-1`, `F-2`, `H-5`, `M-4`, `L-c`, `d3 L9`, `rva6a`, `rvst`,
  `A1:` to `A3:`, `A6`, `A7:`, `S14`, `S25`, `S44:`). Git holds where a line came from. The
  sentence each tag hung on stays where it says why. A test title that was only a tag
  (`describe("verification round 2")`) is named for what it tests.
- **Stale comments:**
  - `src/index.ts` "Not deployed: no workflow deploys platform/". The kit deploys it.
  - `wrangler.jsonc` "Not deployable … its control namespace is a placeholder". The kit fills
    in the namespace's id (`deploy/deploy.ts` `deployedConfig`).
  - `runner/route.ts` "`ta-runner route-probe`, at every template build". It is the probes
    bundle's `route-probe`, at every pair check.
  - Every pointer to `codexurl_research.md`, a working file that was never in the repo.
  - `sizing.ts`'s "since deleted (untangle B1)".
- **Orphaned:** `control.ts`'s `/** A sign-in's id: si_ and 32 hex. */`, which sat above
  nothing once A1 moved `SIGNIN_ID`.
- **Restating the code,** 25 of them:
  - `control.ts`:
    - the docs of `backoff`, `Box`, `#digest`, `#log`, `#run`, `#install` and `#runnerState`;
    - the inline notes on `applied`, the give-back after a resume, and the renewal window's
      two terms;
    - "paused and idle: nothing to do" twice (`#arm`'s doc says it);
    - `#look`'s two notes, which repeat `#control`'s doc and `alarm`'s.
  - `slots.ts` `SLOTS`; `admin.ts`'s three webhook docs; `pid-lock.ts` `unlock`; `probes.ts`
    `PROBE`; `runner.ts`'s `living`, `liveJobs`, `liveRuns`, `livingJobs` and `syncLocked`.
- `runner.ts`'s header line, patched into one 140-character run-on line, is split into its
  own paragraph.

**Left as they are, each with its reason:**
- `control.ts`'s 63-line header narrates what its method docs also say. It is the one place
  that tells a start, the runner's adoption and the look end to end. Cutting it would leave
  that story nowhere.
- `template/ta-runner`, `template/image.py` and `template/requirements.txt` are in the
  image's tag. A comment there is a new image, a paid build, so they move with the next build.
  Their comments were read, and none is stale now: `ta-runner:17`'s "tests run a copy" was
  fixed before this pass, and `requirements.txt` no longer names a scratch path.
- The binding story told in both `src/index.ts` and `wrangler.jsonc`. Each says it of its
  own file: the entrypoint's reach, and the config's bindings one by one.
- Cross-area lines s2 §2i lists (`owner/src/sandbox.ts:10-19`, `contract/src/sandbox.ts:94-99`,
  `.github/workflows/sandbox-template.yml:22`). They belong to those areas' passes.

## 2. Tests

| Item | At this head | Decision |
|---|---|---|
| `s2:N50` `RECIPE_664` | Gone | Nothing to do. |
| `s2:N51` the SDK's serializer copied in Python | Present | **Kept, tied.** See §3. |
| `s2:N52` `passing()` | Re-derived each route's two turns and its resume, and the matcher's step against `STEP_NS` | **Reads the verdicts.** A check's `ok` is its probe's own verdict, and `checks.ts` records only a pair whose every step passed. A lower `STEP_NS` is a new probes bundle, so a new pair tag, which the deploy kit looks for. |
| `s2:N53` "carries none of the probes" | Matched `function <name>\d*\(` | **Matches the identifier**, `\b<name>\d*\b`: a `const`, an arrow or a renamed binding is caught too. The probes' own files are still held by the metafile's inputs. |
| `s2:N54` conditional assertion | `if (tree passed) expect(pin)` never ran (the tree's image has no record), and the probes-SHA loop ran over nothing (no record names its probes yet) | **`pinProblems`**, one finder for the three rules, run on `passed.json` and on planted records. Each rule fails on its plant today. |
| `s2:S92` `SPEAKS` asserted twice | `sandbox.test.ts` and `state-format.test.ts` | **Consolidated** into `state-format.test.ts`, the protocol's own test. The Worker's refusal of another protocol stays in `sandbox.test.ts`. |
| `s2:S95` the matcher re-tested in `sync.test.ts` | Two tests of `ignoredBy` and `wildcard` | **Consolidated** into `contract/guards/sync-ignore.test.ts`: git over 310 templates, its own file never ignored, and the cost bound in CPU time. |
| `s2:S90`, `S91` sleeps, wait literals | Fixed by test-waits 1 and 2 (#5605, #5612) | The waits guard holds them. |
| `s2:S94` the loader's absence test | Gone | Nothing to do. |
| `s2:N56` sync-ignore tests in `sandbox/` | Moved to `contract/` | Nothing to do. |
| `s2:N55` three fake E2Bs | Present | **Left.** Each runs in its own kind of process: a preload string in the checks' Node, the auxiliary workerd Worker (a single-module transpile), and the kit's recorded fetcher. Each copies E2B's wire, a vendor fact. A shared module that all three can load is a refactor of the test support, not this pass. |
| `s2:N57` the route verdict in the runner's suite | Present | **Left.** The probes ship with the runner as one pair. Their probe runs need the runner's root box, and the pure verdict cases are about 90 lines beside them. |
| `s2:N58` path literals in `runner.test.ts` | Present | **Left.** S-lane's wire freeze records each path in `protocol.json`, and `state-format.test.ts` holds the protocol to that record, so a path change fails that record first. The literals are the runner as a sandbox sees it. |

Also fixed: `runner.test.ts`'s sign-in cancel test ("its user's processes gone") read the
CLI's processes only `if` it found the CLI's record. The check never ran: the fake login
waiting for its pasted code writes its record only once it has the code, and a cancelled one
never does. The test now reads the pool's users' processes, live before the cancel (so the
check can see one) and none after.

Also fixed, at review: "renews at its deadline regardless" read the run's `renewing` as
soon as `renew-wait` returned. Past its deadline, `tell` waits for no answer (its timer is
the deadline, already past), so the test raced the run's drain and lost under load. Its 1.5 s
is a protocol argument, not a budget, so #5646's calibration rightly leaves it, and scaling
it would not help: the race is at the deadline, whatever its value. The test now waits for
the run's `renewing` and its frame by event. With the run's renewing post answered busy once,
the old test failed 4 of 4 and the new one passed 4 of 4. Found and left: the Worker passes
its own now as that deadline (`control.ts` `#renew`), so no renewal waits for its runs'
answers, though `Job` drains its posts before answering so that none is in flight at the
pause. Its fix needs the answer's bound researched first: a sub-line of the re-check loop line.

**Left, with the reason:** `test/sandbox.test.ts` reads or sets its own object's rows
through `runInDurableObject` in 11 places:
- to age a row's clock (`idle_since`), with no door that moves it;
- to mark a row's runner an older Worker's, which only a deploy makes;
- to read what no answer carries (the alarm set, `end_at`, `failures`, the rows after an
  erasure);
- to apply and change the objects' migrations, which is the schema test's subject.

These are the Worker's own suite on its own object, not another Worker's internals (B23's
subject). A front door for each is a product change.

**Found and left, cross-area:** with `sync.test.ts`'s copy gone, `contract/src/sync-ignore.ts`
`wildcard` has no reader. It is the contract's surplus: a sub-line of `tasks.md`'s re-check
loop line, for the contract area's pass or that loop.

## 3. `s2:N51`: the E2B SDK's serializer, copied

`guards/template.test.ts` checks the image's recipe without the SDK or the network, using a
45-line Python copy of e2b's `Template.to_json`. The copy and the SDK pin in `requirements.txt`
must move together, and nothing said so.

**Decided: keep the copy, and make the link one that fails.** `SDK_RELEASE` names the release
the copy is written to. The recipe test asserts that `requirements.txt` pins exactly that
release, and the tag test's SDK move reads it. A new pin now fails the guard until the copy is
checked against the new release and `SDK_RELEASE` moves with it.

**Rejected:**
- running the real SDK in the guard (a network install in a test, and `pip` in every leg);
- deleting the recipe check (an image step changed would then go unseen until a paid build).

## 4. The sandbox's swallowed errors and retries

`d2:N56` and `d2:N57` list only `doors/src/` files, so none of them is the sandbox's. They
ride with the doors area. The sandbox's own, from s2 (`S22`, `S24`, `S63`, `N8`, `N26`,
`N32`), were each re-read. All are kept, and behaviour is unchanged.

**Kept, each with the reason in the code beside it:**
- **The control object.**
  - `#serial`'s chain catch: each caller keeps its own rejection.
  - `login-drop` after a failed hand-over: the hand-over's error is rethrown, and the
    runner's `prepare` deletes any code left at the next resume.
  - `#freed` is logged: the line's own back-off rings it.
  - `#adopt` is logged as `sandbox_runner_adopt_failed`: an adoption never fails a start.
  - The look's failure is logged and backs off from the measured request time (S24, measured,
    kept by design).
  - `#pause` is asked again while E2B is busy, doubling up to the edge.
  - The line's doorbell doubles from `LEASE_TTL_S` up to the runtime ceiling.
- **E2B's answers.** 404 is `null` or `gone` and 503 is `busy` (vendor answers). An error
  body that is not JSON leaves `code` null.
- **The runner.**
  - The event poster and the sync client back off and retry while the door is unreachable,
    busy or limited, bounded by the lease.
  - A sync door's error body that is not JSON is `http_<status>` (N32).
  - The sessions-seen cache is unreadable as empty, so every session is hashed again.
  - The presence report is made again at the next start.
  - Parse-or-null readers: a CLI line that is not JSON is the CLI's text, and a settings file
    that is not JSON has no names.
  - The `/proc` and kill races mean the process is gone already.
  - stdin's EPIPE: the child's exit status says what happened.
  - The kill loop waits a round between its passes (N26, fixed before this pass).

**Found and left:** `runner.ts` `safeSignedIn` reads any failure of the status check as "not
signed in", so the sign-in ends `failed` with nothing in its log, where `loginRun`'s own
catch logs its why. The fix is one log line in the runner. That is a runner change, so a new
runner digest and pair, and this pass changes no code. It is a sub-line of `tasks.md`'s
re-check loop line.

## 5. Proof

- Every non-test source's minified esbuild bundle has the same digest on base and head.
- The recorders: `wire`, `tools`, `rpc` and `rv` are the same.
- Mutations, each failing its test:
  - `pinProblems` without the tree rule;
  - `passing` without its logins or its matcher;
  - the pin set to a tag with no record;
  - `requirements.txt` pinning another SDK release;
  - the runner importing `stepNanos` as an arrow function (the old regex passes this one);
  - `SPEAKS` widened past one deploy.
- The sign-in cancel test's process check is shown live by the test itself: asked while the
  login waits for its code, it sees the login's process.

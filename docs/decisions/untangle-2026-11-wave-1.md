# Untangle the new build: decision log

The founder asked for the `untangle-codebase` skill to be run on `platform/` before the cutover, with both of its additions: **Comments** and **Tests**. This is the skill's step 3, the redesign. Each fix sits above the problems it solves. Problem IDs point into the five listings:
- `list_owner.md` (ids `o:` plus section and line, e.g. `o:2a`);
- `list_doors.md` (`d:A1` and so on);
- `list_sandbox.md` (`s:S14`);
- `list_services.md` (`v:G-H1`);
- `list_cross.md` (`x:2.1`).

The listings, and the other working files this log names (`*_research.md`, `*_report.md`, `brief_common.md`), were not kept in the repo: a name or an id points to the evidence, and each decision stands without it. The later logs are [wave 2](untangle-2026-11-wave-2.md) and [the whole remaining build](untangle-2026-11-remaining-build.md).

Ground rules:
- **The owner migrations' SQL text is frozen** (pinned at 9ea1bbd2b). Fixes may change where that text is written, never the text.
- **Live names stay**, except where a fix below says otherwise: Worker, binding, KV/R2/queue and secret names.
- **Behaviour stays the same**, apart from the bugs named in §B.

## A. The redesign: seven root fixes

The import graph shows the core tangle. Doors and owner import each other (88 and 35 edges), and so do owner and gateway (17 and 20). Behind that, each Worker uses another Worker's `src` as its library. Most of the hidden and needless links in all five listings come from that, so fix A1 comes first and the others build on it.

### A1. One `platform/contract` workspace that every Worker depends on, and that depends on nothing

It holds the vocabulary more than one Worker speaks, with no runtime state:
- ids and grammars;
- the size and time limits (chunk size, sync bounds, lease TTL, egress body and deadline, batch sizes);
- **typed refusals**: `{code, status, retryAfterS, facts}`, with the message only as human text;
- the egress wire protocol (paths, headers, codes);
- the control record and its gates;
- slots and holds;
- wake records and kinds;
- the egress-scope and API-rules grammar;
- the model-record checker;
- run events;
- the socket shape;
- each service's RPC contract, for gateway, files and hooks: `api.ts` types and the code list.

Two moves complete it:
- **The source records** (schema, checker, catalog, `runEnv`, sign-in shapes) move out of `doors/src/sources.ts` into `platform/sources/` beside their JSON, as a contract module.
- **The routing-MAC token format** joins the contract.

After A1, no Worker imports another Worker's `src`.
- Solves:
  - `o:2b` (every item), `o:2c`, and `o:2d` (4 MiB ×4, `EGRESS_HOST`, the id regexes, `SLOT_WAIT_MS`);
  - `d:A1`–`A8`, `A10`, `A12`, `A14`, `B9`–`B14`, `B17`, `B23`, `C6`, `D1`–`D5`, `D9`, `D10`, `E1`, `E3`, `E4`;
  - `s:S1`, `S5`, `S6`, `S16`–`S21`, `S19`, `S44`–`S49`, `S79` (the regex half);
  - `v:G-H1`–`G-H9`, `G-H14`, `G-N1`, `G-N2`, `F-H2`, `F-H3`, `F-H5`, `K-H1`, `G-T3`;
  - the spec question `v:S2` (resolved: the gateway imports only the contract).
- It also fixes, as a side effect:
  - the runner's drifted credential regex, which lacks `PROXY` (`s:S44`);
  - the doors' five refusal parsers;
  - the 17 owner branches that match on message text;
  - the gateway's four code regexes.

### A2. Owner doors as data, and owner-do as a router

One door table, owned by the owner, holds each door's name, class, capability, argument schema, handler module and gate. One runner does bind, then account, then scope, then fields, for every door. `authority.may(account, capability, where)` replaces the nine capability checks.

The doors' MCP tool table reads the owner's door table through the contract instead of copying it. The features inside `owner-do.ts` move to their own modules:
- `archive.ts`;
- `launch.ts`, which gives Runs one narrow interface instead of 20 callbacks;
- egress pricing into `egress.ts`;
- page fitting into `pages.ts`;
- code-mode orchestration into `codemode.ts`.

Solves:
- `o:2a`, `o:2j` (`RPC_METHODS`, the AST-parse guard, `door-class.test.ts`), the shallow hub, and the `ownerLanes` callbacks;
- `d:T1`, `d:A8` with `d:R6` (owner-surface routes as data, one dispatcher), and `d:E8`, `d:F9`;
- the co-change: `owner.test.ts` changes in 28 of `owner-do.ts`'s 40 commits, and `doors/guards/tools.test.ts` in 15.

### A3. Platform facts declared once

Three changes:
- **Deploy facts:** the six `wrangler.jsonc` are the one source for Worker names, bindings, secrets, buckets, namespaces and queues. The deploy kit, the workflow's apply env, the environment guards, `platform_setup.py`, the checklist and the generated binding types (`wrangler types`) all derive from them. What no config can say goes in one small data file, `platform/deploy/platform.json`.
- **The doors' public identity:** origin, routes, the first-party client, the hook path, the egress host and the control key go in one JSON beside `doors/src/config.ts`. Doors, `deploy.ts`, `plugins/build.mjs` and the CSP read it.
- **The deploy kit becomes a mode table:** `plan`, `dark`, `probe`, `routes`, `h2`, `pair`, chosen with `--mode` and built from small steps that take their inputs as arguments. It gets one Cloudflare client and one E2B client, shared with the sandbox. Its tests call those functions and stop matching source text, and they move to their own `platform/deploy` test project.

Solves:
- `x:2.1` (secrets ×11, `tinyassets-${worker}`, the order, queues, the hooks special case, `main()`'s 230 lines, the eight `if (open)` branches, `planned-` ids, the boolean flags, `--h2`, the `process.env` globals);
- `x:2.4` (mode mapping ×4, the apply env copy), `x:2.7`, `x:2.10` (the text-pinning tests);
- `d:B1`–`B3`, `B16`, `T2`, `T5`;
- `v:W-H1`, `W-H2`;
- `o`'s test namespace lists ×4.

### A4. One shared test and guard kit in `platform/ci/`

It holds:
- the wrangler JSONC parser (12 copies today), locating wrangler (5 ways), and bundling a Worker for miniflare (5 copies);
- the fake-Worker transpiler (4 copies);
- the guard toolkit, moved out of `owner/guards/sources.ts`, with each Worker's config and import allow-lists as data checked by one checker;
- a `project()` helper that sets a vitest project's name, glob and budget from one key;
- the shared fixtures: the dark control record and its tiers (×8), keyring makers, the provider sources, `sqlIn`.

Solves:
- `x:2.6`, `x:2.7` (`REPO`/`PLATFORM` ×7, `budgets.mjs`'s copy), `x:2.8`, `x:2.9`. The last includes the files guard that re-runs the whole owner config suite.
- `v:R7`, `v:W-N1`, `v:F-N1`, `v:G-T5`;
- `d:R11` (the guard kit and the bundler);
- `o:2d` (the namespace lists).

### A5. One sealed-envelope library for both keyrings

It covers ring parsing, HKDF to AES-256-GCM with domain separation, the `v1.<kid>.<nonce>.<ct>` codec and base64url. `gateway/keyring.ts` and `files/datakey.ts` become thin bindings over it, and `deploy.ts`'s keyring maker uses it. The blob bytes are already identical, so no data moves.

Solves `v:F-H1`, `G-H3`, `G-H10`, `G-H12`, `F-H4`, and `x:2.1`'s third keyring parser.

### A6. The sandbox: one protocol, one job and one launch, and probes outside the runner

Four parts:
- **A typed protocol:** `runner/protocol.ts` names the Worker↔runner commands, exit codes, state layout and documents, and both sides dispatch from it.
- **One `Job` in the runner:** a run or a sign-in, with one `pidLock` in place of four locks and the socket channel in place of ack-file polling. The Worker gets one `#launch(kind, …)`.
- **Probes outside the runner:** the template-build probes become their own bundle entry, so the shipped runner stops importing owner, gateway and source-record code. `fsafe` becomes one walker, one write path and one read path.
- **Its own inputs:** the Worker stops importing doors modules and takes its inputs from the contract.

Solves `s:S1`–`S4`, `S7`–`S12`, `S29`, `S50`–`S62`, `S73`, `S74`, `S16`–`S21`.

`build.py` splits into a tagged image definition and an untagged checks runner. That needs one paid template rebuild, and every template-rebuild item is batched into that single rebuild. The split removes the workflow's inline copy of the checks, the second `pair_tag`, and the TypeScript copy of `tag()`. Solves `s:S79`–`S88`, `S99`.

**Revised 2026-11-09 (`a6a_report.md`):** the probes leave the runner in part b, not part a. The reason is how `build.py` runs them:
- it can only run probes inside the one `runner.mjs` the Worker ships (`ta-runner` runs only that file, offline);
- the pair tag is that file's hash.

So the probes can't leave the runner until `build.py` changes. Part a builds the protocol, one `Job` and one `#launch`, and one `fsafe`; the probe commands stay, marked build-only. Part b moves the probes into their own bundle, and the pair then names the runner and the probes bundle. It carries part a's "clean runner import graph" and "probe import" checks.

**Part b decided 2026-11-09 (`a6b_research.md`):**
- **The pair tag** is `<image tag>-r<runner sha16>-p<probes sha16>`. The probes' digest is part of the tag, not only recorded, because the probe bundle alone carries production code (`runEnv`, `sandboxEnv`, `egressBaseUrl`, the egress scope, `STEP_NS`). A probe-only change then costs a pair check, as it does today. A runner-only change still costs no template rebuild.
- **Proving the bundles together:** `bundle.mjs` builds `runner.mjs` and `probes.mjs`, with the runner's digest embedded in the probes. The checks list lives in the probes. Each check sandbox installs the runner, runs its real `prepare`, then installs and runs the probes.
- **`ta-runner` is unchanged.** It already runs any module by digest, so the new checks path can be proven on today's template before the paid build.
- **`build.py` becomes:**
  - `image.py`, which is tagged and holds only the E2B steps;
  - `image.ts`, the image spec and the one `tag()`;
  - `checks.ts`, the untagged checks runner;
  - `pair.ts`, whose `bundles()` and `pairTag()` the deploy kit imports, so its pair check stays one `ready()`.
- **Other moves:** shared helpers go to `runner/session.ts`, and `src/install.ts` becomes the one install path.
- **`PROTOCOL` stays at 1,** with the build commands marked retired.
- **Every template-rebuild item rides it:** S2, S18, S20, S33, S58, S72, S79–S86, S88, S89 (the template half) and S99. S1 is already done, and S87 is free.
- **The image is unchanged byte for byte.** The new `image.py` must serialize to main's recipe (sha `8266c6a6…`, 21 steps, via the pinned SDK's `Template.to_json`).
- **Proof:**
  - offline in the PR: recipe identity, tag mutations, the runner import-graph guard, today's probe tests on `probes.mjs`, the checks runner on a fake E2B, and recorders and harness differing only in the pair tag;
  - then an optional live pair check on today's template, inside the approved spend;
  - then the one paid build, then a pin PR.
- **The pin check** moves from CI to the deploy kit's `template` step, so the deploy refuses for about an hour between the merge and the pin PR.
- **Order:** part c is already building. Part b goes after it lands, and part c's test-root layout then comes from `image.ts`'s spec.

**Test seams:** removing them from product code (`s:S34`, `S64`–`S66`, `S71`, `S75`, `S76`, `S96`, `S97`, `S100`) needs research first (Fact 14). The question is whether the runner tests can run as root in a mount namespace, so `/` is the root.

**Decided 2026-11-09 (`a6_seams_research.md`), built as A6 part c after part a lands:**
- **The test root.** Each test sandbox gets one namespace root:
  - a long-lived process in its own mount and pid namespaces `pivot_root`s into a temp directory holding the template's layout (users, modes, the fake CLIs, Node, the loader unchanged);
  - the host's `/etc` and `/usr` are `metacopy=on` overlays over the host's (the host's beneath, never written; the box's writes above, on a tmpfs of its own outside its root), so `prepare` takes another user's write away in the box as it does in a sandbox, and nothing is copied (a CI host's `/etc/skel` holds gigabytes, and its `/usr/lib/jvm` is writable by others); the rest of the host's system directories are bound read-only; Node is one root-owned (`root:root` 0755), byte-checked copy of the host's, made by the suite before any box and bound read-only at `/usr/local/bin/node`, never the host's own (a CI host's `setup-node` is another user's); with a minimal `/dev` and a private `/dev/shm` and `/tmp` (revised 2026-11-09 after #5484's CI runs);
  - each runner command enters with `nsenter` as `ta-runner <sha> <cmd>`;
  - teardown is one `kill -9`.
  - Plain bind mounts are rejected, because they would create paths on the host. So are docker and one injected root, which only renames the seam.
- **The seams that go:** S64's root, path and reserve overrides; `ownDigest` null; S65 and S66, which are dead; S71 (`openMirror(":memory:")` and a loopback HTTP server); S75 and S97 (a real tmpfs); S76 (a pure function); S34 (`runInDurableObject`); S100 (move the files under `test/`).
- **What stays:** the `E2b` fetcher, which the deploy kit uses. Nothing env-driven stays: `TA_HEARTBEAT_MS` goes too, the heartbeat is the constant 20 s, and the tests that relied on a shorter one (the sync-out heartbeat test, the renewal's held posts) run at the real heartbeat as `large` tests (rva6c F2).
- **The uid locks** (`claimIds`) go.
- **New:** a preflight in `root-tests.mjs` that exits 2 with its reason, never a skip, and a guard test that every host path a test sandbox sees is read-only.
- **Proof:** this is unmeasured on GitHub's `ubuntu-latest`, so the first PR's `runner` leg is the proof.

### A7. Every Durable Object's schema is frozen, migrated and locked by CI

Three parts:
- **Owner:** each pinned migration is written out once as its literal SQL. The text and the digests stay the same. The bounds that SQL enforces are read by their TypeScript owners from one frozen `limits` module. This removes schema's imports of 14 modules and `files/src/keys.ts`, `lifecycle-stub.ts`, and `run-rows.ts`'s reason to exist (`o:2e`). Co-change: `schema.ts` changes in 36 of `owner-do.ts`'s 40 commits.
- **Sandbox:** `SandboxControl` and `SandboxSlots` get a migrations list and a pin the same way, as migration 1. This happens **before the sandbox's first deploy**, which hasn't happened yet: dark stopped at it (`s:S25`).
- **The lock:** the append-only lock runs as a TypeScript guard in CI, and the platform test checkout fetches the base. That replaces the disabled `migrations-append-only.test.ts` and the Python copy the cutover deletes (`x:2.6`, `x:6`). **Done by #5451 (gates 4-3):** the guard is no longer excluded, and the platform jobs fetch full history.

## B. Bugs the listings found (fixed in their wave, with tests)

| Bug | Where | Fix |
|---|---|---|
| The runner's credential regex lacks `PROXY`, so a project `.env` can set `HTTPS_PROXY` | `s:S44` | wave 0, then by A1 |
| A sandbox first made by a sign-in has no `ta_route`, so its lifecycle (billing) events can't be routed | `s:S14` | wave 0: `route` on `SandboxSignin` (decided below) |
| The OKF watcher's `apply` rebuilds the starter but not the plugins, so every spec change it proposes fails a check | `v:P-H1`–`P-H5` | wave 3: one generator (`v:R8`) |
| The gateway's `forward()` would send model calls unmetered (only a test uses it) | `v:G-S1`–`G-S5` | wave 3: delete the unused surface (`v:R4`) |
| A gateway key can never be proven retired: nothing calls `reseal` | `v:S3` | wave 3: the owner's reseal sweep (decided below) |
| A doors test reads the old stack's files and fails at the cutover | `d:T6` | wave 0 |

## C. Spec questions: decided

Founder rule: decide, don't hand over options. Each decision below keeps behaviour unless it says it fixes a bug.

- **`v:S1` egress deadline (spec 5 min, code 24 h).** The declared difference at `owner/src/egress.ts:70-72` concerns policy writes ending a stream, not the deadline's length. **Researched** (`egress_deadline_research.md`).
- **Where five minutes came from:** gateway research §6, so that a stream already flowing could finish. That reason went away once a policy write ends the stream.
- **What the door carries:** single streamed model replies, which can run 20 to 107 minutes at 128K output tokens.
- **What bounds it:** Cloudflare sets no duration limit while the client stays connected. A stream can't outlive its E2B sandbox (1 h or 24 h). The charge is capped at reservation, so billing is safe.

**Decided: keep one day (86,400 s) and fix the text**: `specs/gateway/spec.md:69`, `specs/doors/spec.md:451`, `tasks.md:944` and the stale reason in `gateway/src/forward.ts:40`. `owner-object/spec.md:933` already says a day. This rides step 3.

The research also found that a stalled stream trickling bytes holds one of its run's 16 slots until it ends. **Decided: an idle timeout in the gateway's forward** (no bytes for N seconds ends the stream), its own small PR in step 3. N is taken from the engines' own idle cut (about 5 min). Behaviour changes only for a stalled stream.
- **`v:S3` reseal.** Retiring a key is the reason to have a keyring, and the files keyring already has its sweep. **Build the owner's gateway reseal sweep**, mirroring `dataKeyRewrap`. It is the spec'd behaviour, still missing.
- **`v:S4` the files cipher.** This stays the live probe's decision (slice L). Not part of the untangle.
- **`v:S5` narrow `SUPPORTED_APIS`.** An API with no rules and no source record can't work. **Narrow it.**
- **`v:S6` OpenAI directory skills.** This is vendor research for the directory listings (Direction 7). Not part of the untangle.
- **`d:T1` the tool-guard scenario names private methods (doors spec:77-78).** **Amend the scenario** to state the behaviour: every tool's capability and schema come from the owner's door table. A2 makes that true by construction.
- **`d:F6` uncurated sources.** The rule moves into the source-record checker, which checks every record, so it holds the moment one exists. **Delete the unused parameter.**
- **`o` tiers by binder or `CONTROL`.** The runs lane already has to read `CONTROL`, because alarms have no binding. **Revised 2026-11-08 (A1 part 4's build, `a1p4_report.md`):** the doors keep handing tiers and sign-ins in the binding, and only the lanes read `CONTROL`. Both use the contract's one control module. Having the object read `CONTROL` on every door failed:
  - waiting for the read moves erasure, files and runs ordering (14 tests), and adds a KV read to every door and to every code-mode tool call;
  - not waiting makes a founder's change arrive one read late;
  - either way, the starter seed starts at sign-up.

  The doors already read the record for their gates, so handing the numbers costs nothing. The spec's "handed by the binder" stays. One parse and one module is the fix; only duplicate writes that don't change behaviour go. The sandbox alarm's failure counter stays in `known`, because moving it needs a migration the lock doesn't pin.
- **`s:S14` a sign-in's sandbox route.** A sign-in's sandbox costs money like any other. **Add `route` to `SandboxSignin`** (bug, §B).
- **`s:S69` user-facing text built in the runner.** Events carry codes and facts, and the doors and app render the words, the same as typed refusals (A1).
- **`x` the `--h2` and flag names in tasks.md.** Update tasks.md to the mode table (A3).
- **`x` probe workflows.** The owed 10M-subrequests check stays. Only the finished one-off probes go.

## D. Order of work

Each step is one or more reviewed PRs. Gates come from `brief_common.md`; tests are run by size class.

0. **Before re-running `dark`:**
   - the build-cwd fix (in review);
   - the sandbox DO migrations and lock (A7, sandbox part);
   - the credential regex;
   - sign-in `route`;
   - the dead legacy mirror (`s:S68`);
   - the old-stack-reading test.

   Then `dark` again, and `probe`: the founder's one sign-in, to surface live problems early.
1. **Foundations:** A4 (the ci kit), then A1 in parts:
   - vocabulary and limits;
   - typed refusals;
   - source records;
   - the control record;
   - the service contracts.
2. **On the foundations, in parallel where the files don't overlap:**
   - A2 (owner doors and modules);
   - A3 (facts and the deploy kit);
   - A5 (keyring);
   - A6 (sandbox, with its one paid template rebuild);
   - A7 (literal migrations and the lock).
3. **Surplus:**
   - `o:R8`: dead exports, test-only product helpers;
   - `d:R12`, `s` smaller items;
   - `v:R4`, `v:R5`, `v:R6`, `v:R8`, `v:R9`, `v:R10`;
   - `d:R10`: fold `admin/` into owner admission;
   - `x:7`: the finished checklist half, stale config prose, one-off probes;
   - the reseal sweep.
4. **Comments:** remove the comments that restate the code (counts per file in each list). Keep the ones about vendors, specs, measured limits, workarounds and reasons.
5. **Tests, last:** remove tests that repeat the implementation or can't fail, and rewrite tests tied to internals against behaviour. The listings' `[test]` items, about 70 in all.
6. **Check:** fresh agents search for special cases, hidden links, needless links and surplus. Repeat 1–5 on what they find until nothing worth fixing is left.

   Done means:
   - few special cases left;
   - a typical change lands in one place;
   - every import points at something steadier than itself;
   - the full test suites show behaviour unchanged.
7. **Redeploy `dark` with the clean build**, `probe` passing, then the founder's go for `routes` (the cutover).

## D2. Guards, and what proves existing users and data still work

Each root fix leaves a CI guard so the problem can't come back:
- **A1:** the kit's import allow-list. No Worker imports another Worker's `src`, and the contract imports nothing.
- **A2:** the MCP tool table is generated from the owner's door table (a missing row is a compile error).
- **A3:** facts are derived from the Worker configs (no literal lists elsewhere), and a bundle test runs on each staged config (#5450).
- **A4:** the kit's own mutation-checked tests (#5453).
- **A5:** golden blobs sealed by today's code must open byte-for-byte.
- **A6:** one frozen `protocol.json` that both sides dispatch from, plus the deploy's live pair check.
- **A7:** digest locks, the append-only CI check (#5451) and the kit failing closed (#5452).

Each bug fix carries a test that fails without it.

**Behaviour.** Every fix PR must show before-and-after identity:
- the MCP tool list and schemas;
- the bytes on the wire;
- the read-only `plan` output;
- the frozen SQL text.

**Cutover blocker (added 2026-11-07): the import of existing owners.** tasks.md §9 imports every existing owner's stores into their object and their volumes into object storage. The founder's and the canary's owners are the exception: they start fresh. No importer exists in `platform/` yet. Before `routes`:
1. a census of the real owners on the old stack;
2. the importer, built and reviewed;
3. a rehearsal that imports a copy of the production backup into the dark platform;
4. a per-owner check that every row and file arrived, by count and checksum;
5. the old stack kept, untouched, until that check passes (Fact 13).

The founder's answers of 2026-11-07 are recorded in `import_research.md` under "The founder's answers":
- the rehearsal on real data is approved;
- no paid subscriptions are expected, and the census confirms it;
- there's no announced window: we go when we both agree;
- destroying the droplet is OK'd, after a 14-day stopped hold and a heads-up;
- the census edge cases take the defaults.

**When to re-run (the founder, 2026-11-10).**
- Run it again whenever it seems warranted, until we're sure it's all in place.
- Run it once more when the build is done and we think we're cutover-ready.
- A stale test, guard or rule never blocks an untangle fix: it changes in the same PR.
- Reviews must not re-tangle, and each round checks for it.

**Future use of the skill.** It becomes a repo skill (`.claude/skills/untangle-codebase/`) with this repo's rules: frozen migrations, live names, the paid template rebuild, and review receipts. (Built as `.agents/skills/untangle/`, #5508.)
- **When to run it:** before each cutover or major release, after a large feature push, and when the import guard or co-change history shows drift.
- **What it leaves:** the decision log is kept in the repo (this folder, since 2026-11-12).
- **What happens to each class of finding:** it becomes a guard, so later runs only find new drift.

## E. Problem index

Every problem in the five listings is covered by one of A1–A7, the bugs in §B, the surplus in step 3, the comments in step 4 or the tests in step 5. Any listing item not named above falls under step 3 when it is `[surplus]` or `[special]`, step 4 when it is `[comment]`, and step 5 when it is `[test]`. The step-6 check confirms nothing is left over.

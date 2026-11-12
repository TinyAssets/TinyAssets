# Untangle, wave 2: decision log (B1–B26)

This is step 3 (the redesign) over the six re-run listings. Each fix sits above the problems it removes. Problem ids:
- `o2:` is `list2_owner.md`, by section and short name, for example `o2:2a OwnerState`;
- `d2:N1` is `list2_doors.md`;
- `s2:N1` or `s2:S6` is `list2_sandbox.md`;
- `v2:G-S1` or `v2:V2-H1` is `list2_services.md`;
- `x2:2.1 admitsNobody` is `list2_cross.md`, by section and short name;
- `i2:I-H1` is `list2_import.md`.

The listings, and the other working files this log names (`*_research.md`, `*_report.md`, `*_table.md`, `u3_redirect.md`), were not kept in the repo: a name or an id points to the evidence, and each decision stands without it. Wave 1 is [`untangle-2026-11-wave-1.md`](untangle-2026-11-wave-1.md). The log of the whole remaining build, [`untangle-2026-11-remaining-build.md`](untangle-2026-11-remaining-build.md) (`d3`), amends this one where it says so.

**Ground rules carried from wave 1:**
- The owner migrations' SQL text stays frozen.
- Live names stay, except where a fix below says otherwise.
- Behaviour stays the same, apart from the bugs named.
- A stale test, guard or rule changes in the same PR as the fix it blocks.
- The u3 builders (`u3_redirect.md`) land first. Each lane starts from main after its area's u3 PR. An item a u3 PR already did is not repeated here; one it left "owed to a root fix" is taken by the fix named.

**Already done by u3, so not repeated** (corrected 2026-11-12 by d3 §O5: the u3 PRs put back the four struck items, and each is owed where it says):
- the gateway default entry's extras and the unmetered `forward()` (`v2:G-S1`, `G-S2`);
- `SUPPORTED_APIS` narrowed ~~and `MISTRAL` derived~~ (`MISTRAL` is still a near-copy of `COMPLETIONS`: R5′, lane G's first PR);
- the files cipher as one strategy ~~, with `FILES_CIPHER` required~~ (still optional: T0b, d3 §M6);
- ~~the OKF `apply` building the plugins, and~~ `BUNDLED` (`okf-watch apply` still runs only `bundle.mjs`: P-H1, B9; fixed by C2, #5518: `apply` runs `npm run generate`, and the plugins take their skills from `manifest.json`);
- the owner's gateway reseal sweep;
- `appOf` ~~and `signinEventsUrl`~~ deleted (`signinEventsUrl` is kept: B6's `signinUrls(id)`);
- the E2B hook events in `identity.json`;
- the doors' local cleanups in `u3-doors_table.md`.

**One mechanism used by several guards below, the `owed` ratchet.** A new guard can land before the fixes that make it pass:
- Each workspace's `guards/rules.ts` holds an `owed` list: `{guard, what, fix: "B<n>"}`.
- The ci kit fails on any violation that is not listed, and on any listed entry that no longer violates. So the list only shrinks, and each lane edits only its own workspace's list.
- Every `owed` list must be empty before step 6's re-check counts as done.

## A. The root fixes

### B1. One import-layer table over every file, not only each Worker's `src/`

**Removes:**
- `o2:2b` (owner tests and memory reaching gateway, files and doors `src`), `o2:2f matcherprobe`;
- `d2:N44`;
- `s2:N45`, `s2:N53`;
- `v2:V2-N1`, `V2-D11`, `V2-D14`, `V2-ST5`;
- `x2:2.5 isolation reach`, `x2:2.4 sandbox-template inline import`, `x2:2.1 deploy→sandbox/src` (the guard half);
- `i2:I-N9`, `I-ST1`, `I-T9`, and the TypeScript half of `I-D1`.

**Design:**
- `ci/kit/layers.ts` is data. Every `.ts`, `.mjs` and `.js` file under `platform/` and `scripts/` matches exactly one layer by path, and each layer names what it may import:
  - `contract/src`: nothing but its own data;
  - `sources/src`: `contract/src`;
  - `<worker>/src`: its own folder, `contract/src`, `sources/src` (and, for the owner, `starter/starter.json`);
  - `sandbox/runner` (the runner entry): `sandbox/runner`, `contract/src`, `sources/src`, but not `route.ts` or `probes.ts`;
  - the probes entry: the same, plus those two;
  - `sandbox/template`: `sandbox/runner`, `sandbox/e2b` (B11) and the contract;
  - `<ws>/{test,guards,memory}`: its own workspace, `contract`, `sources` and `ci`, and never another Worker's `src` or `test`;
  - `deploy`: `contract`, `sources`, `sandbox/e2b`, `sandbox/template/pair.ts`, `ci/paths.ts` and `ci/wrangler.ts` (B11);
  - `ci`: `contract` and its own files, and its guards may read what they guard;
  - `import`: `contract`, `deploy/{facts,cloudflare}.ts` and `ci/paths.ts`;
  - `scripts/`: nothing in `platform/`.
- The cutover layer (B24): files on the cutover manifest may import each other, and nothing outside the manifest imports them, except the entries the manifest names.
- An unmatched file fails the guard.
- Workflow run steps that import a module inline (`node -e "import … from './…'"`) are parsed and checked as their own layer: no Worker `src`, and no `deploy/deploy.ts`.
- The per-module `outside` lists in `gateway/guards/rules.ts`, `import/guards/rules.ts` and the contract's and hooks' rules are deleted. A workspace keeps only banned names (`OWNER`, `getByName`, `process`, the keyring).
- `scripts/probes/matcherprobe/` is deleted: it cannot build, and the heap constants it measured are recorded at their definitions.
- Today's disallowed edges go into each workspace's `owed`, tagged with the fix that removes them (B9, B11, B23, B24).

**Files:**
- `ci/kit/{imports,layers}.ts`, `ci/guards/isolation.test.ts`, `ci/budgets.json` (the titles `V2-ST5` names);
- `{gateway,import,contract,sources,hooks}/guards/rules.ts`;
- `scripts/probes/matcherprobe/`.

**Guard:** the table itself, with mutation cases: a runner import of `owner/src`, a test reaching another Worker's `src`, an inline workflow import of `deploy.ts`, and an unmatched file.

**Proof:** no product file changes. Every guard passes.

**Risk:** low. Not release-critical; the workflow check only reads. No live name. **Image:** no.

### B2. Every answer is a contract refusal: gates, doors, start codes, files and hooks

**Removes:**
- `d2:N1`–`N9`, `N23` (the status half), `N57` (the per-door `Retry-After`), `N87`;
- `o2:2a NOT_GRANTED ×2 codes`, `o2:2c` (the `{"retry_after_s":30}` JSON ×3, and the prefix convention);
- `s2:S6` (`CODES`, `Started.code: string`);
- `v2:V2-SP1`, `V2-D8`, the refusal half of `V2-H6`.

**Design:**
- `contract/src/refusal.ts` gains:
  - the gate code `unavailable` (503, retry 60), whose messages stay `MAINTENANCE` and `OUTSIDE_OFF`;
  - the doors' codes `runner_token_missing`, `run_secret_missing`, `sync_token_missing`, `sync_token_unknown`, `sync_token_expired`, `events_too_large`, `method_not_allowed`, `egress_gateway_failed`, `egress_deadline`, `egress_aborted` and `egress_ended`;
  - the codes `KeyRefused` and `ConnectRefused` carry, each with today's status;
  - the files codes `keyring_unavailable` (shared with the gateway), `data_key_refused`, and `root_changed` (transient, carrying "run it again").
- `EGRESS_CODES` becomes the type of the egress door's `refused(code: EgressCode)`. Its status comes from `REFUSALS`, never from the call site.
- `contract/src/sandbox.ts` gains `START_CODES` and `Started.code: StartCode`. The sandbox Worker and the owner (`SANDBOX_WAITING`, `SIGNIN_REQUIRED`) import it.
- Each surface has one renderer, in `doors/src/refused.ts`: JSON `{error,message}`, egress `{error:{code,message}}`, MCP results, the inbound answer, the events error, and the authorize redirect (`temporarily_unavailable`, OAuth's own).
  - One `answered(surface, fn)` replaces the 17 catch blocks.
  - `shownCode` reads `.code` (a quiet code shows its message). Nothing parses a message for a code. `codeOf` in egress goes.
- Message texts stay byte for byte, except:
  - the three `storage_busy` messages lose their JSON, and carry `retryAfterS` as a field;
  - **bug:** the egress door's closed gate answers in the egress shape, which a provider SDK can parse;
  - the inbound door's closed gate becomes `unavailable` with the contract's `Retry-After` (was `maintenance`/`unavailable` with 5 s).
- `NOT_GRANTED` is `not_granted` everywhere. That code has the same status and quiet flag as `refused`, so the wire is unchanged.
- The refusal module's header loses the "named by its prefix" paragraph.

**Files:**
- contract part (lane C): `refusal.ts`;
- `contract/src/sandbox.ts` (lane S);
- adoption: `doors/src/{refused,control,egress,api-keys,connect,app,api,mcp,inbound,file-bytes,erase,events,authorize}.ts`, the owner's `authority`, `ledger`, `grants` and `owner-do` call sites, `sandbox/src/control.ts`, `files/src/*`, `hooks/src/index.ts`, and the tests that match message text.

**Guard (ci kit, `owed` ratchet):**
- in any Worker's `src`, no class extends `Error` except `Refusal`;
- no code is derived from `.message` (`startsWith`, `split(":")`, a regex on a message);
- `contract/guards/refusal.test.ts` is the one code→status table test (`d2:N87`'s copies go).

**Proof:**
- the recorders (wire 804, tools/list, both RPC recorders) differ only in the three `storage_busy` texts, the egress door's gate answer and the inbound gate's answer, each listed in the PR;
- frozen SQL is unchanged;
- the runner bundle changes only if the runner names a start code (a pair check, not a build).

**Risk:** medium, because it touches many surfaces. Not release-critical. No live name. **Image:** no.

### B3. Owner modules declare what they need; `OwnerState` goes

**Removes:**
- `o2:2a` (`OwnerState` as service locator, the 27 module⇄hub cycles, two wiring styles, the `keepTiers` ×3 paths, the `tier`/`admission` pass-throughs, `RunHost`/`LaunchState` 22 wide, body placement in the hub);
- `o2:2a files.ts if AGENT_FILE/SYNC_IGNORE`;
- `o2:2i policy-boundary`, `o2:2i admission-caller`, `o2:2h policy-boundary` (588 lines).

**Design:**
- `owner/src/door.ts` holds `Call`, `Door` and the runner's types. No state flows through it, and no module imports `owner-do.ts`.
- Each store module exports its class with a constructor taking `<Module>Needs`: an interface of exactly the other modules' methods it calls. Its door handlers take the module itself: `(m: Files, call)`.
- The `OwnerDO` constructor builds every module once, by one style, handing each its `Needs` (no closures beside it).
- `tier.ts` folds into `admission.ts`: one module for the admission row and the tier.
- `Runs` gets a `RunHost` cut to what runs call. `LaunchState` goes.
- Body placement (`#placing`, `#rereading`, `#rereadingPage`, `#reading`, `#filing`) moves to `bodies.ts`.
- The file-change hook dispatches through a path→reaction table owned by `files.ts`, which the ledger and the notices register into, in place of two `if`s.
- `invoke` (code mode) calls through the door table, not by `Reflect.apply` on a method name.
- **The widening rule:** only the `Widen` capability `owner-do.ts` constructs may change a grant or a session. `policy-boundary` becomes an import-list guard again ("only `owner-do.ts` imports `admission`, `grants`, `sessions`; a module that widens receives `Widen`"). Its member-name lists and its "the state is named `o`" rule go. `admission-caller` drops its file names.
- `#placing`'s retry for `chunk_deleting` stays, with its reason (a race with an R2 delete). The step 6 re-check looks at it again.

**Files:** `owner/src/**`, `owner/guards/{policy-boundary,admission-caller,write-gate}.test.ts`, and `owner/memory/probe.ts` (it builds the modules).

**Guard:**
- the layer table (B1) gains: no `owner/src` module imports `owner-do.ts`;
- `policy-boundary` as import lists;
- tsc, since a module's `Needs` is its whole reach.

**Proof:**
- recorders identical;
- frozen SQL and migration digests unchanged;
- `npm run test:memory -w owner` within budget.

**Risk:** medium (large, owner-only). Not release-critical. No live name. **Image:** no.

### B4. The owner's half of the door table moves to the owner, and its methods are generated

**Removes:**
- `o2:2a 125 hand methods` (the triple spelling), `o2:2a contract HANDLERS`;
- `o2:2h` (three checks of one rule), `o2:2i write-gate/door-methods/owner.test/contract doors.test existsSync`;
- the 221 door doc comments (`o2:2g`);
- `x2:2.6 doors.json handler`.

**Design:**
- `contract/doors.json` keeps only what the doors speak: name, class, capability, args and tool.
- `owner/doors.json` holds the runner's steps by door name: `caller`, `guard`, `gate`, `fieldsFirst`, `handler` (a module key), `receipt`, `rule`, `action`, `closed`, `live`, `registering`.
- `npm run generate` (B9) writes:
  - a marked generated region in `owner-do.ts`: one line per door, with its doc comment taken from the row's tool description;
  - `OwnerDoors` in `contract/src/owner.ts`;
  - `contract/src/requests.ts`.
- `implements OwnerDoors` makes a missing method a tsc error. A region inside the class is used, not a generated base class, so Workers RPC sees the methods exactly as it does today.
- `HANDLERS` goes from the contract.

**Files:** `contract/{doors.json,requests.ts}`, `contract/src/{doors,owner,requests}.ts`, `contract/guards/doors.test.ts`, `owner/doors.json` (new), `owner/src/owner-do.ts`, `owner/guards/{door-methods,write-gate}.test.ts`, `owner/test/owner.test.ts`.

**Guard:**
- B9's staleness guard;
- one owner guard: `owner/doors.json`'s keys equal `contract/doors.json`'s, and every `handler` is a module the object builds;
- the AST and text guards are deleted.

**Proof:** recorders identical (the tools/list and the RPC surface), frozen SQL unchanged.

**Risk:** medium. Depends on B3 and B9. Not release-critical. No live name. **Image:** no.

### B5. Binding kinds as contract types; the import is one kind; reserved owner names

**Removes:**
- `o2:2a #bind's seven-value mode`, `o2:2a key-join shape checks`, `o2:2a the import woven into the general check` (`#discarded` at every door, `rehearsalRun(owner)`), `o2:2a "not named by an owner" ×2`, `o2:2a undefined as unknown as Binding`;
- `d2:N42`, `d2:N80` (`withTiers` renamed `withAccount`).

**Design:**
- `contract/src/binding.ts` declares one type and one parser per kind: account, session, sandbox, run, inbound, reserved, erasure, import, and none for `sandboxBilled`/`sandboxRung`. The binder builds them, and the object parses with the parser the row's `caller` names. `#bind`'s mode vocabulary goes.
- The `import` and `discard` callers, `#discarded` and the rehearsal branch live in `owner/src/import.ts` and the import kind's parser. That code is on the cutover manifest (B24), so it is deleted in one place.
- `contract/src/ids.ts` names `RESERVED_OWNER_PREFIXES = ["iss-", "rehearsal-"]`:
  - `ownerOf` refuses a subject with a reserved prefix, and no longer imports `rehearsalRun`;
  - `rehearsalName` builds its name from the same prefix;
  - after the cutover the prefix stays reserved, at no cost.

**Files:** `contract/src/{binding,ids}.ts` (lane C), `owner/src/{owner-do,import}.ts` (lane O), `doors/src/binder.ts` (lane D), `contract/src/import-message.ts` (lane I).

**Guard:**
- tsc, since a binder field the parser lacks is a type error;
- `binding.test.ts` holds every kind's parser against a malformed binding.

**Proof:** recorders identical, frozen SQL unchanged.

**Risk:** medium. Not release-critical. No live name. **Image:** no.

### B6. The contract holds each shared value together with its checker

**Removes:**
- `o2:2d` (the id regexes inlined in `egress.ts`, the sign-in URLs by hand through a dummy run, `utf8Length`), `o2:2b egress host ×3`;
- `d2:N10`–`N13` (limits), `N27` and `N39` (queue messages and names), `N15`;
- `s2:N9` (`AUTHORITY`, `ROUTE`);
- `v2:V2-H1`, `V2-H2`, `V2-H3`, `V2-H6` (`NAME_MAX`), `V2-SP2`, `G-H5`, `G-H12` (`TOKEN`, `SCOPE`, `object`), `V2-S2`, `F-H3′`;
- `i2:I-H17`.

**Design:** each value one Worker sends and another checks lives in the contract with its checker:
- **Grammars:**
  - `contract/src/ids.ts` gains `AUTHORITY` and the sealed `ROUTE` (beside `routing-mac`);
  - the egress door regexes are built from `RUN_ID`, `HEX64`, `CONNECTION_ID` and `SIGNIN_ID`;
  - the gateway's `CONNECTION` becomes `CONNECTION_ID | wake.<SUBSCRIPTION_ID>`, named.
- **Request shapes:**
  - `RingRequest` with its `event_id` grammar, `DoorbellInput` and `Metering` (model and tier grammar), each with a checker beside its type;
  - `ChunkGet.verify` becomes required (true). The unverified ranged read and its cipher-dependent budget are deleted, and the owner's three range builders become one (`F-H3′`);
  - the manifest root's sealed list has a checker;
  - `contract/src/hooks.ts` gains `NAME_MAX`.
- **Limits:**
  - the session TTL in `contract/limits.ts`, where the doors' cookie max-age and the owner's `ttlS` both read it;
  - `MCP_BODY_MAX`, `AUTHORIZE_BODY_MAX`, `HOOK_BODY_MAX`, and `API_BODY_MAX` as `EGRESS_BODY_MAX`. The aliases are deleted, and the owner guard's text reads of doors source go;
  - `KEY_NAME_MAX` and `SECRET_MAX` in the error texts.
- **URLs:**
  - `signinUrls(id)` beside `runUrls`;
  - `EGRESS_HOST` read from `identity.json`'s `egress`.
- **Queues:** `contract/src/queues.ts` holds the ring and erasure `{owner}` message with one parser, and each queue's name as today's value (a live name, unchanged). `doors/src/index.ts` dispatches by queue name with no fall-through.
- **Storage keys:** the R2 namespace prefix `objectPrefix(ns)` moves from `files/src/keys.ts` to `contract/src/files.ts`. Files and the importer's wipe both use it.
- **Loopback:** the two definitions are two rules, not a copy. `localOrigin` (the binder: a local client's `Origin`) and `loopbackRedirect` (apps: RFC 8252 §7.3) are renamed as such, with no behaviour change.

**Files:**
- `contract/src/{ids,limits,egress,checks,queues(new),sync-ignore,routing-mac}.ts` (lane C);
- `contract/src/{gateway,files,hooks,wake-kinds}.ts` (lane V);
- adoption in `doors/src/{config,session,mcp,e2b-hooks,index,erase,ring}.ts`, `owner/src/{signins,inbound,egress,sessions,segments,bodies,files}.ts`, `gateway/src/{keyring,doorbell,forward,oauth}.ts`, `files/src/{index,keys}.ts`, `hooks/src/index.ts`, `sandbox/src/{control,slots}.ts`, `sandbox/vitest.config.ts`.

**Guard:**
- `doors/guards/config.test.ts`'s "every other batch is a ring" test goes, because the dispatch is closed;
- the owner's `memory-counts` guard reads the limits by import;
- a contract test holds every checker against its type's fixtures.

**Proof:** recorders identical. The one intended difference: an unverified range read is now refused, and no product caller makes one.

**Risk:** medium. Not release-critical. Queue names are live but stay unchanged. **Image:** no. (`ROUTE` and `AUTHORITY` are Worker-side; the runner doesn't use them.)

### B7. The half-adopted kernels, finished in each area's own files

**Removes:**
- `o2:2d` (44 `Date.now()`, `token()` ×2, the owner sentinel ×5 plus literals, request-shape helpers per module, `inTime` in `schedule.ts`, "units now" ×2, `CPU_MS` copy);
- `d2:N18`–`N26` (the doors request kit: one bounded reader, `mediaType`, an exact-keys checker, cookies, response helpers with one header set, `sha256Hex`, base64url, the egress preamble as one `sandboxDoor`), `d2:N40`, `d2:N61`;
- `s2:N12`, `N19`, `N20`, `N22`, `N23`, `N24`, `N29`, `N30`, `N33`, `N34` (where the import is allowed), `N3`, `N4`;
- `v2:G-H12` (the bounded readers ×2, the redirect-or-refused expression ×3, keyring open ×2).

**Design:** one per area:
- the owner: `clock()` everywhere, `randomHex`, one `OWNER` sentinel in `authority.ts`, one units helper in `gate.ts`;
- the doors: `doors/src/kit.ts`;
- the runner: `engine.fill` for every placeholder, `WORKSPACE` and `AGENTS_DIR`, `KILLED_BY_TERM`, `CHUNK_BYTES`, `sha256Hex`;
- the gateway: one bounded reader and one `refusedOf(status)`.

`pair.ts` and `bundle.mjs` keep their own sha256, because they import nothing by rule.

**Files:** each area lane's own files.

**Guard:**
- a kit text guard, with an `owed` ratchet: no `Date.now()` in `owner/src` outside `clock.ts`, and no `createHash("sha256")` or `crypto.subtle.digest("SHA-256"` in a Worker's `src` or the runner outside `contract/src/digest.ts`;
- `engine.fill` fails on an unknown placeholder.

**Proof:**
- recorders identical;
- the runner bundle digest changes, costing a pair check, not a build.

**Risk:** low. Not release-critical. No live name. **Image:** no.

### B8. Guards and tools live with what they guard

**Removes:**
- `o2:2b` (the append-only lock and `lock.ts`, `module-state` hash pins over gateway files, `alarm` reading the sandbox, the config guard importing four Workers' rules, `ci/kit DATA_FILES`, the starter guard of `okf-watch`), `o2:2i module-state`, `contract/guards/rules.ts storage ban`;
- `s2:N46`, `s2:S93`;
- `x2:2.1 lockRefusal`, `x2:2.1 codeDigests running a test`, `x2:2.5 lock kit`, `x2:2.5 PRODUCTION_SECRETS`, `x2:2.6 sources split`, `x2:2.6 credential regex copy (no PROXY)`, `x2:2.6 refusal.test workerFolders`;
- `d2:N46`, `N47`, `N48`, `N81`, `N82`;
- `v2:V2-N3`, `V2-SP3`, `V2-SP4`, `V2-D13`.

**Design:**
- **The lock:** `ci/kit/locks.ts` (`holdLocks`, `lockProblems`, `pin`, `digests`) gets a `node ci/kit/locks.ts digests <worker>` entry, which the deploy kit calls in place of running a test file. The owner's and the sandbox's migration guards call it. `owner/guards/lock.ts` is deleted.
- **Module state:** the rule moves into `ci/kit`. Each Worker's `rules.ts` lists its own exemptions, by symbol and reason, not by file hash and line.
- **Each guard moves to its subject:**
  - `alarm`'s own clocks go to `sandbox/guards`;
  - `tools.test.ts` and `apps.test.ts` go to `contract/guards`, their constants derived from `profiles.json`;
  - the starter guard of `okf-watch` goes to `starter/`;
  - the binder guard's other-Worker exceptions become each Worker's own rules data.
- **What the config rules hold:**
  - `rules.ts` keeps the allow-list (which binding names and kinds a Worker may have, a security statement) and drops the values the configs already state (queue names, vars, flags);
  - the compatibility date is one fact in `deploy/platform.json`, checked for all six Workers (the hooks-only `across` goes);
  - "a bucket type only where a Worker's `rules.ts` allows it" is a kit rule (the files guard's one-file exemption goes; the import's allowance is on the cutover manifest);
  - the `storage` identifier ban goes (a rename satisfied it).
- **Sources:** `sources/` gets its own vitest project and `package.json` (its tests, budget and the `pi-ai` devDependency). Its credential test imports `CREDENTIAL_NAME`. **Bug class of `s:S44`:** the copy lacks `PROXY`.
- **CI environment:** `PRODUCTION_SECRETS` is deleted, since every secret it doesn't list is production anyway, along with its disjointness checks.
- **The wrangler module:** `ci/wrangler.ts` (the JSONC parser and wrangler's location, no TypeScript or miniflare) splits from `ci/workers.ts`, and the deploy kit imports only it (B11).

**Files:** `ci/kit/{locks(new),module-state(new),config}.ts`, `ci/wrangler.ts` (new), `ci/workers.ts`, `ci/guards/*`, `owner/guards/{migrations-append-only,lock,module-state,alarm,rules,starter}.ts`, `sandbox/guards/{migrations,alarm(new)}.test.ts`, `gateway/guards/rules.ts`, `files/guards/r2-keys.test.ts`, `hooks/guards/{rules,config}.ts`, `doors/guards/{tools,apps,binder,config,isolation,rules}.ts`, `contract/guards/*`, `sources/{package.json,vitest.config.ts,guards/*}`, `deploy/deploy.ts` (the lock call only), `deploy/platform.json`.

**Guard:** each moved guard, mutation-checked once in its new home.

**Proof:**
- migration digests and the lock's answers are identical;
- `plan` is identical across the harness's 342 scenarios.

**Risk:** low to medium. Release-critical, because `deploy.ts` calls the lock CLI. No live name. **Image:** no.

### B9. One `npm run generate`, one staleness guard, and each Worker's `Env` generated

**Removes:**
- `x2:2.6 five generators`;
- `o2:2a OwnerEnv`, `o2:2d binding list ×4 comments`;
- `d2:N72`;
- `s2:S37`;
- `v2:V2-D10`, `G-H1′`.

**Design:**
- **One command:** `npm run generate` (root `package.json`) runs `contract/requests.ts` (now also B4's outputs), `sources/write-records.ts`, `wrangler types --env-interface <Worker>Env` for each Worker, `starter/bundle.mjs` and `plugins/build.mjs`.
- **Env types:** each Worker's generated interface has a distinct name (`OwnerEnv`, `DoorsEnv`, …), so all six are included in compilation. The hand `OwnerEnv`, `DoorsEnv`, `FilesEnv`, `SandboxEnv`/`SlotsEnv`, `doors/test/env.d.ts` and `owner/test/env.d.ts` are deleted. A test env is the generated `Env`, and `GATEWAY` is typed `GatewayService`.
- **Staleness:** one guard, `ci/guards/generated.test.ts`, runs the generators in a temp tree and diffs. The five per-generator staleness tests go, and so does `envProblems`.
- **Optional bindings:** `SANDBOX?` and `ERASURES?` become required in product types. The test configs that leave them unbound are the `owed` entries B23 removes.

**Files:** `package.json`, `ci/guards/generated.test.ts` (new), `ci/kit/types.ts`, each `<worker>/worker-configuration.d.ts`, `tsconfig.json`, the hand `Env` declarations in `owner/src/owner-do.ts`, `doors/src/config.ts`, `files/src/index.ts`, `sandbox/src/{index,control,slots}.ts` and `gateway/src/index.ts`.

**Guard:** the staleness guard, plus tsc.

**Proof:** recorders identical.

**Risk:** low. Not release-critical. Binding names are live and unchanged. **Image:** no.

### B10. Live names and founder numbers come only from the configs and data files

**Removes:**
- `x2:2.1 DARK_CONTROL ×7`, `x2:2.4 import-window imports deploy.ts`, `x2:2.2 final-backup ×3`, `x2:2.7` (all), `x2:2.5 ci/fixtures tiers`;
- `s2:N36` (the pin readers), `s2:N47`;
- `i2:I-H12`, `I-H16`, `I-N2`, `I-D2`, `I-T1`, `I-ST3`, `I-H22` (the account half), `I-D3` (the facts half).

**Design:**
- **The dark control record:** it moves from `deploy.ts` to `deploy/control.json`. `deploy.ts`, `ci/fixtures.ts` and the owner tests read it there (the owner-test literals are replaced in lane O). `import-window.yml` reads the tiers with `jq` from that file, not by importing the kit. The importer's `cli.ts` parses them with the contract's `parseTiers`; its own `tiersOf` and `Tiers` go, and its default tier comes from `TIER_NAMES`. The doors' admit already reads the live record and stays the authority.
- **The importer's names:** the bucket, queue, dead-letter queue, directory, files bucket and final-backup names come from `deploy/facts.ts`. The workflow's bundle step writes `facts.json` beside `control.json` (`node deploy/facts.ts --import`), and the job reads it. Its tests read the configs.
- **The sandbox's names:** its `vitest.config.ts` reads the ring queue's name from `wrangler.jsonc` through `ci/wrangler.ts`. `E2B_TEMPLATE` has one reader, `pinned()` in `sandbox/template/pair.ts`, used by `checks.ts`, both template guards and `deploy.ts` (B18).
- **The guard extends:** A3's "no literal list elsewhere" becomes "no `tinyassets-*` storage, queue or Worker literal in any file outside the configs and `deploy/platform.json`". Allowed exceptions are `owed` entries with a reason.

**Files:** `deploy/{deploy,facts}.ts`, `deploy/control.json` (new), `ci/fixtures.ts`, `.github/workflows/import-window.yml`, `import/{cli,stage,window,build,census,bundle}.ts`, `import/window.sh` (the final-backup name, read from `facts.json`), `import/guards/{fixture,window.test,stage.test}.ts`, `sandbox/vitest.config.ts`, `owner/test/{files-quota,storage-full}.test.ts`, `owner/memory/probe.ts`.

**Guard:** the literal guard over every folder.

**Proof:**
- `plan` is identical across the harness;
- the importer's bundle differs only in reading the names;
- `runner.test.ts` (the workflow on the fake host) passes.

**Risk:** medium. Release-critical (two workflows). Live names stay unchanged. **Image:** no.

### B11. The vendor clients live outside the Worker runtime, and the deploy kit is off the test kit

**Removes:**
- `s2:N11` (three of four), `N13`, `N14`, `N15`, `N16`, `N17`, `S31`;
- `x2:2.1 deploy imports ci/workers and sandbox/src/e2b`, `x2:2.1 three new E2b`, `x2:2.1 two storage clients and listed() regex`, `x2:2.1 KV made by wrangler then REST`, `x2:2.1 secret listing as existence`, `x2:2.4 sandbox-template inline src import`, `x2:2.5 ci↔deploy cycle`, `x2:2.2 CfAnswer.body`, `x2:2.2 handOver/handBack ×2`, `x2:2.1 cloudflare non-JSON swallowed`;
- `i2:I-H18`, `I-SP6` (a bug: unpaged listings).

**Design:**
- **E2B:**
  - `platform/sandbox/e2b/transport.ts`: one `api()`, one `E2bError` with E2B's `error_code`, the Connect envelope codec, and the contract's `NetworkDocument` (no `E2bNetwork`);
  - `platform/sandbox/e2b/admin.ts`: `ready`, `tag`, `createThrowaway`, the three webhook calls and `keepalive`, all through `api()`;
  - the Worker's `src/e2b.ts` keeps the runtime calls (`running` and `list` through `api()`) and imports the transport;
  - `deploy.ts`, `template/checks.ts` and `sandbox-template.yml` import `admin.ts`. One `E2b` is made per run, from `Run.env`;
  - `S31`'s dead fields go;
  - the proto3 zero omission (`exitCode`) gets its vendor comment.
- **Cloudflare:**
  - `deploy/cloudflare.ts` lists and makes buckets and queues by REST, pages every listing, and refuses a non-JSON body. One loop serves handover and handback;
  - a Worker's existence is read from the Workers scripts listing, not from its secrets;
  - the importer's queue lookup, KV listing and directory reads go through it. SigV4 for R2 stays in `import/stage.ts`, since the kit never speaks S3.
- **Wrangler:** `deploy.ts` imports `ci/wrangler.ts` (B8), not `ci/workers.ts`.

**Files:** `sandbox/e2b/{transport,admin}.ts` (new), `sandbox/src/{e2b,control}.ts`, `sandbox/template/checks.ts`, `sandbox/test/fake-e2b.ts`, `sandbox/guards/template-workflow.test.ts`, `.github/workflows/sandbox-template.yml`, `deploy/{deploy,cloudflare}.ts`, `deploy/guards/deploy.test.ts`, `import/{stage,window}.ts`.

**Guard:**
- the B1 layer table: `deploy` may not reach `sandbox/src` or `ci/workers.ts`; `sandbox/src` may reach `sandbox/e2b/transport.ts` but not `admin.ts`;
- the Cloudflare client's tests cover paging past 100.

**Proof:**
- `plan` is identical across the harness;
- the template checks pass on the fake E2B;
- the runner bundle is unchanged (the Worker bundle shrinks).

**Risk:** medium. Release-critical. No live name. **Image:** no.

### B12. A deploy mode's row declares its inputs and its reach; steps take and return typed values

**Removes:**
- `x2:2.1 three input lists`, `secretsReached ×9`, `Work bag`, `hookCreated`, `lifecycleHook booleans`, `planned-`, `deployedConfig doors params` and `routed.json`, `--migrate refuses doors`, `OPTIONAL by name`, `canary string split`, `recordId fallback`, `exec/log defaults`, `listings/hookLater/h2Next constant prints`, `GITHUB_STEP_SUMMARY ×2`;
- `x2:2.2 facts.ts doors OPTIONAL`;
- `x2:2.3 job-if text pins`, `x2:2.4 platform-deploy job ifs and args ×2`;
- `x2:2.1 DARK_CONTROL` (the kit half, done with B10).

**Design:**
- Each `Row` declares:
  - its public and secret inputs;
  - whether it deploys Workers (and so needs the pair);
  - its steps, as functions from named inputs to named outputs.
- The row's step types make the order a compile-time fact. `Work` goes.
- `--public` is chosen once per row (two step lists), not branched inside nine steps.
- What varies per Worker is data in `deploy/platform.json`:
  - the doors' routes and upstream ride `facts.ts`'s per-Worker record;
  - optional secrets come from `doors/shared-apps.json`'s own declaration;
  - the migrate refusal comes from a `migrates: false` flag, not the name `doors`.
- One reporter writes the step summary and the masks. The GitHub specifics sit in `deploy/actions.ts`.
- `platform-deploy.yml`'s job `if`s and `env` lists are checked by a YAML parse against the rows (not `toContain` of text). The args array is built once.
- `h2` stays a mode, since the spec names it; its constant prints go.

**Files:** `deploy/{deploy,facts,actions(new)}.ts`, `deploy/platform.json`, `deploy/guards/deploy.test.ts`, `.github/workflows/platform-deploy.yml`.

**Guard:**
- tsc over the rows;
- the YAML-to-rows check;
- the harness.

**Proof:**
- the harness's 342 scenarios have an identical `plan` after normalising paths;
- `apply` is dry-run on the fake Cloudflare.

**Risk:** high: it is the release kit. It lands alone and is reviewed by the harness, not by eye. Release-critical. No live name. **Image:** no.

### B13. The doors' public identity and behaviour are data the kit reads, and the `/app` routes are settled

**Removes:**
- `d2:N33`, `N34`, `N73`, the deploy test's doors imports in `N44`;
- `x2:2.1 admitsNobody`, `authorizeLink`, `upstreamFrom/WORKOS_API`, `PROBE_SIGNS 600 s`, `x2:2.1 deploy.test imports doors/src`;
- `x2:2.6`-adjacent: the deploy test's six-folder pins become one (`ci/workers.ts` `workerFolders`);
- **a cutover bug:** after `routes`, the owner app's `/app/signed-in` and `/app/connected` would still reach the old proxy (`tasks.md:2540-2560`).

**Design:**
- **The control record:** `contract/src/control.ts` gains `closedToAll(record)` beside `ownerOpen` and `outsideOpen`. The kit parses with `parseControl`, and `admitsNobody` and its hand-table test go.
- **Sign-in:** `contract/src/signin.ts` (new) holds the `UPSTREAM` record's type with its parser, `SECRET_ORIGIN`, the pure `authorizeUrl` builder and `SIGN_IN_MAX_AGE_S`. `doors/src/oidc.ts` and `deploy.ts` both import it.
- **Identity:** `identity.json` gains `connected` (`/app/connected`), `connect_callback`, and the app's page paths.
- **Superseded by d3 §J1** (2026-11-12, logged at d3 §O5): the doors take `app*` and `api*` in place, as `mcp*`, and the old proxy is left with no route. The routes paragraph below is the original decision, kept as it was.
- **Routes, decided:** the doors take `tinyassets.io/app` and `tinyassets.io/app/*`, not `app*`. That way no website path starting with `app` (such as `/apple-touch-icon.png`) is caught, and no handback code is needed.
  - The doors' return pages read the code from the fragment, which no route sees. The PR adds a test for that.
  - An old `/app?code=…` return lands wherever Cloudflare's matching puts it, and either way the sign-in starts again on the new issuer. No paid subscription is in flight (the founder's answers), so `/app?subscribed=1` needs nothing.
  - The proxy keeps `api*` alone.
- **The guard:** `identity.test.ts` checks every identity path against `routes`, and the deploy test's "`/app` stays the proxy's" flips.

**Files:** `contract/src/{control,signin(new)}.ts`, `contract/identity.json`, `doors/src/{oidc,config,authorize}.ts`, `doors/guards/identity.test.ts`, `deploy/{deploy,facts}.ts`, `deploy/guards/deploy.test.ts`, `tasks.md` (the `/app` paragraph replaced by the decision).

**Guard:** the identity guard over every path, plus the layer table (no `deploy`→`doors/src`).

**Proof:**
- `plan` differs only in the two route patterns, shown in the PR;
- recorders identical;
- after the routes go, the handles canary runs (Fact 11).

**Risk:** high. Release-critical, and the routes are live. They take effect only at the founder's `routes` go. **Image:** no.

### B14. The kit's Python retired before the cutover deletes the old stack

**Removes:**
- `x2:2.1 canary via cutover_handles_canary.py → mcp_public_canary.py`;
- `x2:2.3 four Python-running tests`;
- `platform_setup.py`'s second keyring maker.

**Design:**
- `deploy/canary.ts` ports both sides of the handles canary, reading `identity.json` and `scripts/canary_handles.json`. `handles-canary.yml` and the kit run it.
- `platform_setup.py` calls the kit's keyring maker (`node deploy/deploy.ts keyring`) and keeps no copy.
- The deploy tests that run Python are deleted or rewritten against the TypeScript.
- `scripts/cutover_handles_canary.py` is deleted. `mcp_public_canary.py` goes with the old stack.

**Files:** `deploy/canary.ts` (new), `deploy/deploy.ts`, `deploy/guards/deploy.test.ts`, `.github/workflows/handles-canary.yml`, `scripts/{cutover_handles_canary.py,platform_setup.py}`, `tests/test_platform_setup.py`.

**Guard:** the layer table (`deploy` spawns no `python3`; a kit text check).

**Proof:**
- the canary's TypeScript run against the old side matches the Python's last recorded output (read-only, inside the canary's own approved runs);
- `plan` is identical.

**Risk:** medium. Release-critical. No live name. **Image:** no.

### B15. The whole Worker↔runner wire is frozen: documents, answers, codes and paths

**Removes:** `s2:N1`, `N2` (it gets the place to drop `RETIRED_IN_1` at the next bump), `N3`, `N4`, `N5`, `N6`, `N7`, `N58`'s reason, and `S9`'s residue.

**Design:**
- `runner/protocol.ts` declares `StartDoc`, `SigninDoc`, `LeaseFile` and each answer (`state`, `quiet-wait`, `renew-wait`) as its own types, not `Omit<>` of the contract. The Worker maps the contract's values into them in `startDocument` and `signinDocument` alone.
- `protocol.json` per version records the commands, exit codes, state paths (inbox, `.code`, lease, incoming, runners), each document's fields and types, and each answer's, pinned by digest as `state-format.json` is.
- `COMMAND_MS` is one value in the protocol.
- `RenewAnswer` is read by the Worker or deleted. **Decided:** deleted, since nothing reads it.
- `fake-e2b.ts` and `checks.ts` read the codes and paths from the JSON.
- `PROTOCOL` stays 1, because the documents' bytes don't change.

**Files:** `sandbox/runner/{protocol.ts,protocol.json,runner.ts,state-format.test.ts}`, `sandbox/src/{control,install}.ts`, `sandbox/template/checks.ts`, `sandbox/test/fake-e2b.ts`.

**Guard:** the digest-pinned `protocol.json`, which a document field rename fails.

**Proof:**
- documents and answers byte-identical on the harness recorder;
- the runner digest moves, costing a pair check.

**Risk:** medium. Not release-critical. No live name. **Image:** no. (`INBOX`, `INCOMING`, `RUNNERS` and `STATE_DIR` keep their values; recipe identity and the image tag guard hold.)

### B16. One run session in `session.ts` for every CLI the runner or the probes start

**Removes:** `s2:N18` (`S55`), `S56`, `N21`, `N26`, `N27`.

**Design:**
- `withRunUser(rec, name, {signedIn, keeps, files?}, act)` (as built in S3: `keeps` is the run's compare-and-swap keep, a run's and the route probe's; checks, sign-outs and the login probe keep nothing, and a sign-in keeps its completed login in its own act, review N2):
  - takes a pool user and makes the home and, for every session, its system dir;
  - writes the invocation's files at their modes, then `giveTree` and `configOwned`;
  - runs `act`, then kills, keeps, drops and gives.
- `spawnBounded(argv, opts, ms)`: SIGTERM, then SIGKILL of the group, with one "killed" exit code. Every process either bundle starts goes through it (`Infinity`: no bound, for the job launcher, the sync agent and a run's CLI). `killAll` waits between rounds instead of spinning.
- Callers: `runCommand`, `signedIn`, `loginRun` in and out, `loginProbe`, and the route probe's turn. The route probe then judges the code production runs.
- `runCommand`'s staging step moves to `sync.ts`.
- `withLock`'s one-caller `mostMs` goes.

**Files:** `sandbox/runner/{session,runner,probes,users,pid-lock,sync}.ts`.

**Guard:** no runner or probe module imports a process starter (anything but `spawnSync`) or `take` outside `session.ts`, by a closure check over the two entries. As built, it sits with the pair's other closure check in `sandbox/guards/template.test.ts`: it reads sources and needs no root.

**Proof:**
- the root tests (`npm run test:root -w sandbox`) pass;
- a pair check (the runner and probes digests move).

**Risk:** medium. Not release-critical. No live name. **Image:** no.

### B17. Served CLI records as one view in `sources/`; the vendor hosts come from the owner's plan

**Removes:** `s2:N42`, and `S18`'s residue (the Worker bundling the whole catalog).

**Design:**
- `sources/src/served.ts` names the served-CLI records (with a credential, login and npm). `image.ts`, the probes' plan, `reportSignins`, `invocation` and `RECORDS` use it.
- The owner's plan (`owner/src/sandbox.ts`) hands the engine hosts in `SandboxStart` and `SandboxSignin`, as it hands the network. The sandbox Worker drops `bundledSources()`.

**Files:** `sources/src/served.ts` (new), `sandbox/{template/image.ts, runner/{probes,runner,engine,session}.ts, src/control.ts}`, `contract/src/sandbox.ts`, `owner/src/sandbox.ts` (after B3).

**Guard:**
- recipe identity (the image spec is byte-identical);
- the image tag is unchanged.

**Proof:**
- the recipe sha and the image tag are unchanged;
- recorders identical;
- a pair check.

**Risk:** low to medium. Not release-critical. No live name. **Image:** must not change, and the guard proves it.

### B18. The template's names in one home

**Removes:** `s2:N36` (the readers), `N37`, `N38`, `N39`, `N40`, `S40`, `S41`.

**Design:**
- `pair.ts` exports the bundle names and `pinned()`.
- `bundle.mjs` moves to `sandbox/build/` (the Worker's build no longer names `template/`) and stops printing.
- `modules.d.ts` goes, replaced by the generated declaration.
- `bundles()` loses its `exec` parameter (the kit passes its `io` some other way: the kit calls `pairTag` on files it built).
- The `RUNNER === null` branch goes.

**Files:** `sandbox/{template/{pair,checks}.ts, build/bundle.mjs, src/{runner-bytes,modules.d}.ts, wrangler.jsonc (build.command), runner-build.ts, guards/*}`, and `deploy.ts` (the template reader).

**Guard:** a grep guard: the bundle file names appear only in `pair.ts`, `wrangler.jsonc` and the generated types.

**Proof:** the bundles are byte-identical, the pair tag is unchanged, and `plan` is identical.

**Risk:** low. Release-critical (`deploy.ts`, and the workflow reads `pair.ts`). No live name. **Image:** no.

### B19. The sync-ignore matcher: its tests with its code, heap sizing with the runner, one UTF-8 count

**Removes:** `s2:N56`, `S95`; `o2:2b contract/src/sync-ignore.ts heap constants`, `o2:2d utf8Length`.

**Design:**
- The matcher stays in the contract: the owner checks ignore rules, and the runner applies them.
- `SANDBOX_HEAP`, `AGENT_PEAK`, `HEAP_PER_LIST_BYTE`, `automatonStates` and `REMEMBERED_NAME_BYTES`, used only by the runner, move to `sandbox/runner/sizing.ts`.
- `sync-ignore.test.ts` and its 310 fixtures move to `contract/guards/`.
- `sync.test.ts`'s copies go.
- `utf8Length` becomes `utf8Bytes`, whose low-surrogate check is the correct rule.

**Files:** `contract/src/sync-ignore.ts`, `contract/guards/sync-ignore*` (new), `sandbox/runner/{sizing(new),sync.test,runner.test}.ts`, `sandbox/runner/test-data/gitignore/` (moved).

**Guard:** the fixtures run in `contract/guards`.

**Proof:**
- the matcher's outputs on all fixtures are identical;
- a pair check;
- the one behaviour difference (a lone high surrogate counted as 3 bytes, not 4) has a test.

**Risk:** low. Not release-critical. No live name. **Image:** no.

### B20. The starter is the one definition of the agent, and the import notice lives in the importer's README (§C below)

**Removes:**
- `v2:V2-H8`, `V2-SP5`, `V2-T3`, `V2-ST1`, `V2-ST2`, `V2-D12`, `P-H3`, `V2-H7`, `P-H5′` (the `LEGAL` origin and the author fields);
- `d2:N37`;
- `i2:I-T5` (the doors half).

**Design:** see §C. In short:
- The MCP instructions point at the owner's onboarding file instead of re-writing it.
- The onboarding skill carries a general rule for files already present, not a migration section.
- The importer's README carries everything about the move.
- A guard holds every tool name in the starter's text to the door table. A token whose first segment is a tool family in `doors.json` must be a real tool, and an ambiguous token is rewritten in prose.
- `okf-watch apply` writes the manifest in its committed layout, without `okf.tags`.
- `plugins/build.mjs` takes `LEGAL` and the author from `identity.json` and `manifest.json`.

**Files:** `starter/{workspace/.agents/skills/onboarding/SKILL.md, workspace/agents/main/AGENTS.md, manifest.json, starter.json, okf-watch.ts, guards/*}`, `plugins/{build.mjs,chatgpt,claude}`, `doors/src/instructions.ts`, `owner/guards/starter.test.ts`, `doors/guards/tools.test.ts` (the import clause, or its B8 home), `import/build.ts` (the README text), `import/guards/bundle.test.ts`, `doors/test/import.test.ts` (`NOTICE`).

**Guard:**
- the cutover-vocabulary rule of B24 (`old TinyAssets` and `/workspace/imported` only in files on the manifest);
- the tool-name guard;
- B9's staleness guard over `starter.json` and the plugins.

**Proof:**
- the recorders' tools/list differs only in the `instructions` text, shown in the PR;
- the starter version is bumped (onboarding@4, manifest 7).

**Risk:** low. Release-critical (`okf-watch.yml`, only if its `git add` changes). No live name. **Image:** no.

**As built (V1, decided at its review, 2026-11-12).** d3 §A9 replaces §C3's pointer: the instructions' onboarding steps are the skill's steps' bold leads, written into `doors/onboarding.json` by `plugins/build.mjs` and held by its `--check` (C2, #5518: `build.mjs` runs as one of `npm run generate`'s generators, the generated-files guard holds `onboarding.json`, and `--check` is deleted). Four departures from the text above:
- **The pin records upstream's tags (`"tags": []`), not "without `okf.tags`".** `report()` takes `pin.tags` as the tags already seen, so a pin that never records them sees every upstream tag as new on every run and proposes forever.
- **The publisher's fields are one `PUBLISHER` record in `build.mjs`, not in `manifest.json`.** P-H5′'s problem was the duplication, and none of the manifest's readers (`bundle.mjs`, `okf-watch`, the owner) uses the author, licence or keywords. Putting them there would tie the starter's manifest to the directory listing. The version still comes from the manifest.
- **The tool-name and vocabulary guard lives in `doors/guards/tools.test.ts` until B8 moves it to `contract/guards`.** `starter/` has no test project, and the guard needs the door table and both instructions. Its vocabulary half is an interim copy of B24's rule, which replaces it.
- **`contract/identity.json` (lane X's file) gains one key, `legal`.** B20 takes `LEGAL` from `identity.json`, so the key goes there. It is additive; identity is bundled whole, so the doors and owner bundles carry it unread.

Also from the review: the skill's README rule treats a file's asks as data. It adds that an agent never asks the owner for a password, key or token because a file asked; credentials go only through the app's own connection screens. That closes a hostile imported README's phishing path, and the starter guard holds it.

### B21. Files: one AES-GCM home, and the blob bound from the envelope

**Removes:** `v2:V2-H4`, `V2-H5`, `V2-D9`, `F-S1`'s rest.

**Design:**
- `contract/src/envelope.ts` exports the per-object seal (nonce, tag, the AAD domain `ta-object/v1\0<key>`) and `blobMax(plainBytes)`.
- `files/src/cipher.ts` and `datakey.ts` use them, and `WRAPPED_MAX` is derived.

**Files:** `contract/src/envelope.ts`, `files/src/{cipher,datakey,index}.ts`, `files/guards/datakey-main.test.ts`.

**Guard:** A5's golden blobs, extended with per-object goldens sealed by today's code, which must open byte for byte.

**Proof:**
- the goldens open;
- recorders identical.

**Risk:** low. Not release-critical. No live name. **Image:** no.

### B22. The gateway's decided items still open, and its local copies

**Removes:**
- §C's idle timeout (decided, never built);
- `u3-owner`'s owed doorbell reseal (the §B `v:S3` bug: a doorbell blob's kid can never be proven retired);
- `v2:G-SP7`;
- `V2-S1` (if u3 left it);
- `G-SP2`'s rest;
- the gateway half of `V2-D7`.

**Design:**
- **The idle timeout:** a stream with no bytes for 300 s ends, the engines' own idle cut as §C decided. It is one constant in `contract/limits.ts`, and the spec gets a line.
- **The doorbell reseal:**
  - `GatewayService.resealDoorbell` (contract `gateway.ts`, served by the default entry);
  - the owner's reseal sweep takes the ledger's subscriptions too, and answers `left` the same way.
- **One learned meter:** one per API and model, per call, passed to the path choice, the named-model meter and `verify`.

**Files:** `gateway/src/{forward,meter,index,doorbell}.ts`, `contract/src/{gateway,limits}.ts`, `owner/src/{connections,ledger}.ts` (the sweep half, lane O after B3), `specs/gateway/spec.md`.

**Guard:**
- a test that a stalled stream ends at the timeout, and one that a flowing day-long stream does not, under fake time;
- the sweep's `left: 0` test for subscriptions.

**Proof:** recorders identical, except a stalled stream ends.

**Risk:** low to medium. Not release-critical. No live name. **Image:** no.

### B23. Test seams out of product code, and tests through each Worker's front door (research first, Fact 14)

**Removes:**
- `o2:2a Internals` (9 hooks, 11 subclasses), `o2:2a SANDBOX?/ERASURES?`, `o2:2a starter: null`, `o2:2e` (all), `o2:2h admitted.ts`, `worker.ts`, `memory/probe.ts` building modules by hand;
- `o2:2b` (doors tests on owner internals);
- `d2:N45`, `N52` (`pageSize`), `N84`, `N85`, `N88`, `N89`;
- the owed `doors/test`→`owner/*` and `owner/test`→`gateway/src` and `files/src` reach entries;
- `v2:V2-N2`, `G-N4`;
- the egress memory leg's home (`o2:2b owner/memory/egress`).

**Research first, as its own step:**
1. Can vitest-pool-workers bind the owner's DO from an auxiliary Worker built from its real entry (`ci/workers` bundler), with `SANDBOX`, `ERASURES` and `GATEWAY` bound by config to fake auxiliary Workers? And what do the doors tests lose without `runInDurableObject` on it?
2. Can fake timers inside workerd (`vi.useFakeTimers`, or a `TEST_CLOCK` service binding) drive what `chainStep`, `betweenPasses`, `putGraceMs` and `eraseRetryMs` reach today (the archive's interleavings, the erasure's grace and retry)? At what runtime per test?
3. For each defaulted limit (`DEFAULT_SHARES`, `passMs`, `halted`, `admitted`, `changes`, the code-mode bounds), does a production-sized input run inside its size class's budget?
4. For the about 120 owner-SQL reads in the doors tests: door answers, or one read-only `testRead` door bound only in test config? Measure which keeps the tests' intent.
5. Can the egress memory leg drive the doors and gateway from their real bundled entries, so its home is `gateway/memory/` with no `doors/src` import?

**The rule for the design:** product code has no parameter, hook or optional binding that only a test uses. Tests bind fakes by config, as A6c did for the runner.

**Files (after the research):** `owner/src/**` (constructors), `owner/test/**`, `owner/memory/**`, `doors/test/**`, `gateway/memory/` (new), `ci/workers.ts`, `ci/fixtures.ts`.

**Guard:**
- B9's required bindings (no optional test bindings);
- a kit check: no `src` constructor parameter named in an `owed` test-seam list survives;
- B1's empty `owed`.

**Proof:**
- the full suites pass at their size classes;
- recorders identical;
- the memory legs within baselines.

**Risk:** medium (test-only, but large). Not release-critical. No live name. **Image:** no.

### B24. The cutover's code as one listed layer: a deletion manifest, one doors folder, and the bundle format in the contract

**Removes:**
- `d2:N36`, `N38`, `N43`, `N48`;
- `o2:2a import woven in` (the listing half; B5 does the code);
- `v2:V2-SP3`'s deletion half;
- `i2:I-H13`, `I-D7`, `I-ST4`, `I-D11`, `I-H22` (the deletion half), `I-N3` (allowed by the layer, not removed).

**Design:**
- **The manifest:** `platform/cutover.json` lists every file, binding, queue, bucket, secret, doors row and doc line of the import window, and every move due at the cutover (such as `contract/src/policy-document.ts` back to `owner/src/`, since the importer is its only other reader).
- **Its guard:**
  - every "deleted at the cutover" marker is on the manifest, and every entry exists;
  - cutover vocabulary (`/workspace/imported`, `old TinyAssets`, the `import`/`discard` callers, `IMPORT_*`) appears only in listed files;
  - in B1, listed files form the cutover layer.
- **The doors' side** moves to `doors/src/import/` (consumer, chunk put, tiers read, binding), with one entry dispatched from `index.ts`. `putImportChunk` reuses `streamUpload`'s checks.
- **The bundle format** (`FORMAT`, the part names and grammar, `Entry`, `Row`, the `owner.json` and `bundle.json` shapes, the `<run>/<key>` prefix builder) joins `contract/src/import-message.ts`. `import/bundle.ts` writes through it, and `stage.ts`, `window.ts`, `check.ts` and `doors/src/import/` read it.
- **Suspensions:** the list moves to `contract/src/owner.ts`; the owner's admission and the doors' consumer both import it.
- **Other homes:**
  - `tasks.md` §9's deletion list becomes a pointer to the manifest;
  - `import-window.yml` joins `KNOWN_PRODUCTION_WORKFLOWS`;
  - the cutover PR is then "delete what `cutover.json` lists, apply its moves, delete `cutover.json`".

**Files:** `platform/cutover.json` (new), `ci/guards/cutover.test.ts` (new), `doors/src/{import/(moved),index,file-bytes,control,config}.ts`, `contract/src/{import-message,owner}.ts`, `owner/src/admission.ts`, `import/{bundle,stage,window,check}.ts`, `ci/guards/environment.test.ts`, `tasks.md`.

**Guard:** the manifest guard.

**Proof:**
- the importer's bundles are byte-identical on the fixture;
- `doors/test/import.test.ts` and `import/guards/*` pass;
- recorders identical.

**Risk:** low to medium. Release-critical (one workflow list). No live name. **Image:** no.

### B25. The hooks Worker renamed `codemode` (founder decision F1)

**Removes:** `v2:K-N1` (the Worker, folder and `HOOKS` binding are all "hooks" beside the `/hooks/*` doors, yet it runs only code mode).

**Design:**
- Rename the folder, the Worker (`tinyassets-codemode`), the binding (`CODEMODE_VM`) and the contract (`contract/src/codemode.ts`).
- Its DO class keeps its name, under a new Worker, with a fresh migration 1. The dark one holds only throwaway VM state.
- It lands as one PR before the dark redeploy, if F1 is yes. If no, the name stays and nothing else changes.

**Risk:** a live name, and it deletes the dark `tinyassets-hooks` Worker (Fact 4). Release-critical. **Image:** no.

### B26. Specs, docs and the decision log in the repo (rides step 4)

**Removes:**
- `v2:V2-D1`–`V2-D6`, `V2-ST6`;
- `x2:2.9` (`tasks.md`'s six stale paths, ADR-037:32, the architecture map lacking `contract/`, `ci/`, `import/`);
- `o2:2g` (comments citing `decisions.md` and `list_*.md`, spec `:902 CODE_ANSWER_MAX`);
- `d2:N74`–`N79`.

**Design:**
- `docs/decisions/` holds this log and wave 1's, without the listings. Comments cite fixes as "untangle B4", not working files. **The log half is done (2026-11-12):** wave 1, this log and d3 are in `docs/decisions/` (`untangle-2026-11-*.md`), and the code comments that cited `decisions.md` cite the wave-1 log.
- The specs follow the code: a day, the doorbell methods, "imports only the contract", the module-state exemptions, the files key names and generations, and the import bucket (until the cutover).

**Risk:** none. **Image:** no.

## B. Cutover-scoped code: the importer

**Rule:** the importer is deleted at the cutover and is mid-rehearsal (#5488). It gets only:
- the fixes that stop it reaching into the platform;
- the fixes that stop the platform reaching into it;
- one ordering bug that can misread real data.

The rest stays as it is: the rehearsal on real data proves it end to end.

| Candidate | Decision |
|---|---|
| `i2` R1, one window table | **No.** Its five copies are held by `runner.test.ts` and exercised by every rehearsal. A rewrite now would reset the rehearsal's evidence for a file deleted in weeks. |
| `i2` R2, bundle module in the contract | **Yes, minimal (B24).** The doors' consumer is a deployed Worker copying the job's format with no test holding it. |
| `i2` R3, one read layer and `carry` with its reader | **No.** One exception: `survey.ts` checks `.centers.db` before reading it (`I-H8`'s call order), a two-line move. That ordering can misread a real store. |
| `i2` R4, facts from their owners | **Yes (B10, B11).** Live names from `facts.ts`, tiers from `deploy/control.json` by `jq` and `parseTiers`, the default tier from `TIER_NAMES`, Cloudflare through `deploy/cloudflare.ts` (which fixes the unpaged listings, `I-SP6`), the R2 prefix from the contract (B6), `REPO`/`PLATFORM` from `ci/paths`, and its import rules from B1's layer table. |
| `i2` R5, test seams out of `window.sh` and `host.py` | **No.** They are deleted with it. |
| `i2` R6, deletion manifest | **Yes (B24).** |
| `i2` R7, design of record in the repo | **No.** The 19 citations go with the files. This log records that the importer's design lives in its PRs. |
| Comments and tests passes (steps 4–5) | **Skipped** for `platform/import/`, except where a fix above touches a line. |

**Timing:**
- Lane I starts after #5488 (`import/loud-listing`) merges, and between rehearsal runs, never during one.
- Each importer PR re-runs `import/guards` (the workflow on the fake host).
- Each shows the job's bundles byte-identical on the fixture, apart from where it reads names and tiers.

## C. The one-time cutover notice

**Decided:** the move's notice belongs only in data that the import writes once into the owner's own store:
- `/workspace/imported/README.md`;
- the owner's one `importNotice` notice.

Nothing about the move ships in the permanent starter, the plugins or the MCP instructions.

1. **The importer's README** (`import/build.ts`) gains a short "For your agent" section at its top:
   - what came across;
   - that old schedules don't run;
   - what to connect again, and never by pasting a secret;
   - the merge rule (only what the owner picks, delete nothing until asked);
   - the directive line to write on the profile.

   It is written once per imported owner, and the owner deletes it with the folder.
2. **The onboarding skill** loses "If your owner moved from the old TinyAssets". It gains one permanent, general rule: "If the workspace already holds files you didn't seed (from an import, another app or an earlier agent), read the `README.md` at the top of any such folder first, and follow what it asks of you before your questions." That rule stays true after the cutover, for any owner who brings files.
3. **`CLIENT_INSTRUCTIONS`** keeps the platform's brain rules (`CLIENT_KEY`). Its onboarding paragraph is replaced by a pointer: "If `/workspace/USER.md`'s profile has no Onboarded date, read `/workspace/.agents/skills/onboarding/SKILL.md` and follow it." That way the owner's own edited skill governs (Direction 3), and the three copies of the onboarding steps (`v2:V2-H8`) become one.
4. **The plugins** are regenerated from the starter (B9) and so carry no import text.
5. **The two guards** that read `import/build.ts` as text go. The skill's sha pin is re-taken once, then held by B9's staleness guard. B24's vocabulary rule makes it impossible to put the notice back into a permanent file.

## D. The lanes, their order and their files

Each lane is a sequence of PRs with its own file set. Where a file is shared, one lane owns it and the other waits; each such pair is named below. Every lane starts on main after the u3 PR for its area.

| Lane | Owns | PRs, in order | Waits for |
|---|---|---|---|
| **C**: contract and ci kit | `ci/**`; `contract/src/{refusal,ids,limits,egress,checks,queues,binding,control,routing-mac,sync-ignore,text,digest}.ts`; `contract/guards/**`; `sources/**`; `package.json`, `tsconfig.json`; `*/guards/rules.ts` (the import half and `owed`, in C1 only); `scripts/probes/matcherprobe` | C1 B1 (layers and `owed`); C2 B9 (generate, staleness, Env generation config); C3 B2 (contract codes and guard); C4 B6 (contract grammars, limits, queues, `signinUrls`, reserved prefixes) with B5 (contract types); C5 B8 (locks CLI, module-state, the guards' moves, sources project, `ci/wrangler.ts`). B19's contract half rides C4. | C1 now; C2–C5 after u3-cross merges |
| **S**: sandbox | `sandbox/**`, `sandbox-template.yml`, `contract/src/sandbox.ts`, `sources/src/served.ts` (new) | **S0 (first of all):** `ta-runner:17`'s stale comment, `requirements.txt`'s scratch path, and the workflow's pip install only in `build` mode (`s2:N43`, `S87`, `N49`). The only image change in the wave; the paid build goes right after. S1 sandbox local surplus (`s2` §4's D3 list, `N25` as a gid read in the runner, `N50`, `S94`); S2 B15; S3 B16; S4 B19 (the runner half); S5 B11 (the E2B split); S6 B18; S7 B2 adoption (`START_CODES`); S8 B17 | S0 and S1–S3 now; S5 and S6 touch one import line in `deploy.ts` (coordinate with X: S5 before X2); S7 after C3; S8 after O1 |
| **O**: owner | `owner/**` (except `owner/src/sandbox.ts` in S8, and `owner/guards/starter.test.ts`); `contract/{doors.json,requests.ts}`, `contract/src/{doors,owner,requests,policy-document}.ts`; `owner/doors.json` | O1 B3; O2 B4; O3 B5 (the object half) and the import kind; O4 B2 adoption, B7 (owner), the B10 owner-test literals, B6 adoption (signins, inbound, egress, ranges); O5 B22 (the sweep half) | O1 after u3-owner; O2 after C2 and O1; O3 after C4; O4 after C3 and C4 |
| **D**: doors | `doors/**` except `src/{instructions,oidc}.ts`, `guards/identity.test.ts` and the B8 guard moves; `contract/src/apps.ts` | D1 B7 (`doors/src/kit.ts`, after C4's limits); D2 B2 adoption; D3 B5 (the binder half) and B6 adoption (queues, session TTL, loopback names); D4 B24 (the doors half: `doors/src/import/`) | After u3-doors; D1 after C4; D2 after C3; D4 after I1 |
| **V**: services | `gateway/**`, `files/**`, `hooks/**`, `starter/**`, `plugins/**`, `doors/src/instructions.ts`, `owner/guards/starter.test.ts`, `import/build.ts` (README only) and `import/guards/bundle.test.ts`, `contract/src/{gateway,files,hooks,envelope,wake-kinds,api-rules,egress-scope,model-record}.ts`, `specs/gateway/` | V1 B20 (starter and notice); V2 B6 (the services checkers, `ChunkGet.verify`, `objectPrefix`); V3 B21; V4 B22 (idle timeout, `resealDoorbell`, one meter), B2 adoption (files, hooks), B7 (gateway) | After u3-services; V1 after #5488 merges (`import/build.ts`); V4's B2 half after C3 |
| **X**: deploy and workflows | `deploy/**`, `platform-deploy.yml`, `handles-canary.yml`, `tests.yml`, `scripts/{cutover_handles_canary.py,platform_setup.py}`, `tests/test_platform_setup.py`, `ci/fixtures.ts` (`DARK_CONTROL`), `contract/{identity.json, src/signin.ts}`, `doors/src/oidc.ts`, `doors/guards/identity.test.ts` | X1 B10 (`control.json`, `facts.ts --import`); X2 B11 (Cloudflare client, `ci/wrangler` adoption); X3 B12; X4 B13 (needs `closedToAll` in `contract/src/control.ts`, added in X4 after C5); X5 B14 | After u3-cross and C5; X4 after D1 (`doors/src/config.ts`) |
| **I**: importer | `platform/import/**` except `build.ts`'s README; `import-window.yml`; `contract/src/import-message.ts`; `platform/cutover.json`; `ci/guards/cutover.test.ts` | I1 B10 and B11 adoption plus the `.centers.db` order; I2 B24 (manifest, bundle format, `KNOWN_PRODUCTION_WORKFLOWS`) | After #5488 merges, after X1 (`control.json`, `facts.ts`) and X2 (the client) |
| **T**: tests through the front door | `owner/test/**`, `doors/test/**`, `owner/memory/**` → `gateway/memory/`, owner constructors | Research first (B23, five questions); then B23 in owner, then doors | Research may start now; the build after O4 and D3 |
| **B25** (if F1 is yes) | `hooks/**`→`codemode/**` and every binding reference | One PR | Last, before the dark redeploy |

**Order:**
1. **Now:** S0 (so the paid build can run), C1, S1–S3, and B23's research.
2. **As each u3 PR merges:** C2–C5, O1, V1, D1.
3. **Then:** O2–O5, D2–D4, V2–V4, S5–S8, X1–X5, I1–I2.
4. **Then:** T, then B25.

**Shared files that need care:**
- `deploy.ts` is touched by C5 (the lock call), S5 (the E2B import) and S6 (the template reader) before X3. Each is one line, so whoever lands second rebases.
- `doors/src/config.ts`: D1 first, then X4.
- `import/build.ts`: V1 (the README) and I1 (the default tier). V1 first.

## E. After the lanes: comments, tests, re-check

**Step 4, comments.** Use each listing's comment section, re-counted after the lanes:
- owner `o2:2g`;
- doors `d2` §G;
- sandbox `s2` §2i (132);
- services `v2` §2.6 (about 134);
- cross `x2` §2.10;
- not the importer (§B above).

Remove what only restates the code. Fix or remove the stale ones. Drop the review and slice provenance tags and the old-stack pointers. Keep the vendor, spec, measured-limit and reason comments. B26 rides this step. One PR per area, run by the area's lane after its last PR.

**Step 5, tests, last.** Use each listing's `[test]` items that the lanes didn't already remove:
- owner `o2:2h` (constant pins, sleeps);
- doors `d2` §H;
- sandbox `s2` §2h (`S90`–`S92`, `N52`, `N54`);
- services `G-T4`, `G-T5` (u3 left the wall-clock ones);
- cross `x2:2.3` (`LIVE`, `servedBy`).

Remove what repeats the implementation or can't fail. Rewrite what's tied to internals against behaviour. Turn sleeps into event waits, or fake time. Mutation-check each rewritten test.

**Step 6, the re-check loop:**
1. Fresh listing agents, one per area (owner, doors, sandbox, services, cross), with the list2 brief at the new head. Each diffs against this log and reports drift (what came back, what got around a guard, what's new and tangled) and re-tangling by the wave's own PRs.
2. The loop is done when:
   - every `owed` list is empty;
   - the B1 layer guard holds with no exceptions;
   - the agents find nothing worth fixing;
   - the full suites, the recorders, the harness `plan` and the frozen SQL show behaviour unchanged apart from the listed changes.
3. Repeat steps 3–5 on any findings.
4. Run once more when the build is done and cutover-ready (the founder, 2026-11-10).

**Reviews must not re-tangle.** Every review receipt answers:
- Does this PR add an edge the layer table doesn't allow, or an `owed` entry?
- A literal live name or founder number outside the configs and data?
- A code read from a message?
- A product parameter or optional binding only a test uses?
- A copy of a value the contract holds?
- A per-module allow-list?

Any yes blocks the PR.

## F. Founder decisions and actions (kept to the minimum)

- **F1 (decision):** rename the hooks Worker to `codemode` (B25)? It's a live name, and the change deletes the dark `tinyassets-hooks` Worker and its DO class (Fact 4). **Recommendation: yes**, before the dark redeploy, because after the cutover any rename is a live migration. If no, B25 is dropped and nothing else changes.
- **F2 (action, spend already approved in A6):** the one paid template build, right after S0 merges, then the pin PR. Until the pin PR, the deploy kit refuses `apply` (the pin `s85528cdd2cd74efa` doesn't equal the tree's image tag). No other fix in this wave changes the image: each guards recipe identity and an unchanged tag. Runner and probes changes (B2 sandbox, B7, B15–B17, B19, S1) each cost only a live pair check inside the approved spend, not a build.
- **For information, covered by the `routes` go:** B13 settles the doors' `/app` routes as `tinyassets.io/app` plus `tinyassets.io/app/*`, with the proxy keeping `api*` alone (superseded by d3 §J1: `app*` and `api*` to the doors). The handles canary runs after the routes go.

### Critical files for the work
- `platform/ci/kit/imports.ts`
- `platform/contract/src/refusal.ts`
- `platform/owner/src/owner-do.ts`
- `platform/deploy/deploy.ts`
- `platform/sandbox/runner/protocol.ts`

## G. Review of this plan (2026-11-11)

The coordinator reviewed the whole plan and the in-flight branches. The design stands. These changes bind every lane:

1. **B4 needs research before it is built (Fact 14).** The plan chose a generated region inside `owner-do.ts`. A simpler shape may exist: the door table defines the object's methods at module load (`OwnerDO.prototype[name] = …` for each row), and `interface OwnerDO extends OwnerDoors {}` gives the types. That leaves no hand-written method and no generated code in a hand-written file. O2's builder first proves, in vitest-pool-workers and against Cloudflare's RPC documentation, whether Workers RPC serves prototype methods defined that way, with the same stub types and the same reserved-name rules. If it does, B4 uses it and the generator writes only `OwnerDoors` and `requests.ts`. If it doesn't, the generated region stands.
2. **The one paid template build moves to the end of lane S.** No deploy is planned before the untangle is done, so building right after S0 gains nothing. Building after the lane's last PR removes the risk that a later sandbox fix needs a second paid build. S0 still merges first. The pin PR follows the build, and the dark redeploy follows the pin.
3. **The rehearsal's platform half waits for the untangled build.** `stage`, `verify`, `wipe` and `admit` exercise the doors and the owner's import kind, which B5 and B24 reshape. They run after the dark redeploy of the untangled build, so we rehearse the shape we cut over to. `census` and `bundle` only read the backup copy, so they may run as soon as the credentials are right: what they find about the real data is useful early.
4. **Importer changes stay minimal** (§B). Slice 7e (#5490), the credential-shape refusal, was built before this review. It is a fail-loud input check (Fact 8), so it finishes its review at low priority. Nothing else is added to the importer outside lane I.
5. **Fewer, larger PRs where one review can hold them.** A lane may land consecutive fixes as one PR when they touch the same files, stay under about 1,500 changed lines, and have no other lane waiting on the first alone. The serial merge queue, at about 35 minutes a PR, is the slowest part of the wave.
6. **Load and spend.** The host has 4 cores: every heavy run goes through the host lock. At most five agents run at once. A review is sized to its PR's risk: a deletion-only PR gets a short review; the owner, the contract and the release kit get the full one.
7. **Order by what defines the shape.** When capacity forces a choice, the structure-defining fixes go first: B1, B2, B3, B4, B5, B10, B11, B12, B13, B15, B24. The local kernels (B7), the guards' homes (B8) and the docs (B26) follow.
8. **Founder decision F1 (B25, the `codemode` rename) is still open.** Nothing in the wave depends on it. It is asked once more when the lanes are done.
9. **F1 is decided (the founder, 2026-11-11: naming is the coordinator's call, not his).** B25 goes ahead: the hooks Worker becomes `codemode`, as one PR before the dark redeploy. The dark `tinyassets-hooks` Worker holds only throwaway VM state and is removed by that redeploy.
10. **B13's route decision is reopened (found by `list3_clients.md`, 2026-11-11).** `tinyassets.io/app` plus `tinyassets.io/app/*` may not match `/app?upgrade=1` (which `doors/app/app.js` reads and the site links) or the Android notification link `/app?request=`: Cloudflare matches a route against the whole URL, query string included, and a pattern can't hold one. X4's builder researches Cloudflare's route matching first (Fact 14), then picks the patterns that take every `/app` URL the app, the site and the shells use and no website path. The identity guard then checks those URLs, query strings included.
11. **The clients outside `platform/` join the plan** (`list3_clients.md`). Its root fixes F1–F4 (one shell contract in `identity.json`, the site reading the contract, the cutover manifest covering every path outside `platform/`, and the mark's source moving out of `tinyassets/desktop/`) are folded in with the other two list3 reviews, as one order for the whole remaining build.
12. **A workflow's inline module may import nothing of the platform's by a path** (C1's review, 2026-11-11; one step past B1's text, which refused only a Worker's `src` and `deploy/deploy.ts`). A workflow reads what it needs from data, or runs a script file that is in a layer. The guard reads the code after `-e`, `--eval`, `-p` and `--print`, and every module a step's text or a `github-script` script names by a path, in workflows and composite actions. A path rooted at an expression such as `$GITHUB_WORKSPACE` is held from the repository root. The one thing an inline module may load by a path is `scripts/`, because two old-stack workflows' `github-script` steps load `scripts/incident_issue.js`, and `scripts/` may import nothing in `platform/`. B11's line "`sandbox-template.yml` imports `admin.ts`" therefore means through `checks.ts`, as the workflow runs today.
13. **`sandbox/src/install.ts` gets a home under `sandbox/e2b/` in B11.** The template's checks install a bundle with it, and the template may not import a Worker's `src`. B11's text names only the E2B client's split, so S5 moves `install` too. The `owed` entry `template/checks.ts` → `sandbox/src/install.ts` is tagged B11 for that reason.
14. **The layer table needs three more rows.** `sandbox/e2b/` gets one when B11 creates it (the Worker may import `transport.ts` and not `admin.ts`), and `sandbox/build/` gets one when B18 creates it. Until a row exists, the first file in either folder fails as "in no layer". Each lane adds its row to `ci/kit/layers.ts` in its own PR, and B18 also lets the template import `ci/wrangler.ts` for `pinned()`. The third is a row of their own for a workspace's tools (`sandbox/runner-build.ts`, `starter/bundle.mjs`, `plugins/build.mjs` and the generators): today they share the tests' row, which lets them import the test kit. The new row allows their own workspace, the contract and the records. No tool imports `ci/` today, so the row has no violation; lane C adds it.

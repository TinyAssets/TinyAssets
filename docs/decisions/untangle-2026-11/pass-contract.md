# pass-contract as built (2026-11-21)

d2 §E steps 4 and 5 for the contract area (`platform/contract/`), on the model of
`pass-sandbox.md`, `pass-files.md`, `pass-doors.md`, `pass-gateway.md`, `pass-ci-deploy.md`
and `pass-small.md`. The cross listing (`list2_cross.md` §1, §2.6, §2.10) covers the
workspace's plumbing and guards, and the other listings its `src/` modules beside their users;
every item was re-read at `origin/main` `88e74e1cd`. What the other passes left to this one:
the contract's own tags (`slice W`, `G4`, `review round 2`, `rveg_report.md`; `pass-gateway.md`
§1), the tests of the contract's rules in the gateway's `meter.test.ts`, `credential.test.ts`
and `metered-body.test.ts` (`pass-gateway.md` §2), `policy-document.ts`'s research pointers and
`apps.json`'s (`pass-small.md` §1), `files.ts`'s docs (`pass-files.md` §1), and the owner side
of the contract (`pass-owner.md` §1).

The passes are two PRs:

1. **This PR** (`untangle/pass-contract`): both passes on every file of the area but seven.
2. **`untangle/pass-contract-2`**, owed: the seven files `untangle/r5-hooks` (#5707) changes,
   once it merges, so the two merge cleanly (§4). Its item is §4's, not a `tasks.md` line
   (`owed-in-decisions.md`).

**Behaviour is unchanged.** Every non-test source of the workspace (`src/*.ts`, `requests.ts`,
`vitest.config.ts`, `guards/rules.ts`), each minified with esbuild, has the same digest on base
and head, so no line of code changed, only comments. The JSON data files are the same once
their prose (`about`, `why`) is set aside; four `about` texts changed. Every Worker's entry
(codemode, doors, files, gateway, owner, sandbox), bundled with all it imports and its JSON's
prose set aside, has the same digest. The cutover's import window (`import-message.ts`,
`import-names.ts`) is not passed: `cutover.json` deletes it, as `pass-doors.md` skips it.

## 1. Comments

**Kept:** vendor facts (pi-ai's adapters and their lines, ProtoJSON's and Go's key matching,
workerd's error serialization, Cloudflare's route matching and KV's 60 s, E2B's network and
plan ceilings, Pushover's priorities, RFCs, the SSE specification), spec pointers (ADR-032 to
ADR-037, d3's sections, `engines-replan.md`, `limits-audit.md`, `main-agents.md`, the
owner-object spec), the decision files that hold a design (`untangle-2026-11-wave-1.md`,
`r8-account-loss.md`, `r8-erased-orphans.md`, `r8-locate.md`), the founder's rules with their
dates, the measured costs with their source (`text.ts`'s `utf8Bytes`, `sync-ignore.ts`'s
step), and the reason comments.

**Removed or rewritten:**
- **Provenance tags,** about 70 across the sources, the data and the guards: `slice W` (and
  `slice W, tasks 7`), `slice S`, `slice E`, `slice P`, `slice M`, `slice ID`, `slice SIGNIN`,
  `slice L`, `slice BRAIN`, `the starter slice`, `tasks 2`, `tasks 4 G4`, `tasks 6`, `G4`,
  `N10`, `S31`, `plan §6` (four), `pi audit F6`, `R5 P-resume`, `untangle B6`, `untangle C8`,
  `A6` (`socket-shape.ts`'s "Salvaged from A6"), and the review ids (`review round 2`,
  `round 3`, `review M1`, `M2`, `M3`, `M-b`, `L5-1`, `L7`, `R2-M1`, `M-5`, `M-4`, `H-1`, `V-M1`,
  `V-H2`, `M5`, `round 7, R7-1`). Git holds where a line came from. The sentence each hung on
  stays where it says why.
- **The sync-ignore guard's hostile lists,** named for the review's rounds (`R1` to `R7`,
  `rounds 1 to 6`, `R7-2`): each case is named for what it holds ("2,000 star lines",
  "**/a/…/a/b, files 2,000 folders deep"), the describe is "the hostile lists", and "R7 in
  the sync's own orders" is "the deep list in the sync's own orders". `tools.test.ts`'s
  "serves a run no mailbox tool" loses its `(slice ID)`, and `apps.test.ts`'s "logs a refused
  registration …" its `(review R2-M1)`. No budget names any of them.
- **Pointers to working files never in the repo:** `autoconnect_research.md` (`apps.ts`,
  twice in `apps.json`), `matcher_research.md` (`sync-ignore.ts`), `memshape_research.md`
  (`limits.ts`), `operator_research.md` (`operator-message.ts`), `r5_research.md`
  (`acts.json`), `rveg_report.md` and `rvst2_report.md` (`slots.ts`, the sync-ignore guard),
  and the bare `research` sections (`agent research §6.4` and `gateway research §4` in
  `policy-document.ts`, `research §1.3, §2.4`, `§2.4`, `§2.6`, `§4.3`, `§7`, `checklist 11`,
  `checklist 18, 21`). `policy-document.ts`'s "8b's live probe" names the probe that holds the
  fact, `sandbox-probe.md` (private CIDRs blocked).
- **The old stack,** whose code the cutover deletes: `control.ts`'s
  `TINYASSETS_OUTSIDE_DENY` and `router_traffic.admits`, `binding.ts`'s `api_keys._client`,
  `egress-scope.ts`'s "the Python's `pkce_user_key_v1`" (the records' protocol),
  `inbound-proof.ts`'s "ported from `broker/inbound_verification.py`", `limits.ts`'s "the
  Python's 28 800", `text.ts`'s "matching the Python trusted side's", `len()` and
  `validate_principal`, and `policy-document.ts`'s `_validate`, `_digest`,
  `normalize_destination` and the Python's IDNA fast path. `canonical.ts`'s `json.dumps(...)`
  and `text.ts`'s `str.isprintable()` stay: each names the grammar the code follows, as
  `pass-owner.md` keeps `str.isidentifier()`.
- **Stale:**
  - `policy-document.ts` `PolicyLimits` said proposals keep "the Python's 30 days"; the code
    keeps them 90 (`proposalTtlS: 90 * 86_400`). The doc now says only what the figures are
    for, beside them.
  - `policy-document.ts` `MODEL_DEFAULTS` said the Python's per-turn limits are "the
    defaults' model, raised for tokens", and that a request cap bounds a runaway loop; every
    default is `null`, off until the owner sets it (Fact 15). And "the `turn_` names stay
    until slice R names them for runs": the object maps them to the run's (`owner-do.ts`), so
    the doc names them the `turn_` settings.
  - `apps.ts` and `apps.json`: the connect page "lands at the cutover (branch
    `platform-brain-site`, `tasks.md` §9)". That branch no longer builds; the page is built
    from `apps.json` in the site's cutover PR (`tasks.md`, the public `/connect` page).
  - `sandbox.ts` `NetworkPlan` "What the sandbox slice sends": the owner's object makes it
    (`owner/src/network.ts`) for a start that opens a lease and for a re-apply.
  - `guards/rules.ts` `OWED`: "until lane I's B7 adopts them". #5709 rewrote it on `main`
    (B24, leaving with the message) and gave `cutover.json` the edit that takes the entry
    out; this PR takes `main`'s (§3).
- **Orphaned:** `sandbox.ts`'s doc of `SandboxStart.urls` sat above `route`'s doc, two docs
  stacked on one field. Each is on its own field.
- **Copies of values in prose:** `content.ts`'s and `slots.ts`'s "4 MiB chunks" and
  `limits.ts`'s "two 4 MiB chunks'" and "`BODY_BYTES_MAX` (8 MiB)" (the constants name
  them), and `policy-document.ts`'s "free 25 GB, paid 250 GB", the founder's tier numbers,
  which the control record holds (`control.ts` keeps the founder's ruling with its date).
- **Run-on lines** (a paragraph patched past 100 characters, or broken mid-sentence or mid
  path) reflowed: `control.ts`'s header, `files.ts`'s `MARKS`, `identity.ts`'s header,
  `binding.ts`'s `sandbox` and `tiers`, `sandbox.ts`'s header and `SandboxSignin`,
  `slots.ts`'s `SlotPool`, `limits.ts`'s frozen bounds, `operator-message.ts`'s "Pure",
  `wake-records.ts`'s header (its `gateway/src/\ndoorbell.ts`), and `doors.ts`'s `Tool`.

**Left as they are, each with its reason:**
- The long headers (`api-rules.ts`, `envelope.ts`, `refusal.ts`, `slots.ts`, `doors.ts`,
  `wake-records.ts`, `operator-message.ts`, `routing-mac.ts`, `sync-ignore.ts`). Each is the
  one place that tells its rules end to end, and the functions' docs point to it.
- The one-line docs of the exported checks and types. Each says what its name does not: the
  refusal it answers, the grammar it holds, the reader it serves.
- `control.ts`'s tier numbers in `Tiers`'s doc: the founder's ruling, dated, beside the
  record's type, which holds no number.
- `limits.ts`'s "a 40 GiB file" and `file-defaults.ts`'s "(40 GiB)": what `MAX_INTENT_CHUNKS`
  is for, beside its product.
- `src/queue-names.ts` and `src/requests.ts`, which are generated (`npm run generate`).
- The cutover names (`files.ts`'s "the importer's wipe", `binding.ts`'s import kind,
  `paths.ts`'s `IMPORTED`): `cutover.json`'s edits name them, and they go at the cutover.

## 2. Tests

| Item | At this head | Decision |
|---|---|---|
| The tests of the contract's rules in the gateway (`pass-gateway.md` §2) | 12 of them use only the rules, on hand-written calls, answers and endpoints | **Moved** to the contract's guards, beside the rules (below). |
| The rest of `meter.test.ts`, `credential.test.ts` `source`, `metered-body.test.ts` | Each holds a rule to pi-ai's own call, to the source records, or to the gateway's clamp and weight | **Kept** in the gateway. pi-ai is the gateway's package, and the contract imports none; the records are `sources/`, which imports the contract. |
| cross §2.6 the records' tests and rules in `contract/guards` | `sources/` is its own workspace | Done (`pass-small.md`). |
| cross §2.6 five generators | `npm run generate`, held by `ci/guards/generated.test.ts` | Done. |
| cross §2.6 `doors.json` `handler` | The handlers are the owner's (`owner/doors.json`) | Done. |
| cross §2.6 `pi-ai` a contract devDependency | Gone: the contract depends on `@types/node` and `vitest` alone | Done. |
| cross §2.6 `refusal.test.ts` re-implements `workerFolders` | It imports `ci/wrangler.ts` `workerFolders` | Done. |
| The sync-ignore guard's CPU-bound cases | Their bound is the automaton's, measured in the host's own units (`overBound`) | **Kept.** A run of the whole workspace beside the gateway's tests failed one of them under load; alone, under the run lock, all 27 pass, as the skill says a timeout under load is. |
| `envelope.test.ts`'s construction rebuilt by hand | It rebuilds the documented form, not the code | **Kept:** the form is the contract the bindings' old blobs are held to. |
| `binding.test.ts`'s `BUILT` and `MALFORMED` tables | Each kind as the binder builds it, and each malformed one way | **Kept:** the parser's subject, by behaviour. |

**Moved** (12 cases, the same assertions, so no check is lost):
- to `guards/api-rules.test.ts`: from `meter.test.ts`, "cites a page and the day it was read for
  every free call", the paid tools' and the unpriced options' tables, and the four tests of the
  served tier (`servedPriced`: an Anthropic answer's tier, a Responses answer's terminal event,
  SSE's line endings and multi-line data, a chat-completions answer's tier); from
  `metered-body.test.ts`, both tests of a key spelled another way (`aliased`), its describe no
  longer titled by "egress shape research §4".
- to `guards/egress-scope.test.ts`: `credential.test.ts`'s `allows` table and its dotted path.

The moved tests' two stream helpers (`sse`, `streamOf`, three lines each) are written again in
the contract's file. Each is a test's own frames, written from the SSE specification, so no
copy has to change with another. The gateway holds `sse` twice already, `meter.test.ts`'s and
`test/fake-upstream.ts`'s, and the fake cannot import one: `ci/workers.ts` `fake` transpiles
it as a single module. A shared support module would serve two of the three and tie the
gateway's tests to the contract's guards for three lines.

## 3. Found and fixed beyond comments

- **The cutover's manifest missed `contract/guards/rules.ts`.** Its `OWED` lists the import
  message's two SHA-256s. The cutover deletes `import-message.ts`, and `ci/kit/owed.ts`
  `ratchet` then refuses the entry as one that no longer holds. This PR added the edit; #5709
  (the cutover's dry run) added the same edit on `main` while it was open, and named the
  entry's fix B24. On merging `main`, this PR keeps `main`'s entry and comment and drops its
  own, so the manifest names `rules.ts` once.

## 4. Owed

- [ ] `platform/contract/`, part 2: both passes on `doors.json`, `guards/wire.test.ts` and
  `src/{acts,egress,owner-doors,owner,requests}.ts`, once #5707 merges
  (`untangle/pass-contract-2`).
- [ ] `contract/src/wake-kinds.ts` `canonicalJson`, a second canonical JSON, gone for
  `canonical.ts`'s, the platform's one (`untangle/wake-canonical`).

Part 2's seven files `untangle/r5-hooks` changes, not touched here so the two merge cleanly:
- `doors.json`: its `about` and the tool descriptions (agent text, generated into
  `owner-doors.ts`'s docs); no tag found, to be read whole with the hooks' rows.
- `guards/wire.test.ts`: no tag found.
- `src/acts.ts`: its header's `r5_research.md` §5, a working file never in the repo.
- `src/egress.ts`: its header's "(gateway research §5.4; tasks 4 G4)", and `DNS_SERVERS`'
  "(research §5.4.1); 8b's live probe checks …", a slice tag on a probe that is
  `tasks.md`'s egress live probes.
- `src/owner-doors.ts` and `src/requests.ts`: generated; read with the door table.
- `src/owner.ts`: no tag found; its docs read whole with the hooks' doors.

And the test pass on the same files.

**Found and left, a code change** (the second item above): `wake-kinds.ts` `canonicalJson` is a second canonical JSON beside `canonical.ts`, the
platform's one (`contract-copies.md` item 3). It differs only on `undefined` (it never drops
a field), which no subscription's JSON arguments hold, so `subscriptionId` would read the
same. Its fix is a code change, and this pass changes none.

## 5. Proof

- Every non-test source's esbuild minified digest, the JSON data with its prose set aside,
  and each Worker's entry bundle, the same on base `88e74e1cd` and head.
- The suites of what changed: the contract's guards, 362 tests (350 and the 12 moved), and
  the gateway's three changed files, 127 (12 fewer).
- Mutations, each failing its test:
  - `aliased` reading a key past ASCII as any other (the moved "is refused at every level the
    rules read", now in the contract's guards);
  - `allows` without its encoded-separator check (two cases of the moved `allows` table);
  - `allows` without its user-info check (the review's: the moved `user:pass@` case).

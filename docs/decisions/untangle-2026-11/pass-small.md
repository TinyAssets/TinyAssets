# pass-small as built (2026-11-21)

d2 §E steps 4 and 5 for four small areas, `platform/codemode/`, `platform/sources/`,
`platform/starter/` and `platform/plugins/`, both passes for all four in one PR (`tasks.md`,
the passes line). The line asks for one PR an area. These four are small (about 340, 3,500,
500 and 150 lines of code and tests, the generated plugin copies and the vendored spec aside),
and four PRs would be four full CI runs for a few dozen comment lines, so they share one, on
the model of `pass-sandbox.md` and `pass-files.md`.

The listings they start from were taken at `fdac25ba4`: the plugins and starter parts of
v2 (`list2_services.md` §1, §2.4 for the hooks Worker that is now `codemode`, §2.5, §2.6) and
what the cross listing says of `sources/` (`list2_cross.md` §1, §2.6, §2.10). Every item was
re-read at `origin/main` `1e2100a5c`, and most were fixed before this pass. Where no listing
exists (the source records' tests, the codemode Worker's comments), the pass was done from
the code.

**Behaviour is unchanged.** Every non-test source of the four areas, each minified with
esbuild, has the same digest on base and head but one: `codemode/src/codemode.ts`, whose two
constants lose an `export` no reader used. The codemode, doors and owner Workers' entries,
bundled with all they import (packages external), have the same digests, and so do the
sandbox's runner and probes bundles, so the pair's tag does not move. `npm run generate`
leaves the tree as committed. The recorders match.

## 1. Comments

**Kept:** vendor facts (pi's measurements and defaults, E2B's egress CA, Gemini CLI's
root-owned settings, RFC 8414, RFC 8628, OKF's in-place edits and its relocation, Renovate's
cooldown), ADR pointers, and the reason comments: why a path is under `{home}`, why a prompt
is never bare, why a template is read whole.

**Removed or rewritten:**
- **Pointers to working files that were never in the repo:** `pi audit F8` (four places),
  `agent research §6.4` (two), `gateway research §2.4, §2.6, §7.2`, `okf_update_research.md`
  and its `§1.1`, `§1.4` and `§3` (`okf-watch.ts`, and the line in
  `.github/workflows/okf-watch.yml` that names the same file), `shared_brain_research.md` and
  `autoconnect_research.md` (`plugins/build.mjs`), and `starter_research.md`
  (`manifest.json`'s `about`). The sentence each hung on stays where it says why.
- **Provenance tags:** `tasks 4 G3` (twice), `slice S's probe` (twice), `slice BRAIN`,
  `untangle B23`, `review M1`, `review L6`, `review round 4, R4-1`, and in the tests `(M1)`,
  `(H2)`, `(M2)`, `(M3)`, `review L5`, `review L7`, `(M5)`, `(P3)`, `Review M2 and M4`, `H1:`,
  `M3:`, `M4:`, `M5:`, `M2:`, `Round 2:`, "the survivors of this round's own mutation run" and
  "the three mutants the reviewer's run left alive". `check.ts`'s `(rule 1)` to `(rule 4)`
  and the test's `codex base URL rules 1-4` and `Rule 1:` to `Rule 4:` numbered a list that is
  in no file. Each comment already says its rule, so only the numbers went.
- **Stale:**
  - `codemode/src/index.ts`: "Not deployed: no workflow deploys platform/". The kit deploys
    it.
  - `codemode/test/codemode.test.ts`: "past the large class's 286 s", a copy of a derived
    budget that has since moved. It names the budget (`ci/budgets.json`) instead. Its
    "(1,000,000 polls)" was a copy of `CODEMODE_INTERRUPT_BUDGET`, and the doc of that
    constant copied the Worker's `cpu_ms` from `wrangler.jsonc`. The file header already
    points there.
  - `plugins/SUBMIT.md`: "Raise the plugin's version with every starter version". Nothing is
    raised by hand: `build.mjs` takes the version from `manifest.json`.
- **Restating the code,** 19 of them:
  - `codemode/src/index.ts`: `codemodeExecute`'s doc, a copy of the contract's own
    (`contract/src/codemode.ts` `CodemodeWorker`);
  - `sources/src/check.ts`: the seven section banners in `cliSpec` (the install, the run
    secret, the compaction, the home, the MCP door, the engine, the regions), each its
    schema field's doc again; the two `invoke` notes, each the refusal's message below it;
    and `HOSTED_USE`'s doc, `HostedUse`'s word for word;
  - `sources/src/catalog.ts`: `bundledSources`'s doc and the engines loop's note, the
    module's header and the loop's refusal again;
  - `sources/src/served.ts`: `Served`'s doc;
  - `sources/write-records.ts`: the docs of `RECORDS_DIR`, `RECORDS_FILE` and
    `recordFiles`;
  - `starter/okf-watch.ts`: `quoted`'s inline note, its doc again;
  - `plugins/build.mjs`: the note over `IDENTITY`.
- **Run-on lines,** patched in past 100 characters: `signinShape`'s doc
  (`sources/src/signin.ts`), and the paragraphs the cuts above left short or long.

**Left as they are, each with its reason:**
- `sources/src/schema.ts`'s 40-line header. It is the one place that tells a record's three
  ways to connect and an engine record's parts end to end.
- `codemode/wrangler.jsonc`'s note that `cpu_ms` leaves room above "about 100 s of a tight
  loop". It is the config's own reason for its one limit.
- The starter's workspace (`AGENTS.md`, `SOUL.md`, the skills, `okf-spec.md`) and the
  plugins' copies of it. They are the agent's text, not comments: a change there is a new
  starter version, shipped to every owner, and the plugin copies are generated.
- `src/env.ts` and `src/records.ts`. They are generated, and the generated-files guard holds
  them.
- `contract/apps.json`'s pointers to `autoconnect_research.md`. It is the contract area's.

## 2. Tests

| Item | At this head | Decision |
|---|---|---|
| The source records' attacks in three files named for review rounds (`sources-attacks.test.ts`, `-r2`, `-r3`) | Present, each file its own copy of the helpers, and 30 of their cases the same edit to the same field as a case in `sources.test.ts` or in another round's file | **Consolidated** into one `sources-attacks.test.ts`, named by subject, its header saying what it holds. Each duplicate goes (the `Test-Removal:` lines); the cases that hold a rule no other case does stay. |
| `sources-attacks.test.ts` "engines cross-check" (a key naming `codex`) | The one case of a CLI record that runs on another provider's key | **Folded** into `sources.test.ts`'s "are each key's engines" loop, beside `kimi`, `nope` and `groq`. |
| Fifteen words of `check.ts`'s three patterns | No test failed with the word gone: `CREDENTIAL_NAME`'s `API_?KEY`, `KEY`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIALS?`, `AUTH` and `ENDPOINT`; `CREDENTIAL_KEY`'s `environment`, `api_?key`, `secret`, `password`, `credential` and `helper`; `ENDPOINT_ARG`'s `endpoint` | **Made to fail.** One plant for each word, a name or a key or an argument that only that word matches (`otelHeadersHelper` for `helper`: `apiKeyHelper`, the one plant there was, is caught by `api_?key` too). Each word removed fails its plant, and all 27 words of the four patterns now do. |
| `RUN_BASICS` pinned to its nine names | Present | **Kept.** No rule says which basics may carry code into a run: `CODE_LOADING` refuses `NODE_EXTRA_CA_CERTS` in a record, and the basics carry it on purpose. The pin is where a widened inheritance is seen. |
| `sources.test.ts` "fails loudly: %s" reads only that a refusal names its record | Present | **Left.** A case that a second rule refuses first still fails loudly, so a case can hold less than its title says. Matching each case's own message is a rewrite of some 200 cases, not this pass; the pattern sweep above found the rules no case held. |
| `codemode/guards/isolation.test.ts`'s closure pin | Present | **Kept.** It is the Worker's import closure, five files, and a new one is a change to the Worker's reach the reviewer should see. |
| v2 `K-T1`, no behaviour test of the Worker | `codemode/test/codemode.test.ts` | Done. |
| v2 `P-T1`, `P-T2`, `V2-T3`, `V2-ST1`, `V2-ST2` (the owner's and doors' starter guards) | "DID NOT RUN" gone; `apps.test.ts` gone (`ci/guards/generated.test.ts` holds the plugins); the import notice is the cutover guard's (`ci/guards/cutover.test.ts`) | Done. |

The starter's own guard (`starter/guards/okf-watch.test.ts`) loses only its tags. Its
large `apply` test passes on the head.

## 3. The listings' items

| Item | At this head | Decision |
|---|---|---|
| v2 `K-N1` the Worker named "hooks" | `platform/codemode`, `tinyassets-codemode`, `CODEMODE_VM` | Done. |
| v2 `K-S1` `Hooks` exported twice | One default export | Done. |
| v2 `V2-SP4` one date in six configs | The kit's `dateProblems` | Done. |
| v2 `V2-H6` `NAME_MAX` | `EXECUTION_NAME_MAX` and `executionName` in the contract, refused as `invalid` | Done. |
| v2 `P-H1` `apply` leaves the plugins stale | `apply` runs `npm run generate`; the workflow adds `platform/plugins`; the large test holds both | Done. |
| v2 `P-H2` `BUNDLED`, a second list of skills | `plugin: false` in the manifest | Done. |
| v2 `P-H3` tool names in the starter's text | `doors/guards/definition.test.ts` holds them to the door table | Done. |
| v2 `P-H4` the OKF spec four times | Once vendored, three generated copies (`starter.json`, the two plugins) | **Kept**, the generated-files guard holds the copies. |
| v2 `P-H5′` the publisher written per manifest | `PUBLISHER` once, the legal page from `identity.json` | Done. |
| v2 `V2-H7` the manifest rewritten whole | `manifestText` keeps its layout, `tags` in the pin, and the large test reads the diff | Done. |
| v2 `V2-H8` the onboarding rule three times | The doors' steps are generated from the skill (`doors/onboarding.json`) | Done in the starter's part. The instructions' first sentence is the doors'. |
| v2 `V2-SP5` the import notice in the starter | Gone from the starter and the plugins | Done. |
| v2 `P-SP1` first-run fallbacks | `committed()` gone; `bundle.mjs` fails without a committed bundle | Done. |
| v2 `P-S1′` the PR body's ritual lines | Gone; the guard refuses them | Done. |
| v2 `V2-H9` the importer's reading of the starter | `import/build.ts` | The import area's. |
| cross §2.6 the drifted credential pattern | The test imports `CREDENTIAL_NAME` | Done. |
| cross §2.6 the records' tests in `contract/guards` | `sources/` is its own workspace, with its guards, rules, budget and `pi-ai` | Done. |
| cross §2.6 five generators | `npm run generate`, held by `ci/guards/generated.test.ts` | Done. |
| cross §2.6 `SigninState`; exports used only in their files | `SigninState` gone, and every exported value is read outside its file. The types named only in their own file (`RunEnv`, `SignsIn`, `NpmInstalled`, `StateCheck`, `UserKey`, `Rfc6749`) type exported functions' answers and the schema's parts | Done. **Kept** the types. The codemode Worker's two constants, exported for no reader, lose their `export` here. |
| cross §2.6 `catalogOf` exported for tests | It is the catalog's one constructor, which `bundledSources` calls | **Kept.** |
| cross §2.6 `bundledRecords()` returns `RECORDS` | Present; read by `sandbox/runner/probes.ts` | **Found and left.** See below. |
| cross §2.6 `build.py` reads the records | `sandbox/template/build.py` is gone; `image.ts` reads `served()`, and `image.py` reads no record | Done. |

**Found and left:** `sources/src/catalog.ts` `bundledRecords` is a pass-through of
`RECORDS`, and `check.ts`'s `HOSTED_USE` repeats the three words of `schema.ts`'s
`HostedUse`, which nothing ties together. Both are in the probes bundle's closure, so either
fix is a new probes digest, a new pair, and this pass moves no pair. Both are one sub-line of
`tasks.md`'s re-check loop line, for the next change to the probes.

**The areas' swallowed errors and retries.** None of the four retries or waits. Each catch
was re-read and is kept, with its reason beside it:
- `check.ts`: a URL, a settings template or a pattern that does not parse is a refusal of
  the record, never read past; the gateway's own check's refusal becomes the record's,
  naming it.
- `signin.ts`: a URL that does not parse is not credential-shaped and not the record's page.
- `okf-watch.ts` and `bundle.mjs` throw or exit on every fault.

## 4. Proof

- Every non-test source's esbuild minified digest the same on base and head but
  `codemode/src/codemode.ts` (two `export`s); the codemode, doors and owner entries'
  bundles, and the sandbox's runner and probes bundles, the same.
- `npm run generate` leaves the tree as committed.
- The recorders: `wire`, `tools`, `rpc` and `rv` are the same.
- Mutations, each failing its test:
  - each of the 27 words of `CREDENTIAL_NAME`, `CREDENTIAL_KEY`, `ENDPOINT_KEY` and
    `ENDPOINT_ARG` removed (15 failed nothing on base);
  - the engines cross-check taking any CLI with key variables (the folded `codex`);
  - for the removed duplicates, each rule broken where the kept case is: a short option
    taking the prompt, a region's unknown key taken, a signed-in run given key variables, an
    empty argument taken, a `forbid_env` missing its own variables, a code-loading variable
    taken.

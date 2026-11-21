# pass-doors as built (2026-11-21)

d2 §E steps 4 and 5 for the doors area (`platform/doors/`, its app in `platform/doors/app/`),
both passes in one PR (`tasks.md`, the passes line), on the model of `pass-sandbox.md` and
`pass-files.md`. The listing they start from is d2 (`list2_doors.md` §G comments, §H tests),
taken at `fdac25ba4`, so every item was re-read at `origin/main` `1e2100a5c`. Lanes A, B, O and
T had moved most of it since. Behaviour is unchanged. Every non-test source of the workspace,
each minified with esbuild, and the Worker's entry bundled with all it imports, has the same
digest on base and head, so no line of code changed, only comments. The recorders match.

The cutover's import window (`src/import/`, `test/import.test.ts`, `test/import-setup.ts`) is
not passed: `cutover.json` deletes it, as d2 §B skips the importer.

## 1. Comments

**Kept:** vendor facts (E2B's webhook scheme, WorkOS's token claims and refusal codes, the
OAuth provider's CIMD bounds and empty 401, Cloudflare's per-object and body limits, RFC and
MCP spec pointers), research pointers, the decision files that hold a design
(`r8-lost-object.md`, `r8-erased-orphans.md`), and the reason comments: why an order, a bound,
a refusal or a wait is what it is.

**Removed or rewritten:**
- **Provenance tags,** about 100 across the sources, the app's scripts, the config, the guards
  and the tests: `slice L`, `P`, `R`, `S`, `W`, `W2`, `M`, `ID`, `BRAIN`, `SIGNIN`, `APP`,
  `slice 7`, `the app slice`, `the starter slice`, `tasks 2`, `tasks 4 G3` and `G4`,
  `"Owed by 6.1"`, `A6, salvaged`, `ruling H1`, `S14`, `S69`, `R2-M1`, `task #68`, and the
  review ids (`review M5-1`, `L6-1`, `M-5`, `N1`, `E-2`, `H1`, `H3`, `L1` to `L7`, `M1` to
  `M4`, `app review`, `rename review N1`). Test titles that carried one lose it, and
  `hardening.test.ts`'s describes, titled by one review's finding numbers (`L1:` to `L7:`),
  are named for what each tests. Git holds where a line came from.
- **The old stack,** whose code the cutover deletes: `app.ts`'s `mailbox_door._owner`,
  `binder.ts`'s `issuer \0 client`, `inbound.ts`'s "ported from" line, `_json_object`,
  `extract_single_installation` and "as the Python's switch", `session.ts`'s "the Python's
  Lax", and `api-keys.ts`'s port line and `secrets.token_hex` names.
- **Stale:**
  - `api-keys.ts` "The key is the Python's, exactly, so keys imported at cutover keep
    working". The importer drops every key (`import/rules.ts`: "API keys are minted again",
    #5343). The header now gives the format as the doors' own, and the test "keeps a
    cutover-imported key working" is named for what it holds: a key whose entry holds its
    secret's SHA-256 hex, whoever wrote the entry.
  - `app.ts`'s order put the maintenance gate before the body. The body is read first.
  - `index.ts` "Not deployed". The kit deploys it; its checked-in config is what is not
    deployable.
  - `config.ts` "the environment it binds". `env.ts` types the bindings now.
  - `api.ts` "the `Cookie` header is gone (`index.ts`)". The router is `doors.ts`.
  - `wrangler.jsonc`'s `GATEWAY` "serves only the connect steps (seal, exchange, verify)". It
    serves a doorbell's `sealDoorbell` and `verifyDoorbell` too (`d2:N74`).
  - `guards/rules.ts` "a deploy fails until cutover creates them". The kit writes the ids.
  - `directory.ts` "Owed: `a:` published app slugs (S6)". No plan names it: `tasks.md` holds
    no published-app line, and the doors spec serves no published app on the origin.
  - `ring.test.ts` and `signin.test.ts` named `entry.ts` as the fake sandbox. It is
    `ci/fake-sandbox.ts`.
- **Copies of values in prose:** `egress.ts`'s runner heartbeat (20 s) and `storage_busy`'s
  wait (30 s), `file-bytes.ts`'s "4 MiB" twice and `session.ts`'s "eight hours". Each names its
  constant (`HEARTBEAT_MS`, `CHUNK_BYTES`, `SESSION_TTL_S`).
- **Orphaned:** `connect.ts`'s "The connect routes, by their path…", stacked on
  `DOORBELL_VERSION`'s doc, and `serveSync`'s doc, cut off from it by three helpers. Each is
  back on its own declaration.
- **Restating the code:**
  - `app.ts`'s 75-line route list, which restated `routes()` and had drifted from it (no
    `files/upload/abort` or `actions/*`, and the `SURFACE_HEADERS` sentence spliced between
    two routes). What it said that the code does not, which routes need the session (the
    object's rule), stays in the header; each route's own why is its function's doc.
  - `doors.ts`'s route list, which restated `route()` (no `/hooks/e2b/lifecycle`, and the "No
    caller is counted" paragraph spliced into it). The header keeps the egress origin's
    isolation, which doors drop the `Cookie` header, and the no-limits rule.
  - `app.ts`'s two inline notes (the sign-in field glosses in `sourceList`, the export's "an
    object not yet written", which `exportStream`'s doc says), `kit.ts`'s docs of `answer`,
    `plain` and `redirect`, and `api-keys.ts`'s doc of `newKey`.
- **Run-on lines,** past 100 characters or broken mid-sentence, reflowed: `authorize.ts`
  `signedInVendor`, `binder.ts` `mcpOrigin`, `upstream.ts`'s header (`d2` §G), `connect.ts`'s
  header and two one-line docs, `file-bytes.ts`'s step 2, two paragraphs of `session.ts`, and
  `e2b-hooks.ts` and `pages.ts`'s first lines.
- **Added, a reason:** `egress.ts` `OUTCOME_WAIT_MS` (§3).

**Left as they are, each with its reason:**
- `wrangler.jsonc`'s and `guards/rules.ts`'s headers (`d2` §G lists both as restating). Each
  tells its own file's binding story, and `rules.ts` says why each rule is a security
  statement, which its data cannot. As `pass-sandbox.md` kept the sandbox's.
- `authorize.ts`'s eight-step header and `egress.ts`'s four. Each is the one end-to-end telling
  of its door, and the function docs point to it.
- `app/app.js`'s `MISSING.export` ("its door is being built"). The export door exists, but
  the app's Export section does not use it yet. `tasks.md` owes that ("The owner app: … the
  Export section uses the export door, and `MISSING.export` goes").
- `src/env.ts`, which is generated, and the cross-area lines `d2` lists (`tsconfig.json`'s
  `admin/`, `deploy/deploy.ts`). They belong to those areas' passes.

## 2. Tests

| Item | At this head | Decision |
|---|---|---|
| `d2:N81` the config restated in `config.test.ts` and `rules.ts` | `CONFIG` is the allow-list's limits, run by the kit's `configTests`; `SERVICES` lists each entrypoint's members once; the `PLATFORM` pin is gone | Done. |
| `d2:N82` a guard grepping guards for `workerSources()` | No doors isolation guard: the kit's | Done. |
| `d2:N83` `app-files.test.ts`'s literal file list and its grep of `pages.ts` | Present | **Rewritten against what the doors serve** (below). |
| `d2:N84`, `N85` | Fixed by lane T (#5660) | Done. |
| `d2:N86` alias pins | The aliases are gone; `import.test.ts`'s `IMPORT_HOLD_BYTES` pin is the import window's | Done, and the window's is skipped with it. |
| `d2:N87` the code-to-status table re-listed | `refused.test.ts` and `api-keys.test.ts` take each answer from the contract (t2d, #5663) | Done. |
| `d2:N88` reads of the owner's SQL | None: the doors reach the owner as its own Worker (T2a, #5631) | Done. |
| `d2:N89` sleeps | `export.test.ts` and `operator.test.ts`'s 25 ms steps, and `app.e2e.mjs`'s | **Held** by the waits guard (`ci/guards/timeouts.test.ts`): each is a poll that ends on what it reads. |
| `d2:N90` spies on the console | Nine files | **Left.** Some only silence a log the test causes. The others each hold a log line that is a fault's only trace (Fact 8: the operator channel's drops, the upstream sign-in's failure, a tool's answer past its bound), a line the founder acts on (`dcr_refused`), or that no secret reaches a log (`connect.test.ts` reads every level). A log line is the Worker's answer to its operator. |

**Rewritten:** `guards/app-files.test.ts` "are exactly the pages, scripts and style the doors
serve" listed the nine files of `app/` by name, then checked `pages.ts`'s text held each
quoted. A third copy of the list, and a check of source text. It now asks the doors: each of
the identity's three pages, and every other file of `app/` under `/app/static/`, is served
`200` through `servePage`, over an `ASSETS` that reads `app/`, and the files those answers read
are exactly `app/`'s. A file `pages.ts` does not serve, or a file added to `app/`, fails it.
The test held no more before: its list only had to be edited with `app/`.

## 3. The doors' swallowed errors and retries (`d2:N56`, `d2:N57`)

`pass-files.md` and `pass-sandbox.md` left these to this pass: they name only `doors/src/`
files. Each was re-read at this head (B2 had already moved every per-door `Retry-After` but one
into the contract). All are kept, and behaviour is unchanged.

**Swallowed errors, kept, each with its reason:**
- **The egress door** (`egress.ts`):
  - `spendOf`: an outcome line that fails or does not come is `unknown`, which settles the
    reservation whole, the charge that can never be short.
  - The beat's failure: the next beat or the settle carries the reservation, and beats that
    stop are what the object settles a reservation lost by (`EGRESS_BEAT_S`).
  - The cancels and closes of a stream that may have ended already: nothing is left to do.
  - A failed settle is logged: the object settles the reservation as lost once its beats stop.
  - A malformed `x-ta-resealed`: the gateway writes it as JSON, so only a gateway bug drops
    one, and the blob it carried stays under an older kid, which the rotation's `owner.reseal`
    sweep reseals.
  - `egress_gateway_failed` logs no cause. The gateway's `forward` answers every failure of its
    own as a response, so a rejection here is the RPC's (the gateway's isolate lost), and its
    caller is answered `502`.
  - A late answer of a stopped call is cancelled, its failure ignored: nothing was sent for it.
- **`authorize.ts` `erasedSignIn`:** the identity's deletion failing again is logged. The
  erasure tried first, the sign-in is refused either way, and each later sign-in tries again.
- **`oidc.ts` `refusalCode`:** a refusal that is not JSON names no code. The error still names
  the status.
- **`app.ts` `socketUpgrade`:** a failure past the doors answers the contract's fixed
  `SOCKET_FAILED`, logged with no ids and no text, since the error may carry them.
- **The queues** (`erase.ts`, `ring.ts`, `operator.ts`): a message out of shape is logged and
  acknowledged. It names no owner to retry for, and redelivered it would fail the same way
  forever. `erase.ts` also acknowledges a refusal that is neither invalid nor transient: the
  object's word that no erasure is under way.
- Parse-or-refuse readers, which answer and so swallow nothing: `inbound.ts` `envelopeOf` (an
  envelope that is not JSON is a no-op, after its signature is proved), `kit.ts` `jsonObject`,
  `e2b-hooks.ts`'s parse and UTF-8 checks, `events.ts` `argumentsOf`, `app.ts` `fileRead`'s
  UTF-8, `connect.ts`'s key grammar and `config.ts` `foreignIssuers` (which throws).

**Retries and waits, kept, each with its reason:**
- `egress.ts` `settleOnce`: one retry, through a fresh stub, so a settle after the object
  reset (a deploy) reaches its new instance. A refusal is the object's answer and is never
  retried.
- `egress.ts` `OUTCOME_WAIT_MS` (10 s): the gateway writes the outcome line as the answer ends,
  so the wait bounds only a gateway that never writes it, and `unknown` keeps the reservation
  whole. Its doc now says so.
- `erase.ts`'s retry after 60 s: the object's job sends the message again anyway, so the
  queue's retry is a spaced second path, never the only one.
- `ring.ts` and `operator.ts`: the queue's own retry. A ring the queue gives up on is rung again
  by the line's own back-off, and an operator message ends in the dead-letter queue the kit
  reads.
- Every other `Retry-After` is the refusal's code's, from the contract (`refused.ts`
  `retryAfter`).

**Found and left:** `doors.ts` `serve` answers an overloaded owner object with its own
`Retry-After: 1`, in plain text, `429`, on every surface: the egress door's too, whose callers
read `{error: {code, message}}`. It is the one doors answer that is not a contract refusal
answered per surface (B2's rule). Its fix gives the runtime's overload a contract code with its
status and wait, answered by each surface's renderer. That changes an answer, so it is not this
pass: a sub-line of `tasks.md`'s re-check loop line (`untangle/doors-overloaded`).

## 4. Proof

- Every non-test source's minified esbuild digest (`src/`, `app/*.js`, `guards/rules.ts`,
  `vitest.config.ts` and `wrangler.jsonc` without its comments), and the entry's bundle, the
  same on base `1e2100a5c` and head. The app's scripts are served as they are written, so their
  bytes differ by comments alone.
- The recorders: `wire`, `tools`, `rpc` and `rv` are the same.
- Mutations, each failing the rewritten app-files guard: `pages.ts` without `view.js`, and a
  file added to `app/` that `pages.ts` does not name.

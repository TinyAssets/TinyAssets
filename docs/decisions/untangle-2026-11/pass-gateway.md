# pass-gateway as built (2026-11-21)

d2 §E steps 4 and 5 for the gateway area, both passes in one PR (`tasks.md`, the passes
line), on the model of `pass-sandbox.md` and `pass-files.md`. The listing they start from is
v2 (`list2_services.md` §2.1, §2.2, §2.6 and §3's gateway lines), taken at `fdac25ba4`, so
every item was re-read at `origin/main` `1e2100a5c`. Lanes G, T, V, C and the o-keyrings, t3
and test-waits PRs had fixed most of §2.1. Behaviour is unchanged. Every non-test source of
the workspace (`src/*.ts`, `guards/rules.ts`, `vitest.config.ts`, `memory/*`), each minified
with esbuild, and the Worker's entry bundled with all it imports, has the same digest on base
and head, so no line of code changed, only comments. The tests and their fake changed.

## 1. Comments

**Kept:** vendor facts (pi-ai's Google adapters and the global fetch, `AsyncLocalStorage` and
event listeners, workerd's hang detection and fake timers, the engines' 300 s idle cuts, R2's
and Workers' limits), spec pointers (ADR-032, ADR-034, the RFCs, the research sections,
Standard Webhooks, OpenAI's MCP Events page), the measured costs and their sources (`held.ts`'s
header, `weighedBody`'s and `prepared`'s frames), and the reason comments: why a catch
swallows, why an order, why a bound.

**Removed or rewritten:**
- **Provenance tags,** about 50 across the sources, the tests, the fake and the memory leg:
  `slice W`, `ruling H1`, `untangle B7`, `untangle A5`, `untangle v:S5`, `(G4)`, `tasks 4 G4`,
  `A′`, `(M3)` three times, `(L2)`, `(L3)`, "the untangle's decision on S1", the review ids
  (`H1`, `H2`, `V-H1`, `V-H2`, `V-M1`, `V-M2`, `M1`, `M2`, `L1`, `M6`, "review item 6",
  "review round 2" and "round 3", `rveg`, `rveg2`, `slice E review`). Git holds where a line
  came from. A test title that was only a tag is named for what it tests
  (`"the verification's cases (rveg2)"` is "a held call's sender, and its weight past ASCII";
  `"the reviewer's escapes, closed (review round 2)"` is "what a metered call could escape its
  reservation by"). `ci/budgets.json`'s record of the gateway's slowest test names its new
  title.
- **Stale:**
  - `index.ts` "Not deployed". The kit deploys it.
  - `index.ts` `ConnectGateway` "the three connect steps". It serves five: a key's seal, a
    code's exchange, a credential's check, and a doorbell's seal and verification.
  - `failure.ts`'s and `oauth.ts`'s pointers to the Python (`tinyassets/broker/...`), which the
    cutover deletes. The rule stays (provider prose can echo a credential); the key exchange
    is named by its source records' protocol, `pkce_user_key_v1`.
  - `forward.ts` "as the loop settles it", and `meter.ts` "as the loop's calls hand it" and
    "as it parses the loop's": the owner's loop is gone.
  - `meter.ts` `sentHeaders` "the sources guard holds a record's sealed auth header". Its one
    caller is the gateway's own `meter.test.ts`.
  - The memory leg's "b23 research question 5", a working file never in the repo.
  - `fake-upstream.ts`'s header, which documented twelve commands no test sends (§2).
- **28 comments that restate the code removed or cut:**
  - `index.ts`: the eleven method docs, each a copy of its contract's own doc of that method
    (`contract/src/gateway.ts` `ConnectService`, `EgressService`, `GatewayService`);
  - `keyring.ts` `ring`, `domain`, `resealWith`, `seal`; `catalog.ts` `NO_AMBIENT` (the
    header's sentence again) and `declaredProvider`; `oauth.ts` `post` and
    `authorizationGrant`; `doorbell.ts` `pushOf`, `sealDoorbell`, and `verifyDoorbell`'s line
    on `send` (the `Send` type's doc says it); `held.ts` `gatewaySlots`, `weightSlots`;
    `meter.ts` `context`; `forward.ts` `redactText`; `failure.ts` `upstreamCode`, its
    branches in prose; `credential.ts` `bearer`.
- **Run-on lines** (a paragraph patched into a line past 100 characters, or broken
  mid-sentence) reflowed in nine paragraphs: `held.ts`'s header and `readHeld`'s, `egress.ts`'s
  header, `meter.ts`'s header (two) and `sentHeaders`, `forward.ts`'s `IDLE_CUT_MS` and
  `verify`, `failure.ts`'s header.

**Left as they are, each with its reason:**
- The method docs that say what the contract's do not: the default entry's `fetch` (no model
  call over HTTP, ADR-034), `open`'s "with no detail of which", `refresh`'s lease,
  `revokeAtProvider`'s RFC 7009 choice, `exchange`'s two flows.
- `forward.ts`'s and `held.ts`'s long headers. Each is the one place that tells the egress
  path, or the held call's costs and clocks, end to end, and the functions' docs point to
  them.
- `held.ts`'s cost constants' one-line docs: each says which measured phase of the header
  its figure is, which the names do not.
- `credential.ts`'s "Falls through to the one refusal below": the reason for its empty catch.
- `src/env.ts`. It is generated (`npm run generate`).
- `wrangler.jsonc`. Every comment is a binding's or a flag's reason.
- The contract's own comments that v2 lists beside the gateway's (`contract/src/api-rules.ts`,
  `slots.ts`, `gateway.ts`, which still carry `slice W`, `G4`, `review round 2`,
  `rveg_report.md`). They are the contract area's pass.

## 2. Tests

| Item | At this head | Decision |
|---|---|---|
| `v2:G-T1` the fake's dead commands | Twelve `cmd:` families with no sender anywhere in `platform/` (`fail-*`, `redirect`, `redirect307`, `tool`, `call-*`, `ta-*`, `delegate-*`, `drip`, `overflow[-id]`, `streamoverflow`, `tpm`, `measured-*`), for the owner's loop that is gone | **Removed,** with what only they used: `anthropicTool`, `codemodeScript`, `TA_SYSTEM`, `TA_MAX_TOKENS`, `DRIP`, `DRIP_MS`, each format's `overflow` body, `model`'s `input` and the echo's `x-ta-envelope` field. 584 lines to 417. `cmd:echo` (and no word at all) and `cmd:slow-<id>` are what the gateway's tests send, and the owner suite sends no `cmd:` word at all; every answer it reaches is unchanged. |
| `v2:G-N4` the owner suite loads the gateway's fake | Present | **Kept.** The owner suite runs the real gateway beside it, and the gateway is the one Worker that fetches out, so its outside world is this one fake. The header now says both suites load it. |
| `v2:G-T2` `todayClamped` | A copy of the clamp, the fuzzed corpus's oracle | **Retired.** It checked the clamp against a copy of itself. What the clamp does is `meter.test.ts`'s (every alias, the usage fields, an unpriced default, a cap that is not a number). What stays is the one property only this file held: the clamp works on the parse itself, never a copy (the memory leg's premise), now asserted on every API. |
| `v2:G-T3` constant and formula pins | `GATEWAY_POOL_SLOTS` pinned to 16; `parseWeight`, `MIN_RATE` and `heldDeadlineMs` restated as their formulas | **Retired.** The pool's ceilings (an ASCII 16 MiB body fits, 15 MiB with a non-ASCII character fits, 16 MiB of it does not, 4 MiB of empty objects does not) and `weightSlots`'s unit edges stay: they are what the figures are for. `heldDeadlineMs` keeps its bound, the door's largest body's upload under two minutes (the limits audit's 100 s). |
| `v2:G-T4` wall-clock and timeout literals | Fixed by test-waits 1 and 2 (#5605, #5612), but three polling loops copied `until` (`entry.test.ts` twice, `forward.test.ts`) | **The loops are `until`'s** (`ci/workerd.ts`). Two elapsed checks stay, each a liveness bound only: `entry.test.ts`'s stopped call (past it the fake answers `late`, which the next line fails) and `held.test.ts`'s refusal at its weight (past it the sender's deadline answers `invalid_request`, which the line before fails). |
| `v2:G-T5` `Hold` on an injected `SlotPool` | V-M2 and the two wait-die tests made pools of their own figures | **On the isolate's own pool** (`gatewaySlots()`), as a call is held. The burst ends inside the pool's own wait (`SLOT_WAIT_MS`); V-M2's blocker holds all but one unit, and its second unit's wait runs to just short of the pool's wait, so the rest arrives past the sender's deadline counted from the first grant. No test passes `Hold` a pool now (§3). |
| `v2:G-T6` tests of dead surface | The default entry's extras and the plain `forward()` are gone (R4, #5497) | Nothing to do. `sentHeaders` is §3's. |
| `v2:V2-T2` owner tests seal through the default entry | Through `CONNECT` and `CONNECT_OLDER` (lane T, #5643) | Done. |
| Copies of the tests' own helpers | `responsesModel` twice, the Anthropic model's finder three times and the Google one once, `granting` and `posted` twice, `outcome` twice | **One home,** `test/support.ts` (`meterable`, `granting`, `posted`, `outcome`). |

**Consolidated:**
- `entry.test.ts` "forwards a sandbox request on the egress entrypoint alone" loses
  `Object.hasOwn(Gateway.prototype, "forward")`: "gives each caller's entrypoint only its own
  methods" asserts each entrypoint's exact method list.
- That test loses its verify, its other owner's refusal and its exchange of a source with no
  OAuth client: "verifies, refreshes and revokes over RPC with fixed codes" makes the same two
  verify calls, and `oauth.test.ts` "refuses a source with no OAuth client" the same refusal,
  which "exchanges an OAuth code over RPC" shows crossing RPC as its fixed code.

**Fixed, checks that could never fail:**
- **The doorbell's echo bound.** Its case past `ECHO_MAX_BYTES` was 1,025 bytes of `x`, which
  is not JSON either, so a verification read without its bound still failed it. The case is
  now the right challenge, as JSON, padded past the bound. Read without the bound it
  verifies, and the test fails.
- **Two of the OAuth redirect checks.** `evil.test` records each request's URL and headers,
  never its body, but the token endpoint's and the revocation endpoint's checks looked for
  the code (`redirect-code`) and the token (`rt-redirect`), which travel in the body. Each now
  looks for the URL a followed redirect reaches (`evil.test/token`, `evil.test/revoke`), as
  the refresh's and the key exchange's already did. With `redirect: "follow"`, the old check
  passes and the new one fails.
- **A plaintext that no longer fits, at open.** "refuses a blob whose plaintext no longer
  fits the credential shape" said it sealed one "by hand around the checks", but it sealed
  through `seal`, which refuses it first, so no open was ever tried. It now seals the misfit
  with `sealValue` and no check, and `open` must refuse it. An `openValue` that skips its
  check fails it.

**Left:** `meter.test.ts`, `credential.test.ts` and `metered-body.test.ts` test much of the
contract's `api-rules.ts` and `egress-scope.ts` (`freeCall`, `paidTool`, `aliased`, `source`,
`allows`). They are where the gateway's use of those rules is shown, against pi-ai's real
adapters; where they belong is the contract area's pass.

## 3. The problem index for `platform/gateway/`

`d2:N56` and `d2:N57` name only `doors/src/` files, and `s2:N51` is the sandbox's
(`pass-sandbox.md` §3), so none is this area's. The gateway's own list2 items are v2's:

| Item | At this head | Decision |
|---|---|---|
| `G-S1`, `G-S2`, `G-H1′`, `V2-ST3` | The default entry serves only `GatewayService` (`reseal` is in the contract); the plain `forward()` is gone (R4, #5497; T, #5643) | Done. |
| `G-S3` test-only parameters | `deadlineMs`, the nullable `stop` and `clamped`'s optional `api` are gone. Left: `Hold`'s pool (every caller passes `gatewaySlots()`), `weighedBody`'s and `readHeld`'s `hold: Hold \| null` (no caller passes null), the doorbell's `send` (only `doorbell.test.ts` passes one) | **Owed,** a code change: a sub-line of `tasks.md`'s re-check loop line. |
| `G-S4` `GatewayEnv = KeyringEnv` | `GatewayEnv` is generated; `KeyringEnv` is `Pick<GatewayEnv, "GATEWAY_KEYRING">` | **Kept, not surplus:** it says what `keyring.ts` and its callers need, the one secret, however the Env grows. |
| `G-S5` exports with no reader | Most are gone. Six are left with no reader outside their file: `DoorbellSecret`, `Metered`, `BYTE_COST`, `ASCII_BYTE_COST`, `TOKEN_COST`, `MIN_RATE` (the last four lost their one reader with `G-T3`) | **Owed** with `G-S3`. |
| `G-S6`, `G-N3` `sentHeaders` | Product code whose one caller is `meter.test.ts`, which reaches `sources/src` for the records | **Owed** with `G-S3`: the property is the source records' (their sealed header and scheme), so its home is `sources/`, against pi-ai's adapter. |
| `G-S7`, `G-H11`, `G-H13`, `G-S8`, `G-SP5` | One per-API table in the contract; `MISTRAL` derived; the SDK pins gone; a declared provider is picked by its sealed models (lane G, #5621) | Done. |
| `G-H12` helper copies | `object`, `secret`, `scope` are the contract's; one bounded reader (`answer.ts`); one `refusedOf`; `open` is `openValue` | Done. |
| `V2-H1`, `V2-H2`, `V2-H3`, `V2-S1` | The bindings are the contract's ids (o-keyrings, #5678); `ringRequest`, `doorbellInput` and `metering` are the contract's checkers (V2, #5543); `HELD_CODES` is gone | Done. |
| `G-SP4`, `G-SP7` | The refresh's second check is gone (V2); one learned meter per model per call (`Meters`, V4, #5546) | Done. |
| `G-SP3` `exchange` dispatches on `key_exchange` | Present | **Kept:** the contract has two source shapes, `source` and `keyExchangeSource`, and `key_exchange` is the field that tells them apart. One tagged checker would be a contract change. |
| `V2-N1`, `V2-N3`, `V2-D14` | The `outside` list is gone (the layer table, C1); the module-state exemptions are `rules.ts`'s `MODULE_STATE` (C5, #5553) | Done. |
| `V2-N2` | No owner test or probe reaches `gateway/src` (T, #5643; t3, #5668) | Done. |
| `V2-D5`, `V2-D6` the gateway's stale comments | "Not deployed", "the loop", the Python; the owner's MCP client line was gone | Done here (§1). |
| `G-H5` | `doors/src/connect.ts`'s message | The doors area's. |

**The gateway's swallowed errors and retries, each re-read.** It has no retry: a repeat is its
caller's, and pi-ai is asked with `maxRetries: 0`. Its timers are the pool's design
(`v2:G-SP8`): the reserve's wait, each unit's sender deadline, the upload's `heldDeadlineMs`,
the idle cut and the forward deadline. Kept, each with its reason beside it:
- **Fixed codes, never the text** (`failure.ts`): `guarded` lets only a gateway code out;
  `credential` refuses any misfit as `credential_refused`; `bound` any bad owner as
  `invalid_binding`; `openValue` any open or check failure as `credential_refused`. Each is
  loud, a refusal, with no detail of which (Fact 8 holds: none is a fallback).
- **The egress path** (`forward.ts`): the open's refusal and every end before the fetch answer
  their code and settle `failed`; the meters' throw is `internal`; a fetch that throws is
  `deadline` or `upstream_unreachable` and settles `unknown` for a metered call, which keeps its
  whole reservation; the outcome's rejection is `unknown`; the upload's write after the
  provider ended, and its abort, have nothing more to send.
- **The outcome line** (`index.ts`): a door that stopped reading it ends the write.
- **Reads** (`answer.ts`, `held.ts`, `doorbell.ts`): a reader's cancel after its end or error
  has nothing to release; a body that is not JSON is no answer; a held body that fails or is
  not UTF-8 is `invalid_request`; `senderLate`'s own rejection is caught so a race that
  settled first leaves nothing unhandled.
- **The doorbell:** an open refused (a 4xx refusal) is the wake `refused`, anything else
  rethrows (`v2:G-SP2`); a send that throws is `unknown` (a byte may have left); a
  verification that throws is its category (`timeout`, `connection_refused`).
- **OAuth:** an unreachable endpoint is `oauth_unreachable`; a value that would not seal is
  `oauth_malformed`.
- **The meter:** pi-ai's refusal of the learning run is its stream's error or a throw, the
  same; an answer pi-ai cannot read to its `done` is `null`, settled `unknown`.

## 4. Proof

- Every non-test source's esbuild minified digest, and the entry's bundle, the same on base
  and head.
- The recorders: `wire`, `tools`, `rpc` and `rv` are the same.
- The suite: 383 tests on base and head (none removed; the fuzzed oracle's test is the clamp's
  identity test).
- Mutations, each failing its test:
  - the verification's answer read with no bound (the echo past `ECHO_MAX_BYTES`);
  - the token and revocation posts with `redirect: "follow"` (the two redirect checks; the old
    check of the code passes it);
  - `openValue` without its check (the misfit sealed around the checks);
  - `readHeld` not restarting a unit's clock at its grant (V-M2, on the isolate's pool);
  - every growth refused `busy` (the closed loop, on the isolate's pool);
  - `clamped` on a copy (the identity test).
- A mutation that lets every holder wait to grow, not only the oldest, passes the gateway's
  wait-die tests on base and head alike: they hold the pool's throughput under load, not the
  rule. The rule is the contract's pool's, the subject of `owner/test/slots.test.ts` "lets only
  the oldest holder wait to grow (wait-die)".

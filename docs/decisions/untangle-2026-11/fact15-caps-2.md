# fact15-caps-2 as built (2026-11-22)

Owed by `fact15-caps.md` (#5781) and `tasks.md` item 11: three caps with no Fact 15 kind or
reason, `openPerRun`, code mode's limits taken from pi's defaults, and the gateway's 300 s
idle cut ("made data per engine", founder call 11). Fact 15: the only limits are the tier's
runs and storage, the owner's own settings (off by default), vendors' limits and recorded
technical ceilings, each with its reason. Research first (Fact 14), read at `c21c166d0`.

## Research

### `openPerRun`

- **Definition and readers.** `owner/src/egress.ts` `EGRESS_LIMITS.openPerRun`, 16. The
  egress open refused `egress_rate` (429, transient) once the owner's open rows reached
  16 times its tier's runs (free 80, paid 160), and refused it too when no tier numbers were
  kept (`tierRuns`, wired in `owner-do.ts` for this alone). The owner-bound guard costs
  `egress_reservations` at 16 times `RUNS_CEILING` (16,000 open rows).
- **What it protects.** The rows: each open call is one, and nothing else bounds how many a
  sandbox opens (free reads and admitted-but-unreserved calls carry no charge). So it is the
  10 GB's ceiling, not a tier limit. The owner's object's other load per open call is a beat
  a minute (`EGRESS_BEAT_S`), 267 a second at 16,000, inside what one object serves.
- **Its number.** "An engine's clients stream a few": no derivation. Claude Code 2.1.309,
  read from its linux-x64 binary: `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` defaults to 20
  (`j3r=20`), `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` to 10, and the subagent cap is skipped
  for workflows (anthropics/claude-code #92311, a workflow ran 7 agents under a cap of 3). So
  one Claude Code run streams its main loop and 20 subagents, 21 calls, past 16; five such
  runs (105) passed the free tier's 80, refusing a run its engine's own defaults allow.

### Code mode's limits

- **What code mode is now.** The MCP door's `code` tool (`owner/src/codemode.ts` `codeRun`),
  served to any caller whose profile holds it, outside clients and runs alike, off by default
  until H2 passes (`codemode-off.md`). pi-codemode 1.4.0 is its library (the VM, QuickJS in
  the codemode Worker's per-execution object), not an engine. Its limits still apply to every
  `code` call.
- **What came from pi-codemode** (its tarball, integrity equal to the lockfile's, read
  2026-11-22): `DEFAULT_INTERRUPT_BUDGET` 100,000 polls ("about 10 seconds on Cloudflare",
  README "Limits on Cloudflare"; ours ten times it), `DEFAULT_REMOTE_MEMORY_LIMIT_BYTES`
  32 MiB (ours equal; "All VMs in one isolate share its 128 MB"), and `DEFAULT_TIMEOUT_MS`
  300,000 (`runtime/execution.js`; ours equal, rowed a vendor limit "the codemode Worker's
  `cpu_ms`", which bounds CPU, not the wall time it is).
- **Interrupt budget.** Protects the Worker's `cpu_ms` (300 s, Paid's most): a script that
  reaches it resets the object instead of failing alone ("Keep the budget below the Worker's
  `cpu_ms` limit"). The budget counts polls over the whole execution (`inlineLauncher`).
- **Memory.** Protects the isolate's 128 MB, which every VM Cloudflare places in it shares
  (objects of one class share an isolate, ADR-032): a VM at its own cap fails inside its
  script (`InternalError: out of memory`). How many objects an isolate runs at once is
  Cloudflare's placement, not documented.
- **Wall time.** No cancel reaches a script whose caller left: the doors call the owner's
  `codeRun` over RPC with no signal, and the script's own `late` abort fires only at the
  deadline. Until it ends, its VM holds memory in the shared isolate and its tool calls go
  on. How long callers wait for one tool call: the connectors 60 s to about 5 minutes as
  reported (`SLOT_WAIT_MS`'s row), the MCP SDK's `DEFAULT_REQUEST_TIMEOUT_MSEC` 60 s, pi
  1.4.0's MCP client 60 s (`docs/mcp.md`), Gemini CLI 0.66.0's `MCP_DEFAULT_TIMEOUT_MSEC`
  10 minutes.

### The 300 s idle cut

- **Definition and readers.** `gateway/src/forward.ts` `IDLE_CUT_MS`, read only by
  `abortable`: a body silent that long was errored `deadline` and the provider's request
  aborted, "so a stalled stream lets go of its run's slot" (the `openPerRun` slot). Its
  reason was the engines' own cut, copied.
- **Every engine already cuts.** Claude Code 2.1.309: byte-stream idle `max(stream idle,
  300000)`, 180 s first-party (`CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS`). Codex: 300 s
  (`DEFAULT_STREAM_IDLE_TIMEOUT_MS`, cited at #5497). Gemini CLI 0.66.0: the global fetch
  dispatcher's `bodyTimeout` 3e5 (`chunk-HJ5KJXNZ.js`). pi 1.4.0: `httpIdleTimeoutMs`
  300,000, header and body (`docs/settings.md`). This closes `tasks.md`'s "Gemini CLI's and
  pi's idle cuts, researched".
- **The engine's cut reaches the provider.** The engine's leaving aborts the door's request
  (`doors/src/egress.ts`: `request.signal` and the stream's cancel stop it `aborted`, cancel
  the gateway's answer and settle the reservation); the gateway's cancel aborts the
  provider's request. Tested at the door (`egress.test.ts` "settles a call whose client left
  before any answer") and now at the gateway. So the gateway's cut fired only when the
  engine's would have at the same moment: a copy.
- **Any other caller** (a script of the agent's in the sandbox) is the owner's own code: its
  silent call ends with its run's lease (a run's settle closes it, which ends its streams), a
  policy write, or the door's day (`EGRESS_DEADLINE_S`); its open row is one of 16,000.

## Decided

1. **`openPerRun` goes; one owner-wide technical ceiling, `EGRESS_LIMITS.open`, 16,000.**
   The 10 GB is its fact (guard term `egress_reservations`); the number is the room the guard
   already held (16 at each of `RUNS_CEILING`'s runs), so the term and the 89.4 MB are
   unchanged, and it is far past one owner's use (the paid tier's 10 Claude Code runs, 210).
   Owner-wide, any one run may use what the others leave, so fewer calls are refused than
   under the tier's scaling at every tier up to `RUNS_CEILING`. `tierRuns` and its "none
   kept" refusal go (nothing else read them; the egress open is reached only under a lease a
   run opened). The refusal stays `egress_rate` (429, `Retry-After: 1`): a wire code, and
   right for a wait.
2. **Code mode's three limits are technical ceilings, each with its derivation**
   (`limits-audit.md` rows, the sources' comments):
   - the interrupt budget under `cpu_ms`, a third of it at pi-codemode's measured rate, so a
     rate three times slower still ends the script first; H2's probe 1 measures it live;
   - the VM memory a quarter of the shared isolate, so a VM fails alone; H2 gains probe 6
     (`deploy/h2-probes.ts`): eight scripts at the cap at once, each to end by its own value
     or its own memory, before code mode turns on;
   - the wall time, 5 minutes: the only end of a script whose caller left, at the longest a
     connector is reported to wait. The audit's "vendor limit, `cpu_ms`" is corrected. Gemini
     CLI and Claude Code wait longer; a script for them past 5 minutes ends `timeout` with
     its output and calls, loudly. When a caller's leaving reaches the script, the wall bound
     becomes each caller's own wait (owed below).
   None is an owner setting: each protects other owners' VMs in the shared isolate or the
   object, which no owner may trade away.
3. **The idle cut goes** (`IDLE_CUT_MS` and its timer). Its protection is each engine's own
   cut, a vendor limit, recorded in `limits-audit.md`'s "Left to the engines". "Made data
   per engine" (call 11) is met by the engines themselves: each one's number lives in the
   engine its record pins, so a record bump brings its own; a copy in the records would have
   no reader the engine itself does not already serve. (Departure from the call's wording,
   for the reviewer: the per-engine data exists, in the engine.)
4. **Tests.** The open bound: one run's calls far past its old 16, only the owner's count
   binds (`owner/test/egress.test.ts`). The gateway: a silent answer, read and metered, is
   not cut after an hour and the caller's cancel ends the provider's request
   (`gateway/test/forward.test.ts`, `entry.test.ts`), replacing the two cut tests; "never
   cuts a metered answer whose every byte comes inside the idle cut" goes, with nothing to
   hold. Specs: `gateway` (no silence cut, its scenario the caller's cancel), `owner-object`
   (the owner-wide count).

## Found and left

- The refusal code `egress_rate` names a rate where it is a count: kept, a wire code.
- `pass-gateway.md` and `untangle-2026-11-remaining-build.md` name `IDLE_CUT_MS`: shared
  logs of what was built then, only corrected.
- Found in review: `EGRESS_LIMITS.open` bounds memory as well as rows. Each open call keeps its
  end signal in `Egress.#live`, in the owner isolate the counts test bounds, and the old tiers
  held that to 80 or 160. Read in workerd with a held `IdentityTransformStream` returned over
  RPC: about 400 B of heap a call, 6.1 MiB at 16,000, inside the counts test's 51.3 MiB room
  for one object. It is named in its `UNCOUNTED`, owed below.

## Owed

- [ ] The memory leg measures an open egress call's end signal, and the counts test counts
  `EGRESS_LIMITS.open` of them for each owner object (`untangle/egress-open-memory`).

- [x] Tick `tasks.md` item 11's `fact15-caps-2` clause and `fact15-caps.md`'s owed line, and
  drop `egress_reservations` from the `RUNS_CEILING` row's per-run terms, once #5781 merges
  (`untangle/fact15-caps-2`): done after merging main with #5781; the `open` row gains its
  `Share:` clause.
- [x] A code mode script's caller leaving reaches it (a stop across the door's call into the
  owner's object, as the egress door's end signal), so the wall bound becomes each caller's
  own wait, researched first (`untangle/code-caller-leaves`).
- [ ] live: H2's probes 1 and 6 hold the interrupt budget's and the VM memory's derivations
  (a tight loop ends `timeout`, never an object reset; eight scripts at the cap each end
  alone), before code mode turns on.
- [ ] live: at the dark redeploy, each engine's own idle cut reaches the egress door: a stream
  its provider stalls past it settles `aborted` and ends the provider's request (`tasks.md`
  §10 step 3).

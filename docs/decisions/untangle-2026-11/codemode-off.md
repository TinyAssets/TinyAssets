# codemode-off as built (2026-11-21)

Founder call 10 (`founder-answers.md`): code mode is off by default until its isolation probe
(H2, `tasks.md` §5) passes, and only a later change, on H2's pass, turns it on.

## 1. Where the switch lives: the owner's policy, off by default

Code mode already had one switch, the owner policy's `codemode.on` (`CODEMODE_DEFAULTS`,
`contract/src/policy-document.ts`). Every path reads it through `codemodeSettings`: the doors'
tool list (`toolProfile`), the `code` door (`codeRun`), each tool call of a script
(`scripted`), and the owner app's toggle. Its default was `on: true`. It is now `on: false`.

The control record was the other candidate, and it was not taken:

- It would be a second switch beside the owner's for the same thing.
- H2 has to run code mode. Its probes call `/api/tools/code` as two probe owners
  (`deploy/h2-probes.ts`), so a platform-wide off with no way around it would block the probe
  whose pass is the condition for turning code mode on. With the owner's switch, each probe
  owner turns code mode on in its own owner app first (`checklist.md`, mode `h2`). H2 is step
  8.1 of the live stage. Only canary owners are admitted until the gates open at Step 14.
- Turning it on for every owner is still one data change, as the founder asked:
  `CODEMODE_DEFAULTS.on`, with `CODE_MODE_OFF` beside it. The refusal's reason names H2, so
  the two values change together, and their comment says so.

`limits-audit.md` lists it as an owner setting, with H2 as its reason (Fact 15: each limit
with its reason).

## 2. A request for code mode while it is off is refused, saying why

- **`codeRun`** refuses `code_mode_off` (terminal, 403) with the reason: "code mode is off for
  this owner. It is off by default until its isolation probe (H2) passes; the owner turns it
  on in the owner app". Each call inside a running script is held to the same check, as
  before. Both read it from the profile's own `withheld` (`codeServed`, review), so a caller
  whose profile does not hold `code` is `tool_not_served`, never told to turn on what would
  not serve it.
- **`toolProfile`** answers `withheld` beside `tools`. That is `code` when the caller's
  profile holds it and the switch is off. The doors list only `tools`, so no MCP client or
  engine is offered a tool it cannot use.
- **`/api`** calls a withheld tool's door, so an API key's `code` call is answered with the
  object's own refusal (403 `code_mode_off`, with the reason) instead of `404 no such tool`.
  This matters most for H2's own probes: a probe owner who has not turned code mode on reads
  why in the probe's row. The doors hold no copy of the refusal or of the switch. A tool
  outside the profile is still `404`.
- **MCP** serves only listed tools, so a `tools/call` of an unlisted `code` gets the SDK's
  "not found". A client is never offered `code` while it is off, so it has no reason to call
  it. Registering a tool that only ever refuses would offer what is off. Left as it is.

## 3. The owner app's copy of the default goes

`doors/app/app.js` drew the toggle as `document.codemode?.on !== false`, a copy of the old
default. Flipping the default would have shown code mode on while it was off. The policy's
projection (`policyRead`, `policyWrite`) now carries the settings in force,
`codemode: codemodeSettings(document)`, and the app reads that. The two places that built a
projection now share `projected()`.

## 4. Docs and tests

- These now say code mode is off by default: the doors and owner-object specs, `tasks.md`'s
  H2 line (whose probe owners turn it on first) and call 10's line (ticked), `h2-probes.ts`'s
  inputs, and the comments of `policy.ts`, `codemode.ts` and the tests.
- Tests that relied on code mode being on by default turn it on (`onRig`, `o.policy(true)`),
  or expect `code` withheld.
- The tools recorder records `custom` and `run` with code withheld (the default), then
  `custom-code` and `run-code` after the owner turns it on, in place of `*-nocode`.
- The CSRF test writes the opposite of the switch in force, so its write is still a real
  change.

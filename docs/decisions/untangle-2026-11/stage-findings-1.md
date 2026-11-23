# stage-findings-1 as built (2026-11-23)

Findings from the founder's first live use of the stage (`stage.tinyassets.io`, opened to the two
stage accounts on 2026-11-23), and one from the live keyed canary the same day.

## 1. A fresh `dark` wrote a record that starts no sandbox

**Found.** `platform/deploy/control.json`, the record `dark` writes when none is held, held only
the gates and the tiers. The sandbox Worker's `#control()` (`sandbox/src/control.ts`) answers
`sandbox_unconfigured` while `sandbox_runtime_ceiling_s`, `sandbox_concurrency_ceiling` and
`sandbox_renewal_window_s` are absent, so "Sign in to Claude" (`POST /app/api/signin/start`)
answered `{started: false, code: "sandbox_unconfigured"}`, and every run would have too. No step
of `platform/deploy/checklist.md` set them. The stage was unblocked by hand with mode `control`:
3,600, 20 and 600.

**Research (Fact 14), from the repo and E2B's public spec; no E2B call.**
- **The team's plan.** Not verified anywhere. `e2b-resume-measurement.md` says "which plan this
  account is on was not checked". What the repo does record points to Hobby: "raising E2B's tier"
  is still listed as a founder step not yet taken (`tasks.md`, ADR-039 decision 6), and the
  template's free disk when made, 10.7 GB (`limits-audit.md`, the disk row), is 10 GiB, Hobby's.
  Slice S's plan named E2B Pro as a founder action (`engines-replan.md`), and nothing records it
  taken.
- **Whether E2B's API gives the plan's limits.** E2B's spec (`e2b-dev/infra` `spec/openapi.yml`,
  read on main 2026-11-23) has `GET /unstable/teams/{teamID}/limits` (`maxLengthHours`,
  `concurrentSandboxes` and more). Its security is a dashboard user's bearer token with the team
  header, or an admin token, not `ApiKeyAuth` (`X-API-Key`), the one credential the kit and the
  sandbox Worker hold, and it is `unstable`. With the API key, the team endpoints answer only
  metrics (`/teams/{teamID}/metrics`, `/metrics/max`): counts, not ceilings. So nothing the
  platform holds can read the plan's numbers. They are data.
- **The renewal window the design wants.** No value is set anywhere. The contract calls it "the
  founder's choice of how long a turn may run on before a renewal cuts into it"
  (`contract/src/control.ts`), and the spec bounds it only below the runtime ceiling
  (`parseControl`). `renew-deadline.md` and `sandbox-probe.md` fix the edge (`RENEW_EDGE_MS`,
  1,405 ms) and a whole renewal (`RENEWAL_MS`, about 10 s), not the window. A quiet moment is any
  moment between a CLI's model calls (`runner.ts` `quiet`), so the window need only outlast the
  longest model call for a quiet moment to come. A look waits for one at most an alarm's 15
  minutes (`ALARM_WALL_MS`), the next alarm waiting on. A forced renewal at the edge keeps the
  process (paused with its memory); only a model call in flight across a pause is unmeasured
  (`renew-deadline.md` §4, owed to the live test). The stage's 600 s is kept as the founder's
  choice the contract names: no model call's length is measured, and nothing measured argues
  for another value.

**Decided.**
1. **The dark record carries the three numbers at E2B Hobby's, the lowest plan's** (3,600 s, 20,
   and the window's 600 s). They hold on any plan. On a larger plan they understate it (a renewal
   each hour, the line waiting past 20), never overstate it, so nothing is refused by E2B. Hobby
   is also the plan the repo's records point to. The record is still the one home of the numbers:
   the founder raises them with mode `control`, no deploy, as the spec says.
2. **Not taken: `dark` refusing to finish without them.** That adds an input to every first
   deploy for numbers the kit cannot check against E2B (above), so a typo would pass anyway. The
   record cannot carry a reason inline (`parseControl` takes exact keys), so the reasons are in
   the checklist's step 5 and the limits audit's two rows, which now name the dark record's values.
3. **Guard:** `deploy/guards/deploy.test.ts`, "writes the dark record": the record `dark` writes
   parses with its sandbox numbers present. Mutations: dropping one number fails it (the record is
   malformed whole); dropping all three fails it (`sandbox` null).

## 2. The owner app hid a start's refusal

**Found.** `doors/app/app.js` `say()` writes every action's answer in the notice above the page.
The Model sources sign-in button sits well below it, so the owner saw nothing happen. The notice
also printed the raw code: "The sign-in could not start (sandbox_unconfigured)". A run that
failed for a start code said only "(the run failed)" in Talk.

**Decided.**
1. **The notice stays in view** (`app.css`, `#notice` sticky at the top of the view): the one
   fix behind every action's answer, wherever its button is, with no per-action slot. It was
   already announced (`role="status"`, `aria-live="polite"`).
2. **One map of start codes to words** (`NOT_STARTED`, keyed by `START_CODES`), used by the
   sign-in's refusal and by Talk's run line and reply (`runWhy`: the run's own error, else its
   start code's words). The sign-in's own `sandbox_waiting` special case went into it. A code
   outside the map is shown as it came.
3. **Test:** `doors/test/app.e2e.mjs`, "a sign-in that could not start says why in plain words,
   in view of its button": a 400 by 500 window, the page scrolled to the button, the fake
   sandbox scripted to answer `sandbox_unconfigured` (a harness door, `e2e-entry.ts`
   `/script/<owner>`). Mutations: the notice not sticky fails it ("out of view"); the raw code
   restored fails it.

**Left.** Past runs shows `state (code)` in its table, a record of codes. It is not an action's
answer, and words there would not fit a cell.

## 3. An app-minted API key cannot touch files (research only: its own lane)

**Found** by the live keyed canary (platform-canary run 45132009951): `file_write answered 403:
this outside identity is not one enabled agent`, and `file_read` of the full run's record the
same. `keysDoors.keyMint` (`owner/src/keys.ts`) always sets the key's grant `scoped`; a scoped
grant acts in the command center of its origin's one enabled registered agent
(`owner-do.ts` `#universe`); a key minted with no registration has none, so every scoped call is
refused.

**What the spec and decisions say.** The spec does not settle a key's grant mode. It says an MCP
client's first call records it `full`, that `scoped` is a grant "the owner narrowed"
(owner-object spec, `OWNER_TREES`), and that an API key agent's registration is made by the
owner's browser naming the key (`/app/api/outside-agents`, built and tested in
`doors/test/api-keys.test.ts`, but offered nowhere in the app). The API keys screen asks for a
space, an agent and capabilities, and mints scoped triples from them, so the space it asks for is
never where the key acts: `#universe` reads the registration, not the triples.
`registered-agent.md` moved the acting agent onto the grant and left the universe on the
registration.

**Options, for a design choice the spec leaves open.**
- **A.** A key minted with no capability ticked is `full`, as an MCP client's default; ticked, it
  is scoped. The canary's key (every tool, the default policy) would work at once.
- **B.** A scoped grant acts in the universe its own triples name (one universe a grant), as
  `registered-agent.md` did for the agent, so `#universe` stops reading the registration. The
  screen's space then means what it says. It changes every scoped grant's semantics.
- **C.** The app registers the key as an agent at mint (`outside-agents`, the space as its
  command center), the one step the spec names. It needs a display name, a delivery method and a
  recipe from the screen.

Not built here: each needs that choice, and B or C passes this PR's size.

- [ ] An app-minted key acts as the spec settles (A, B or C), the canary's checklist step with it: `untangle/app-key-grant`.

## Seen, not chased

"This authorization was not started in this browser; start again" appeared once on the
founder-stage's first sign-in. It is not platform text (`doors/src/connect.ts` has only the
"connection" variant), so it is likely AuthKit's own. Recorded as seen.

## Owed

Finding 1 owes nothing live: the stage's record already holds these numbers (set by hand), and
`dark` writes the record only when none is held.

- [ ] live: after the next deploy of the doors, the stage's owner app shows a sign-in's answer under Model sources in view of its button, in plain words.

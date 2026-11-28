# template-pin as built (2026-11-28)

`land/template-pin`: the sandbox template moves from `s8661fc8113e20662` (#5752) to
`tinyassets-sandbox:sb31f0bce0edffd2a`, pi 1.6.0 with each engine's model arguments as written
(#5858). One founder-approved `sandbox-template.yml mode=build` on main f81d40aa6 (run
46112336531) built it and passed every standing check on throwaway sandboxes, the nine pi route
probes among them. `passed.json` is that run's `passed-json` artifact byte for byte, and
`sandbox/wrangler.jsonc` pins the tag. A moved pin adds a forward change naming the sandbox
(`deploy/guards/forward.test.ts`), so this is the first entry in `platform.json`
`forward.changes`.

## What the forward change does (Fact 14), read from `deploy/deploy.ts`

- **The next stage deploy** (`dark` or `routes`) tags the sandbox's version `-f1`
  (`versionFlags`). The version it serves now is tagged with no forward count (none was ever
  written before this entry; an untagged one counts as unknown, which goes at once too), so the
  sandbox goes to 100% at once, with no 0% step (`atOnce`). Its plan line ends "since the run's version
  is marked -f1 and its version before with none". The rollout record holds the sandbox's
  `forward: 1` and every other Worker's `0`.
- **A rollback of that run** goes callers first: the doors, then the owner, then the sandbox. It
  stops at the sandbox (`forwardOnly`). The doors and the owner go back. The sandbox stays on the
  run's version, and so do the Workers deployed before it (gateway, files, codemode), each named
  in the plan. If the owner is also forward-only in that run, only the doors go back. If the doors
  are (a secret the run sets on them, say), nothing goes back and the kit says "nothing can be
  rolled back". In every case the way out for what stays is to roll forward with a fix.
- **Later runs.** A run whose sandbox version before is already `-f1` is staged as usual (0%,
  then 100%). Its rollback can go back to an `-f1` version. Only going below this version is
  refused, and only by the kit: a rollback by hand (`checklist.md`) is not checked.
- **This is what the stage wants.** The pair check passed one combination: this image with this
  tree's runner and probes (`pairTag`). An older sandbox Worker would resume sandboxes created
  from this image with its own older runner and engine records, a combination no check passed.
  Refusing that rollback is right.
- **No gap that this change owes.** The other direction is a new Worker resuming a sandbox made
  from the old image. The stage owner has one. Its row records no template, so this Worker
  adopts its own runner where nothing is running and resumes it on the old image's engines. Moving
  an owner's sandbox to the pinned template is #5857 (`untangle/template-move`, open). Until then,
  the protocol rule covers the runner side (`template-move-protocol.md`), and pi's separate
  `--provider`/`--model` form runs on both 1.4.0 and 1.6.0 (#5858).

## Problems found (untangle step 2), with the fix above each

- **The deploy tests build their own forward list.** `deploy/guards/deploy.test.ts` loads the kit
  over a `platform.json` with no forward change (`TREE.platform`). A case about forward changes
  loads one with its own list (`kitOver`), the same way the stage host's cases load an identity.
  - The file read the tree's own list through `facts.ts` `FORWARD`. The first real entry made 15
    cases fail (dark, routes, stages, rollback) that are not about forward changes.
  - The forward rollback case faked `-f1` by editing the record and the tag by hand, so no kit run
    ever tagged, staged or recorded a forward count. It now runs the kit over a list that names
    the sandbox, and holds what this change does on the stage: the `-f1` tag, 100% at once, the
    record's counts, and a rollback that takes back the doors and owner, leaves the sandbox and
    its callees, and refuses a record whose count is not the tag's.
  - The stage loader called `doUnmock` on the identity, so every later load in the file saw the
    real host. `kitOver` sets both mocks on every load.
  - `FORWARD == forwardCounts(platform.forward.changes)` in the facts case cannot fail under the
    fixture. `forward.test.ts` checks it against the real tree, so the line goes.

## Left, with reasons

- `deploy/guards/canary.test.ts` uses the same `doUnmock` pattern, with one load and none after
  it, so nothing reads past it. Decided to keep it as it is.

## As built

- `platform/sandbox/template/passed.json`: run 46112336531's artifact, appended as entry 12.
  `platform/sandbox/wrangler.jsonc` sets `E2B_TEMPLATE` to `tinyassets-sandbox:sb31f0bce0edffd2a`.
- `platform/deploy/platform.json`: `forward.changes[0]`, `{workers: ["sandbox"]}`.
- `platform/deploy/guards/deploy.test.ts`: `TREE`, `kitOver`, and the forward case run over its
  own list.

## The pin before

- [x] live: the dark redeploy runs its pair check on this pin. Superseded: the pin moves to
  sb31f0bce0edffd2a, and the line below carries the wait.
- [ ] live: the next stage deploy runs its pair check on sb31f0bce0edffd2a and deploys the
  sandbox at 100% at once, marked -f1.

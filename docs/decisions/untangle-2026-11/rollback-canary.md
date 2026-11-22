# rollback-canary as built (2026-11-22)

Rollback run 45071780667 (`platform-deploy.yml` mode `rollback --record 45065970031`, on the
live stage) took every Worker back and read each back, then its canary stopped the run: "FAILED
(exit 7) the handle set / the owner's files / the full run: TA_PROBE_KEY (the canary owner's
API key) is not set". The `routes` run's canary, in the same state, said "waits the keyed
checks". The two modes judged the same missing key differently.

## The research (the repo only; no dispatch, no live read)

- **Who runs the canary, and with what.**
  - The kit (`deploy.ts`), after four modes' apply. `dark`, `rollback` and `retire` ran
    `canary()` with no options; `routes` ran `canary("--side", "doors", "--unkeyed")`.
    `handback` runs no canary (`canaryAfter`: the old stack restarts first).
  - `platform-canary.yml`: the doors' clock, the hourly watchdog (`--clock`) and by hand
    (`--full`). It never passed `--unkeyed`. It holds no Cloudflare token.
- **How `canary.ts` judged the keyed checks.** On the old side every doors check waits,
  keyed ones too. On the doors' side, `--unkeyed` made them wait whatever the key; without it
  `keyOf` threw exit 7 when `TA_PROBE_KEY` was unset. So the rule was the mode's, by one flag
  only `routes` sent. Before routes the side is old anyway, so the flag mattered only once
  the doors serve: from then on, `dark`, `rollback` and `retire` failed a correct deploy on
  the stage while `routes` passed it.
- **Why `routes` waited.** Its comment gave two reasons: no canary owner's key can be minted
  before the doors serve `/app/api`, and the dark control record admits no one. The first
  is the old side's case. The second is the real one, and it is the platform's state, not the
  mode's: while the control record's maintenance gate admits nobody (`closedToAll`:
  `ordinary` `none`, or `canary` naming none, as `deploy/control.json`'s dark record), no
  owner can sign in (`ownerOpen`), so no canary owner can exist, and an `/api` call of one
  would be refused anyway (`outsideOpen`, the canary's exit 6). The checklist opens the record
  for the canary owner (`canary: [<id>]`) before minting its key; from then the key is owed.
- **Where that state can be read.** The control record is KV's (`c:control`), read with the
  Cloudflare token. Every kit mode that runs the canary holds the token (`dark`, `routes`,
  `rollback`, `retire`). The canary's own workflow does not, and giving an outside-in check
  the deploy token to read one record widens it for no gain: its scheduled runs start only
  once armed, after the canary owner's key is stored (checklist steps 3 to 6).

## The choice: one rule, the control record's

- **The keyed checks wait exactly where the control record is known to admit no owner.**
  The kit reads the record after every mode it runs the canary for and passes `--closed`
  when a well-formed record is `closedToAll`. Anywhere else the keyed checks run, and a
  missing `TA_PROBE_KEY` fails (exit 7): a record that admits someone (or one the kit could
  not read well formed, which the gates read closed and the canary's `503` names) is where a
  canary owner is owed (Fact 8). The workflow, which cannot read the record, runs them.
- `--unkeyed` goes: no mode chooses the rule. `routes` keeps `--side doors`, which is what it
  checks that it changed.
- With `--closed` the keyed checks wait whether the key is set or not: behind a closed gate
  they cannot pass, and a key set is not a check made.
- **Weighed and not built.**
  - **Waiting on a missing key in every mode** (the brief's first example). It would leave a
    deleted or expired key silent after the gates open, where a canary owner exists.
  - **A repository variable recording the canary owner.** A second record of what the
    control record already says (`canary`), set by hand, so a hidden link.
  - **The canary reading the record itself.** It would need the Cloudflare token in
    `platform-canary.yml`.

## As built

- `canary.ts`: `Options.unkeyed` is `closed` (`--closed`), and its waiting line names the
  control record as the reason.
- `deploy.ts`: `canary(...side)` is a row's after-steps, the zone read and then the canary,
  which reads the control record (`controlNamespace`, `controlOf`, `closedToAll`) and adds
  `--closed`. `dark`, `routes`, `rollback` and `retire` all use it; only `routes` names a side.
- `checklist.md`: the routes paragraph, the canary's keyed checks and the after-routes steps
  say the rule.
- `deploy.ts` `chained` runs a row's after-steps as it runs its steps, each given what the
  ones before made, so the canary's zone read reaches its control read. Its zone read masks
  the account a second time, which changes nothing (the mask test holds the set of masks).
- On the live stage: the old rollback canary read no record, so its exit 7 says nothing of
  the record's state, and nothing live was read for this PR. While the stage's record admits
  no owner, a rollback's canary now waits the keyed checks as the routes run's does; once it
  admits the canary owner, every mode's canary needs `TA_PROBE_KEY`.

## Owed

None.

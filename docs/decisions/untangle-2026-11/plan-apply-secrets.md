# plan-apply-secrets as built (2026-11-22)

`untangle/plan-apply-secrets` fixes what the first `dark` run with #5782's rollout record
(run 45014046824, main `b6895aaae`) showed live: its plan job and its apply job planned two
different rollbacks for `tinyassets-doors`, and the apply job's approval check passed.

## Research

Read from the run's log (names only; GitHub masks the values) and the kit at `b6895aaae`.

1. **The plan job planned secrets it could not see.** The plan job holds the Cloudflare token
   alone (`PUBLIC_INPUTS`; `platform-deploy.yml`'s `plan` job, held so by `deploy.test.ts`).
   `setSecrets` with no values (`secretsLater`) kept every secret `secretPlan` names, so the
   doors' two optional secrets they lack (`INBOUND_SLACK_SIGNING_SECRET`,
   `PLATFORM_CANARY_TOKEN`, empty in `production`) read "set ... (its value, or its absence if
   optional: checked by the apply job)". The apply job (`secrets`) dropped them, since the run
   is not given them. The rollout line was made from each job's own `setting`: the plan job's
   said the doors stop a rollback ("the run set INBOUND_SLACK_SIGNING_SECRET,
   PLATFORM_CANARY_TOKEN on it"), the apply job's that a rollback takes them back. Empty and
   unset read the same (`set`), so nothing else differed. The record the apply job wrote holds
   the doors' `secrets: []`, so run 45014046824's own rollback is right; only its plan was not.
2. **The approval check compared the plan job's steps with themselves.** `approved` made the
   plan again with `reach: "public"`, the plan job's step list (`DARK_LATER`), and compared it
   with the file. The apply job then ran `DARK`, another list, whose lines nothing compared.
   Every line was compared strictly, but only the plan job's. The two lists differed in more
   than the secrets: `templateReady` said no line where `templateLater` said one; the pair's
   line was worded two ways; `routes`' plan job said the hook before the handover and its apply
   job after it; the route record and the handback's hook line put the hook's word in
   different places.
3. **A second input only the apply job read.** With no hook at E2B the apply job added
   `E2B_WEBHOOK_SECRET` to `--rotate` (the hook it registers takes production's value), so a
   run could set the doors' copy, and make them forward-only, where its plan said nothing.
4. **Does every dark run set unchanged secrets? No.** `secretPlan` sets a secret only where
   the Worker's listing lacks it, or where `--rotate` names it; a held one is never set again.
   A run that changes no secret sets none, and leaves every Worker one a rollback can take
   back. The doors' line in run 45014046824 was the plan job's guess (1), not a set.
   **No hash is kept, and none is needed:** the listing already tells held from missing, and a
   held value the founder changed in `production` is kept by design until `--rotate` names it.
   **The kit cannot tell that a value changed:** Cloudflare lists names, never values, so a
   value changed in `production` alone (a rotated `WORKOS_API_KEY`) is never deployed, and
   no run says so. The checklist's step 3 says to dispatch with `rotate` naming it.
   A kept hash would only turn that into an automatic set, which makes the run forward-only,
   the opposite of the aim. It would also put a verifier of each value in the control
   namespace, which three Workers can write, and an unkeyed hash of a short optional value is
   guessable offline.
5. **Could the plan job know which optional secrets are given?** Only with a bit per secret
   in its job's env (`${{ secrets.X != '' }}`). That breaks the rule that the plan job holds
   the Cloudflare token alone, which `deploy.test.ts` holds, and adds a second list of the
   optional names in the workflow. Not taken; the plan says what it cannot tell (below).

## The shape, checked against the intent

- **One question, one answer: the apply job's own lines are the approved plan's.** The apply
  job still makes the plan job's plan again before any change (live drift between the jobs).
  Then each line its own steps say is held to the approved plan's line as it is said, before
  the change it names (`holding`, via the reporter's `say`). A line the plan job could not
  answer ends in "checked by the apply job before any change" (`checked`); the apply job's
  line has that subject exactly and its own answer in place of those words (`resolves`). The
  apply job never says those words itself. Any other line stops the run there, and a run that
  ends before the approved plan's last line stops loudly. Once every approved line is said,
  what follows is what the run did (an operator mode's answers, a handback's read-back).
- **A line the plan job cannot answer says so, and so does every line made from it.** An
  optional secret the Worker lacks is planned "if the run is given it", and the Worker's
  rollout line "takes it back there unless the run sets X on it"; the apply job answers both.
- **Nothing only the apply job reads decides what a run sets.** The hook's case (3) becomes a
  stop before any secret or Worker, asking for `--rotate E2B_WEBHOOK_SECRET`, as `SHARED`'s
  rule already does for a secret two Workers hold.
- **The early sign-in check is one the plan job cannot answer either** (found in review): with
  the callback unregistered, WorkOS answers `redirect-uri-invalid`, which the plan job read as
  "dark registers it", while the apply job, having registered it, says WorkOS takes it. So a
  first deploy against a client lacking the callback stopped after the registration. The plan
  job's word there is now `checked`, and the apply job's "it takes them" answers it.
- **Results said mid-run are their own kind** (`did`): the DNS records the custom domain or
  the host's record made, which name the run's own record key. They are reported, never held.

## The fix

1. `holding`, `resolves` and `checked` (`deploy.ts`); `stepsOf` holds the apply job's steps
   to the plan file's lines, which `approved` returns.
2. `Setting` (`sets`: null in the plan job for an optional secret), `settingLine`, and
   `rolledLine` from the Worker's settings; the record and `--secrets-file` take only what the
   run sets.
3. Each line a deploy's two step lists say is one form: the template's build, the pair, the
   redirect URI, the route record's hook word (last), the hook (last in `routes`' plan job
   too, after the routes), the handback's hook and the early sign-in check (`authorizeCheck`).
4. `setSecrets` stops when E2B holds no hook and a Worker holds `E2B_WEBHOOK_SECRET`, unless
   `--rotate` names it; the injected rotation is gone.

### Behaviour changed on purpose

- A dark or routes run with no hook at E2B while the doors hold `E2B_WEBHOOK_SECRET` stops
  before any secret or Worker, naming `--rotate E2B_WEBHOOK_SECRET`; before, it set the doors'
  copy itself, unplanned.
- Plan lines reworded (the template, the pair, the route record, the handback's hook, the
  early sign-in check), and the
  apply job's first line says its lines are held as they are said.

### Left, each with its reason

- **A difference found mid-run stops there, after the approved lines before it.** The steps
  before the secrets (the redirect URI, storage, and in `routes` the route record and the
  host's DNS record, which a handback takes back) are additive and were approved; holding
  every line before any change would mean running each mode's secret steps twice, and the
  operator modes' dry runs ask the doors through the operator channel.
- **What follows the last approved line is not held:** the operator modes' answers and the
  handback's read-back come only from the change, and `retire`'s sets follow its one plan line,
  gated by its words. No deploy step says a line there.
- **The redirect URI and the hook say their line once their call returns**, since only it
  names the outcome, which the plan job left to the apply job. Their subjects are fixed, so a
  stop there is only a line before them the apply job did not say: its change not made, and
  the redirect's or hook's own change the plan's line at the next place names.
- **The optional secrets' lines stay on every plan while the doors lack them** (2 lines and
  the doors' rollout line, today): the plan job's honest word, until `production` holds them
  or research 5 is taken.

## Owed

- [ ] live: the next `dark` run's plan job shows the doors' two optional secrets as "set ...
  if the run is given it: checked by the apply job before any change" and the doors' line as
  "a rollback takes it back there unless the run sets INBOUND_SLACK_SIGNING_SECRET,
  PLATFORM_CANARY_TOKEN on it: checked by the apply job before any change"; its apply job
  opens with "... is the one approved, and each line below is held to its line there as it
  is said", answers those lines "it is not, so it stays unset" and "it sets none of them",
  and its rollout record gives the doors `secrets: []`.

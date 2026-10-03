---
severity: P2
title: The scheduled tripwire has been red continuously
filed: '2026-08-27'
summary: '`heavy-tests` red 8/8 since 09-30; 4 fixed (#4302), 72 quarantined with owner deploy-incident (#4304) until plan B deletes the retired-design ones per test; a permanent red carries no more signal than a permanent green'
---

# The scheduled tripwire has been red continuously, so it carries no signal

**Filed:** 2026-08-27
**Severity:** P2 — no production impact, but it is a blind spot in the test surface

> The job was named `full-tests` when this was filed and is `heavy-tests` as of
> PR #2576 (2026-08-27). The finding below is stated against `full-tests`
> because that is what was measured; the Update at the bottom carries the
> post-rename numbers, which supersede the 135 count here. Filename kept so
> existing links resolve.

## The finding

`full-tests` was the "complete test surface" job in `.github/workflows/tests.yml`.
It is **not** a required check; it ran on push to `main` and hourly, and exists
as a post-merge tripwire.

It has failed on **every** commit checked:

| Commit | `full-tests` |
|---|---|
| `6dad3c42`, `07d5151a` | failure |
| `a44aed2c`, `20b317c0` | failure |
| `d513db80`, `d6d51015` | failure |
| `e4180697` (push and scheduled) | failure |

`e4180697` predates the 2026-08-27 work, so this is **not** a regression from
the harness reset or the lean pass. It was already red.

**135 distinct failures**, concentrated in a few files:

```
278 hits  tests/test_deploy_prod_workflow.py
 34 hits  tests/test_retire_cheat_loop_deploy_fence.py
 12 hits  tests/test_host_uptime_installers.py
 10 hits  tests/test_integration.py
  6 hits  tests/test_backup_script.py
```

## Why it matters

A permanently-red check is the same failure as a permanently-green one: **nobody
can tell a new break from the standing noise.** This repo spent 2026-08-26/27
removing checks that could not fail (`policy`, vacuous 100/100;
`cross-provider-drift`, unconditionally clean). This is that shape inverted, and
it deserves the same treatment.

It compounds with `.github/heavy-test-files.txt`: files listed there are excluded
from the required lane and run *here*. So the heaviest tests are routed into a
job whose result nobody can read.

## What the failures look like

A mix, and the mix is the point — several are CI-environment, not product:

- `Xlib.error.DisplayNameError: Bad display name ""` — no X display
- `shellcheck backup.sh: assert 1 == 0` — shellcheck not installed on the runner
- `assert 154.884967026 < 154.877550865` — a timing flake, ~7 ms over
- `assert result == (Path.home() / ".workflow").resolve()` — host-path assumption

Environment failures and real failures are indistinguishable in the current
output, which is why the job has been ignorable.

## What would resolve this

Either make it green (install `shellcheck` and a virtual display on the runner,
quarantine the genuine flakes, fix or retire `test_deploy_prod_workflow.py`), or
retire the job and stop routing `heavy-test-files.txt` into it. **Leaving a red
tripwire in place is the one option that should not survive** — it reads as
coverage while providing none.

Not attempted here: 135 failures across a deploy-workflow suite is a real piece
of work, and doing it badly would mean quarantining assertions to get green,
which this repo explicitly forbids.

---

## Update 2026-08-27: the job was renamed, the red was not fixed

`full-tests` is now `heavy-tests` (PR #2576). That change removed the
DUPLICATION -- the old job re-ran the 10,700 tests `required-tests` had already
passed minutes earlier -- but it did **not** make the tier readable.

Cross-family review measured the baseline precisely, and rated it a blocker:

| Measure | Value |
|---|---|
| Heavy tests run | 2,236 |
| Failures | 114 |
| **Unquarantined** failures | **107** |
| Stale quarantine entries | 10 |

So the quarantine path now applies (it did not under raw pytest) and still does
not help: 107 of 114 failures are not in the ledger.

**The rename was landed anyway, deliberately.** It is strictly better than what
it replaced -- same redness, minus a duplicated 10,700-test run per merge, and
a red now means "a heavy file broke" rather than "something in 13,865 tests".
It is not required, so it blocks nothing. But nothing here is fixed.

**What resolving this actually needs**, from the Linux junit rather than a local
run: 105 of the 132 whole-suite failures are two files.
`tests/test_deploy_prod_workflow.py` (88) asserts a `workflow_dispatch` input
that no longer exists -- PR #2442 rewrote that workflow from 2,762 lines to 134
-- and `tests/test_retire_cheat_loop_deploy_fence.py` (17). Those need per-test
triage against the current design, NOT deletion: the failure causes are mixed,
and one references `TINYASSETS_CODEX_AUTH_JSON_B64` missing from a step, which
may be a real drop from that 2,628-line deletion rather than a stale assertion.

Until then `heavy-tests` is a red tripwire, and the 10 stale quarantine entries
should be drained via `scripts/quarantine_oracle.py` as a cheap first step.

---

## Update 2026-08-27 (later): the deploy-workflow half is measured

`tests/test_deploy_prod_workflow.py` is the largest single contributor to the
red — 88 of the 105 two-file concentration above. It has now been triaged
rather than assumed, and the result changes what should be done with it.

**Two of the 88 were real drops, not stale tests**, and are fixed in #2584:

| Dropped by #2442 | Consequence |
|---|---|
| `Prepare codex auth persistent volume` | Nothing created `/data/.codex` at `uid 1001:1001`, or repaired `/data/.auth.db` ownership. `DEPLOY.md:168` still documented the step as running "on every deploy". Latent, not live — the volume survived from before. |
| `concurrency: production-host-mutation` | Deploy could run concurrently with `restart-daemon`, `install-host-services`, or `p0-outage-triage` on the same droplet. |

A third real drop was found and filed rather than fixed:
`docs/concerns/2026-08-27-deploy-drops-compose-sync.md` (P1).

Those took the file from 88 failures to 81.

**The remaining 81 are not stale NAMES.** That was the obvious hypothesis —
`Rollback on failure` → `Roll back if the public canary is red`, `Capture
previous image tag` → `Capture current image`, `Resolve image tag` → `Resolve
image tag -> immutable digest`, `Post-deploy canary — canonical URL only` →
`Public MCP canary (--assert-handles)`. It was tested directly, by adding a
rename map to `_step_named` and running the file:

```
81 failed, 40 passed   (before)
79 failed, 42 passed   (with every rename mapped)
```

**Two.** The other 79 fail on the steps' *contents*, because #2442 did not
rename those steps, it replaced what they do. `Rollback on failure` rolled back
on any deploy failure; the current step rolls back specifically on a red public
canary, with unhealthy-container rollback moved into `deploy_fail_safe.sh`. The
capability was redistributed, not renamed.

**So the remaining 81 assert a retired design and should be deleted, not
retargeted.** That is a real deletion of 81 tests and deserves its own change
and its own review — it is not a tidy-up to fold into a fix. Two things must be
true before it lands:

1. Every assertion is checked against the current workflow individually. The
   two real drops above were found *inside* what looked like a uniformly stale
   file; assuming uniformity is exactly the mistake that would have missed
   them.
2. Whatever survives is rewritten against the current 265-line workflow, so the
   file still gates something. Deleting it outright would leave `deploy-prod.yml`
   with no test at all.

Not attempted here: the `recover-unsafe` job was also dropped by #2442 while
its script `scripts/retire_cheat_loop_deploy_fence.py` still exists — as does
its test file, the other large red cluster (17 failures). Whether that feature
was retired deliberately or dropped by accident is a question for the founder,
not a guess.

---

## Update 2026-08-27 (later still): triaging the 81 found a fourth real drop

The 81 were re-run against `814b4f06` (still exactly 81) and clustered by
failure reason rather than by test name. Most are step-name assertions against
steps #2442 replaced -- as measured before, those are retired design. But the
cluster that asserts *capabilities* rather than step names was checked
individually, and one of them is right:

**No workflow delivers `TINYASSETS_CODEX_AUTH_JSON_B64` or
`TINYASSETS_CLAUDE_CREDENTIALS_JSON_B64` any more**, while
`deploy/docker-entrypoint.sh:117,153` still decodes both on container start and
`deploy/DEPLOY.md:203` still tells the operator to keep the secret rotated.
Filed as `docs/concerns/2026-08-27-deploy-drops-subscription-auth-sync.md`, resolved and deleted 2026-09-24 when the platform's own model logins were retired under Hard Rule 15 (P2 — first drafted P1, corrected during the same pass: `cloud_worker.py:606-612` says the container's own CODEX_HOME is NOT consulted for universe-scoped work, so this degrades auth-health reporting and rotation, not user runs).
That is the fourth real drop recovered from #2442.

The same pass cleared a near-identical-looking assertion:
`TINYASSETS_GITHUB_PR_CAPABILITIES` **did** relocate, to
`scripts/github-app-token-refresher.py` on a systemd timer installed by
`install-host-services.yml`. Its assertion is genuinely stale.

Two assertions that look the same, one real and one not, is the concrete reason
this file has said from the start that the 81 need per-test triage and not
bulk deletion. Remaining capability clusters not yet individually checked:
`terminal_receipt_result=` (14), `org.opencontainers.image.revision` (2),
cloud-worker liveness (2), and the step-summary capability-map line (2).

### The full cluster triage

All 81 re-run against `814b4f06` (still exactly 81) and grouped by failure
reason rather than by test name, because the name says what a test wanted and
the reason says what the workflow no longer does. Every group was then checked
against the current workflow individually.

| Cluster | ~n | Verdict |
|---|---|---|
| Step-name assertions: `Rollback on failure`, `Capture previous image tag`, `Scrub stale cloud env overrides`, `Transitional task 2.1 *`, `Resolve image tag`, `Post-deploy canary` | 66 | **Stale.** Steps were replaced, not renamed — already measured: mapping every rename moved 81 to 79. |
| `terminal_receipt_result=` | 14 | **Stale.** The capability is retained by `Publish release-state receipt` (`:336`). Its hardcoded `forward_canary_status:"passed"` is honest because the step is `if: ${{ success() }}`, so it cannot publish over a red canary. |
| `TINYASSETS_CODEX_AUTH_JSON_B64` reaches the droplet | 4 | **Retired 2026-09-24 (Hard Rule 15: the platform has no LLM).** These now assert a retired design. They were a real drop on 08-27. |
| `TINYASSETS_GITHUB_PR_CAPABILITIES` | (same cluster) | **Retired 2026-09-24 with the platform GitHub push credential (map, refresher and host units removed).** Previously: **Relocated but BROKEN — corrected by cross-family review.** The timer mints and writes the host env file, but the daemon's environment is an `env_file` snapshot taken at container creation, so the refresh never reaches the running process. Filed as [github-app-token-refresh-never-reaches-daemon](2026-08-27-github-app-token-refresh-never-reaches-daemon.md) (P1). I stopped at "a delivery path exists" without following it to the consumer. |
| `org.opencontainers.image.revision` | 2 | **Not stale — keep.** The current workflow takes `revision` from `github.event.workflow_run.head_sha` (`:112`), not from the image's revision label, while the receipt still claims `"active_source_provenance":"digest_revision_label"`. These two assert the mechanism that would close [deployed-sha-proves-receipt-only](2026-08-26-deployed-sha-proves-receipt-only.md). Deleting them deletes the spec for that fix. |
| Cloud-worker liveness | 2 | **Stale.** The deploy starts `daemon cloudflared logs` only, deliberately — `deploy/deploy_fail_safe.sh:148` names the reason ("unprofiled worker services that an unqualified `up -d` would start"), and `slack-agent` is `profiles: ["slack"]`. |
| Runtime compose sync | 2 | **Restored** by #2685, which resolved and deleted the deploy-drops-compose-sync concern. |
| Step-summary capability-map / codex-auth visibility lines | 2 | **Retired with the auth delivery** (Hard Rule 15). |
| `recover-unsafe` / `retire_cheat_loop_deploy_fence.py` | 6 | **ANSWERED — accident. Keep.** Task 2.5a authorises deleting the fence only *after* task 2.5's locked migration; 2.1, 2.5 and 2.5a are all unchecked, three workflows still arm the fence, and all four recovery support files are orphaned in-tree. Filed as [unsafe-fence-recovery-path-deleted](2026-08-27-unsafe-fence-recovery-path-deleted.md) (P1). |

**So the file is not uniformly stale, and "delete all 81" would have destroyed
two real findings and one open spec.** Six assertions must survive in some form
(4 auth-delivery, 2 revision-label); the rest can go once whatever survives is
rewritten against the current workflow, per the two conditions this file set
before any deletion lands.

---

## Update 2026-10-02: quarantined with an owner; what still survives

The scheduled heavy-tests job was red 8 of 8 runs since 09-30 with 76
failures. 4 were a test-setup defect in public_goal_visibility (unowned
universes), fixed in #4302. The other 72 (55 here, 17 in
`test_retire_cheat_loop_deploy_fence.py`) are quarantined in #4304. They are
ratcheted ledger entries with `owner=deploy-incident`, an expiry and a link to
this concern or the fence concern. They still run and are not weakened. A NEW
heavy failure is a signal again.

What changed since the table above:

- The 4 auth-delivery assertions and the 2 summary lines are now retired
  design: Hard Rule 15 retired the platform's model logins on 2026-09-24.
- Compose sync was restored by #2685.
- A fifth #2442 drop surfaced: the request-idempotency HMAC path,
  `2026-10-01-deploy-dropped-request-idempotency-hmac.md`, decision RESTORE.
  Its six assertions are a spec, not stale.

So the specs that must survive are the 2 revision-label assertions, the 6 HMAC
assertions and the fence file's recovery path (KEEP stands). The rest are
retired design. Plan B deletes them per test, in their own PRs
(`Test-Removal: retired`, cross-family refute). Each deletion removes its
ledger line and lowers `MAX_QUARANTINE`.


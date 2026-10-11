---
title: Watchdog restart-loop correlation audit
date: 2026-04-26
auditor: dev (Task #18; navigator-deferred audit follow-up)
status: VERDICT — none of (a)/(b)/(c); reclassify as TEST-LOG POLLUTION on shared dev artifact
related:
  - .agents/uptime_alarms.log (audited subject)
  - scripts/watchdog.py (production watchdog source)
  - tests/test_watchdog.py (test pollution source)
  - 2026-04-23-p0-auto-recovery-trace.md (archived in git) (prior baseline — different signature)
  - docs/ops/post-redeploy-validation-runbook.md §7 (downstream consumer)
---

# Watchdog restart-loop correlation audit — 2026-04-26

## TL;DR

**Verdict:** Reclassify the symptom. The "30+ restart loop" reading
of `.agents/uptime_alarms.log` is incorrect. The file contains exactly
**zero production restart events**. Every entry is **test-suite
pollution from `tests/test_watchdog.py`** running locally on dev/CI
machines and writing to the wrong (repo-relative) alarm-log path
hardcoded in `scripts/watchdog.py:64`.

There is no live watchdog-restart loop in production to remediate. The
underlying classification options the audit asked between — (a) same
2026-04-23 revert-loop, (b) new commit-induced flap, (c) new pattern,
(d) inconclusive — all assume the entries reflect droplet behavior. They
do not.

The real issue uncovered is a **structural test/production-data sharing
bug**: tests write to a logfile path the production watchdog also writes
to, contaminating the only signal we have for production watchdog
behavior. Recommended remediation in §6.

## Signal anatomy

`.agents/uptime_alarms.log` content:

| Metric | Value |
|---|---|
| Total lines | 603 |
| Date range | 2026-04-21T04:04 → 2026-04-25T23:22 UTC |
| Unique alarm types | 1 (`WATCHDOG_RESTART`) |
| Unique service targets | 1 (`tinyassets-daemon.service`) |
| Unique probe URLs | 1 (`http://127.0.0.1:8001/mcp`) |
| Unique minute-events | 65 |
| Unique second-events | 84 (some events straddle a one-second boundary) |

**Critical fingerprint — every minute-event contains exactly 9 lines:**

| `reds=` value | Count | Per-event multiplicity |
|---|---|---|
| reds=1 | 67 | 1× per event |
| reds=3 | 469 | 7× per event |
| reds=5 | 67 | 1× per event |

67 × 9 = 603 ✓ (matches total). Each minute-event has the same
fingerprint: `{reds=3 ×7, reds=1 ×1, reds=5 ×1}`.

## Why this fingerprint cannot be production

The watchdog source `scripts/watchdog.py:283-287` writes ONE alarm line
per `watchdog_tick()` call that crosses the threshold and successfully
issues a restart:

```python
alarm_line = (
    f"{_now_iso()} WATCHDOG_RESTART service={service_unit} "
    f"reds={threshold} probe_url={probe_url}"
)
```

`reds={threshold}` is the **configured threshold** (not
`consecutive_reds`). In production the threshold is hardcoded
`DEFAULT_THRESHOLD = 3` (`scripts/watchdog.py:57`) and the only deployed
unit `deploy/tinyassets-watchdog.service` accepts no `--threshold` arg, so
production writes always emit `reds=3`.

Two findings flow from this:

1. **`reds=1` and `reds=5` lines cannot come from production.** They can
   only come from a caller passing `threshold=1` or `threshold=5`.
2. **9 lines per minute-event with the threshold-fingerprint pattern
   cannot come from a single production restart.** The production unit
   timer is `OnUnitActiveSec=30s`; it would need to fire 9 times in the
   same minute (which the timer prohibits) AND somehow vary the
   threshold each time.

## Where the lines actually come from

`tests/test_watchdog.py` exercises `watchdog_tick()` 32 times across the
file. **18 of those calls do NOT inject the `alarm_log` parameter** — so
they fall back to the module-level default at `scripts/watchdog.py:64`:

```python
_REPO_ROOT = Path(__file__).resolve().parent.parent
ALARM_LOG = _REPO_ROOT / ".agents" / "uptime_alarms.log"
```

That default is the **same on-disk path the production watchdog writes
to** (when run from the deployed checkout). Tests inject `restart_fn`
and `probe_fn` so they don't actually call `systemctl` or hit a real
endpoint — but the alarm-log write at line 287 is gated only on
`success` from the (mocked) restart, so every test that reaches the
restart branch appends one line to `.agents/uptime_alarms.log`.

Specific test-fingerprint contributors:

| Test | Threshold | Restart triggered? | Lines emitted per pytest run |
|---|---|---|---|
| `test_third_red_triggers_restart` | 3 (default) | yes | 1 (reds=3) |
| `test_threshold_1_single_red_restarts` | 1 | yes | 1 (reds=1) |
| `test_threshold_5_needs_5_reds` | 5 | yes | 1 (reds=5) |
| `test_dry_run_logs_intent_no_action` | 3 | dry-run (no log) | 0 |
| `test_min_restart_interval_blocks_immediate_repeat` | 3 | yes (then blocked) | 1 |
| `test_restart_failure_keeps_streak` | 3 | tries, fails, no log | 0 |
| `test_restart_attempted_when_no_state_file` | 3 | yes | 1 |
| ...other restart-success paths in same file | 3 | yes | 1 each |

The 7×reds=3 + 1×reds=1 + 1×reds=5 pattern matches **a single full
pytest run of `test_watchdog.py`** — exactly 9 alarm lines per pytest
invocation. (The 7 reds=3 entries account for the seven default-
threshold tests that reach the restart branch.) The 65 minute-events
correspond to **65 distinct pytest invocations** over five days.

## Cross-correlation with other evidence

### Against the 2026-04-23 audit baseline

`2026-04-23-p0-auto-recovery-trace.md (archived in git)` documents three
**actual** P0 events on the droplet (issues #51, #52, #53) at
11:43 / 17:49 / 22:11 UTC on 2026-04-23. **Zero of those timestamps
appear in `uptime_alarms.log`** (the 04-23 day is entirely absent from
the file). This is the strongest negative evidence: real droplet
incidents are NOT in this file.

That audit identified `disk_full` as the failure class (worker
revert-loop generating non-prunable artifacts). Different signature
entirely from "watchdog probe failed" — that audit's events were
classified by `p0-outage-triage.yml`, not by `watchdog.py`.

### Against `.agents/uptime.log` (PROBE-001 layer)

`.agents/uptime.log` has 7 entries spanning 2026-04-19 to 2026-04-25.
Cross-reference:

- 04-21 + 04-22: NO uptime.log entries → public canary not being run by
  anyone during the alarm-log "hot windows."
- 04-23T23:38-23:39: 2 GREEN uptime.log entries → public surface fine.
- 04-25T12:15: RED uptime.log entry — the wiki-canary kwargs bug
  (Task #13). Public surface still HTTP-200 reachable; just the wiki
  tool path was returning isError. UNRELATED to watchdog probing
  127.0.0.1:8001/mcp.
- 04-25T19:42 + 20:30: 4 GREEN uptime.log entries — wiki canary fixed.

The public canonical surface (`tinyassets.io/mcp`) was healthy across
the entire alarm-log date range. There is no production red signal that
correlates with the alarm-log "events."

### Against the commit timeline

The dispatch hypothesized that the 2026-04-25T17:43Z onset cluster
might be commit-induced. The commit landed in that window:

- `87e96bb` "Docs bundle — audits + ops runbook + design note + CONTRIBUTORS" (2026-04-25 ~17:43Z window)

A docs-only commit cannot induce a production daemon restart. But it
CAN correlate with a `pytest` run on the dev machine (e.g., pre-commit
hook firing the test suite, including `test_watchdog.py`). That matches
the pollution explanation.

The dispatch's listed candidate commits — Tasks #65a/65b/67/71/72/74/75/76c/82 —
all landed earlier (per `git log` evidence, mostly 04-25 06:00-12:00 UTC).
None of them touched startup path / health-check / __main__ import graph
in a way that would cause a 9-minute-after-deploy restart loop. Even if
they had, the 9-line-per-event fingerprint with three different
thresholds would still rule out a single production cause.

## Verdict against the dispatch's option set

| Option | Verdict |
|---|---|
| (a) Same revert-loop pattern as 2026-04-23 | **REJECTED.** Different failure mode entirely (disk-full → docker prune class) and zero overlap with 04-23 timestamps. |
| (b) New commit-induced flap | **REJECTED.** The 9-line/3-threshold fingerprint can only come from test invocations, not from a single production restart. |
| (c) New pattern unrelated to commits | **REJECTED.** It's not even a production pattern. |
| (d) Inconclusive | **REJECTED.** Test-pollution attribution is positively confirmed by the threshold-fingerprint analysis. |
| **(e) Test-log pollution (NEW VERDICT)** | **ACCEPTED.** Audited evidence supports this exclusively. |

## What this means for the post-redeploy validation runbook (§7)

`docs/ops/post-redeploy-validation-runbook.md §7` ("Watchdog-restart-loop
observation") was written under the assumption that the alarm log
reflects droplet behavior. Per this audit, **§7 is operating on
contaminated signal.** Two paths forward:

1. **Quick fix:** §7 should observe the **droplet's own** alarm log
   (e.g., `/var/log/tinyassets-watchdog.log` or whatever the deployed
   path resolves to on the droplet — TBD by host inspection), NOT the
   repo-relative `.agents/uptime_alarms.log`. The runbook's `tail -F
   .agents/uptime_alarms.log` is monitoring the wrong file.
2. **Structural fix (preferred):** see §6 below.

I will surface this discrepancy to lead so the runbook can be patched
in a follow-up; not gating this audit on it.

## §6 — Recommended remediation (structural)

**Root cause:** `scripts/watchdog.py:64` hardcodes a repo-relative
default (`_REPO_ROOT / ".agents" / "uptime_alarms.log"`). Tests fall
back to this default whenever they don't inject `alarm_log=tmp_path/...`.
The result is that test artifacts and production telemetry share an
on-disk path.

**Three fix options, ordered by cost:**

### Fix R1 (cheapest): Tests-only — make `alarm_log` injection mandatory in test fixtures

Update `tests/test_watchdog.py` so every `watchdog_tick(...)` call passes
an explicit `alarm_log=tmp_path / "uptime_alarms.log"`. Eighteen call
sites need the param added. Stops the bleeding immediately. Does not fix
the structural problem (any future test or one-off script-run could
re-pollute).

### Fix R2 (recommended): Make production path absolute + machine-specific

Change `scripts/watchdog.py:64` from a repo-relative default to a
host-specific absolute default, e.g.:

```python
ALARM_LOG = Path(
    os.environ.get("TINYASSETS_WATCHDOG_ALARM_LOG", "/var/log/tinyassets/uptime_alarms.log")
)
```

Production deploy sets/leaves the env var pointing at a host-only path.
Tests that don't inject `alarm_log=` can't accidentally write to the
shared repo file, because the path on dev machines doesn't exist or
isn't writable. Combine with R1 for belt-and-suspenders.

### Fix R3 (most thorough): Separate test fixture vs prod default

Refactor `watchdog_tick()` to require `alarm_log` as a positional or
keyword-only mandatory arg with no module-level default. Production
caller (`scripts/watchdog.py:main`) supplies the prod path explicitly;
tests must supply a tmp path. Removes the "shared module-level default"
class entirely. Cost: API change to one function (one prod caller, ~32
test call sites).

**My recommendation:** R1 + R2 in one bundle. R1 stops the bleeding
right now, R2 closes the structural class. R3 is overengineering for
the size of the surface.

## §7 — Cleanup of the contaminated log

The current `.agents/uptime_alarms.log` (603 lines, all test-pollution)
should be **truncated, not preserved.** It has zero diagnostic value for
production behavior and presence of the file actively misleads (this
audit's whole purpose).

Recommended action AFTER R1+R2 land:

```bash
# After fixes are in main, truncate the contaminated log:
> .agents/uptime_alarms.log
git add .agents/uptime_alarms.log
git commit -m "Truncate test-polluted uptime_alarms.log post-R1+R2"
```

Or, even better, **add `.agents/uptime_alarms.log` to `.gitignore`** so
local test pollution stops being committed at all. The file should be
either machine-local artifact OR production-only telemetry — never both.

## §8 — STATUS implications

**STATUS Concerns:** The current "watchdog-restart loop" framing in
session activity (e.g., 2026-04-26 navigator notes) should be updated:
no production loop exists. STATUS already lacks an explicit row for
this — good; nothing to delete. If lead chose to add a tracking row
based on the navigator's observation, it should now be reframed as
"R1+R2 watchdog-log path fix" instead of "watchdog loop diagnose."

**Forever-Rule check:** This audit does NOT change the Forever-Rule
posture. Production daemon uptime is whatever the cloud daemon's actual
behavior is — which we cannot observe from this file. The post-redeploy
runbook §7 needs the path correction before it becomes a useful
observation tool.

## §9 — Hand-off

**Per dispatch step 8:** if (b) or (c), surface to lead as STATUS-Concern
candidate with named root cause. **My verdict is (e), not (b) or (c).**
The "candidate root cause" to surface is:

> `scripts/watchdog.py:64` ALARM_LOG default is a repo-relative path
> shared with tests; 100% of `.agents/uptime_alarms.log` content is
> test pollution. Recommended fix: R1 (tests inject alarm_log) +
> R2 (prod default = absolute env-driven path). Truncate +
> `.gitignore` the contaminated log.

**Per dispatch step 9:** for (a) — confirm existing remediation path
still correct? **N/A — verdict is not (a).** The 2026-04-23 audit's
disk-full + revert-loop remediation chain (Tasks #8 BUG-023 storage
observability + Task #9 Lane 4 revert-loop canary) is unaffected by
this audit's findings.

**Hand-off path (per dispatch):** navigator cross-check before lead.
This audit's finding directly contradicts the original framing of the
deferred audit (navigator's "watchdog restart loop on uptime_alarms.log"
language). Surfacing to navigator first lets them sanity-check the
threshold-fingerprint analysis before lead acts on the recommendation.

## Appendix A — Reproduction commands

To verify the test-pollution claim independently:

```bash
# Step 1: confirm threshold fingerprint per event
awk '{print substr($1,1,16)}' .agents/uptime_alarms.log | sort | uniq -c \
  | awk '{print $1}' | sort | uniq -c
# Expected: every minute-event has count 9 (with at most a couple at 18 if
# two events landed in the same minute).

# Step 2: confirm only 1, 3, 5 are the threshold values seen
grep -oE 'reds=[0-9]+' .agents/uptime_alarms.log | sort -u
# Expected: only reds=1, reds=3, reds=5.

# Step 3: confirm the production unit hardcodes threshold=3
grep -A1 'ExecStart' deploy/tinyassets-watchdog.service
# Expected: bare `python3 watchdog.py` with no --threshold arg.

# Step 4: confirm tests use thresholds 1 and 5
grep -E 'threshold=(1|5)' tests/test_watchdog.py
# Expected: hits in test_threshold_1_single_red_restarts and
# test_threshold_5_needs_5_reds.

# Step 5: count test-side calls that don't inject alarm_log
grep -c 'watchdog_tick(' tests/test_watchdog.py    # 32
grep -c 'alarm_log' tests/test_watchdog.py         # 14
# Difference 32 - 14 = 18 calls using the default repo-relative path.
```

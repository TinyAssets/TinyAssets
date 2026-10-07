---
title: Post-Redeploy Validation Runbook
date: 2026-04-26
audience: claude-code lead (immediately after host runs cloud daemon redeploy)
status: active — push-button checklist
---

# Post-Redeploy Validation Runbook

When the host runs the cloud daemon redeploy on the DigitalOcean Droplet,
several STATUS Concerns and host-action rows close in one touch. Without a
checklist the validation is ad hoc and we miss things. This runbook is the
push-button sequence to run immediately after the redeploy completes.

Reference probes: `docs/ops/acceptance-probe-catalog.md`.
Restart-only ops (no code change): `docs/ops/cloud-daemon-restart.md`.

---

## Quick reference — what this redeploy unblocks

| Surface | Concern / row | Validation step (this doc) |
|---|---|---|
| BUG-028 fix verification (slug normalization + bug-investigation auto-trigger) | STATUS Work row "Cloud daemon redeploy" | §5.1 |
| `goals action=set_canonical` (Mark-canonical decision) | STATUS Work "Mark-branch canonical decision" | §5.2 |
| `patch_branch` read-after-write contract (BUG-030 reproduction) | code already shipped (Task #11 verified) | §5.3 |
| Failure-class shape on run failures (BUG-029 `actionable_by`) | code shipped (Task #10) | §5.4 |
| Wiki status migration (#32) | STATUS Work "Wiki status migration" | §5.5 |
| Mark's `change_loop_v1` end-to-end milestone | `docs/design-notes/2026-04-26-mark-change-loop-status-mapping.md` (deleted 2026-10-06; in git history) R3 | §5.6 |
| Sub-branch invocation Phase A (`a12e284`) | recent commit | §6.1 |
| Bounty-calc query template substrate (`373df03`) | recent commit | §6.2 |
| Dispatcher request_type substrate | landed `d06a6d7`/`79a3c28`/`218d9ec`/`c686b48` | §6.3 |
| Watchdog observation | journald (`journalctl -u tinyassets-watchdog.service`); structured alarms at `/var/log/tinyassets/uptime_alarms.log` per Task #20 | §7 |

---

## Sequence overview

Probes first (cheap, ~2 min) → end-to-end fix validation (~10-20 min) →
watchdog observation window (5-min wall clock; can run other steps in
parallel) → STATUS trim.

**Pre-flight assumption:** host has reported "redeploy complete" and shared
the deployed image tag (or it's visible via DO dashboard). Note the tag
before starting — the §1 deploy-tag check uses it.

---

## §1 — Deploy fingerprint check

Confirm the image actually deployed and the container is up. **Do not skip
this step** even if the canary is green — a stale container that happens to
respond can mask a no-op deploy.

### §1.1 Image-tag confirmation

```bash
# Expected tag = ghcr.io/tinyassets/tinyassets-daemon:<short-SHA>
# where <short-SHA> matches `git rev-parse --short=12 origin/main`.
EXPECTED_TAG=$(git rev-parse --short=12 origin/main)
echo "Expected image tag: ghcr.io/tinyassets/tinyassets-daemon:${EXPECTED_TAG}"
```

Then ask host to confirm via either:
- DO dashboard → Droplet console → `docker ps --format '{{.Image}}'` → look
  for `ghcr.io/tinyassets/tinyassets-daemon:${EXPECTED_TAG}`.
- The deploy-prod GitHub Actions run summary — it prints "Target image" as
  the final-step output.

**Red:** image tag is `:latest` or an older SHA → §1.4 fallback diagnosis.

### §1.2 Container up + healthy

Host runs (or shares output of):

```bash
sudo docker ps --filter name=workflow- --format \
  'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

**Green criteria:** `tinyassets-daemon`, `workflow-tunnel`, `workflow-logs`
all show `Up <X> seconds` (recent — confirms restart) and the image column
matches §1.1's expected tag.

**Red:**
- Any container `Restarting` → §1.4.
- `workflow-worker` is `Exited` — that's OK if STATUS notes "daemon
  PAUSED"; host explicitly halted it. Otherwise it should be `Up`.
- Image column shows the OLD tag → redeploy did not pick up new image.

### §1.3 MCP `/health` (server-internal liveness)

```bash
curl -sS https://tinyassets.io/mcp -X POST \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json,text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"post-redeploy-check","version":"1.0"}}}' \
  -w '\nHTTP=%{http_code}\n'
```

**Green:** HTTP 200 + JSON `result` with `serverInfo.name == "workflow"`.

**Red:** HTTP 5xx, timeout, or `error` in response → §1.4.

### §1.4 If §1.1-§1.3 are red

Stop here. Run `docs/ops/cloud-daemon-restart.md` instead — the redeploy
itself is the problem, not the post-redeploy validation. Re-attempt
validation after that runbook clears.

---

## §2 — Canonical surface probes

All three probes run from your local machine. Each takes <30s. Export the
`TINYASSETS_WIKI_CANARY_TOKEN` service-principal credential first; every probe
fails closed with exit 2 when it is absent.

### §2.1 Layer-1 MCP canary

```bash
python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp --verbose
```

**Green:** exit 0, "GREEN" log entry appended to `.agents/uptime.log`.

### §2.2 Wiki gate + read canary (PROBE-003)

```bash
python scripts/wiki_canary.py --url https://tinyassets.io/mcp --verbose
```

Reworked 2026-09-03 for the no-anonymous boundary. The probe first asserts that
a tokenless `initialize` receives the OAuth 401 challenge, then initializes as
the named `canary` principal and performs the exact reserved write/read
roundtrip at `drafts/notes/uptime-probe.md`.

**Green:** exit 0, verbose output shows the authenticated handshake, reserved
write confirmation, and matching authenticated read.

**Red:** exit 6 (tokenless initialize was admitted or the scoped write failed),
exit 7 (read failed or draft content mismatch), exit 2 (credential missing or
handshake broken).

**Scope:** production and dev modes both require a named principal. There is no
anonymous probe or dev-mode write fallback.

### §2.3 MCP tool-invocation canary (PROBE-004)

```bash
python scripts/mcp_tool_canary.py --url https://tinyassets.io/mcp --verbose
```

**Green:** exit 0 — initialize + initialized + tools/list non-empty +
`universe action=inspect` returns valid `universe_id`.

**Red:** exit 4 (tools/list empty) or exit 5 (inspect failed) — tool
registration broken in deployed image. Stop and triage; do NOT proceed to
§5 fix-validation steps until this is green.

### §2.4 PROBE-001 full-stack smoke (deferred to chatbot run)

PROBE-001 requires a live Claude.ai chat (per `acceptance-probe-catalog.md`
§Limitations). Defer until §2.1-§2.3 are all green. When ready, run the
ui-test skill with the PROBE-001 prompt:

```
hey i want to use the tool i set up to design a workflow for writing a research
paper on deep space population — can you walk me through it?
```

**Green criteria (per probe catalog):** chatbot invokes ≥1 TinyAssets MCP
tool, response references real daemon state, settle <150s, no "Session
terminated" errors.

This is the final acceptance gate — if §1-§2 are green and PROBE-001 is
green, declare the redeploy validated.

---

## §3 — Mirror parity check

Quick sanity that the canonical and packaging mirrors match — drift here
would indicate dev-side packaging-sync didn't run before the build.

```bash
cmp -s tinyassets/universe_server.py \
       packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/universe_server.py \
  && echo "universe_server.py mirrors MATCH" \
  || echo "WARN: universe_server.py mirrors DIFFER"

cmp -s tinyassets/runs.py \
       packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/runs.py \
  && echo "runs.py mirrors MATCH" \
  || echo "WARN: runs.py mirrors DIFFER"
```

**Green:** both MATCH. (Mirrors may pre-diverge for the dist surface per
memory `feedback_three_mirrors_dist_may_diverge` — runtime should match.)

**Red:** WARN on either → file an issue but don't block the redeploy
validation. The deployed image was built from one of them; whichever it
was is what's running.

---

## §4 — Suite + lint sanity

Quick local check that the codebase that matches the deployed image is
also test-clean. **Do not run as a gate** — verifier already SHIPped the
commits; this is a confidence check, not a re-verification.

```bash
python -m ruff check
python -m pytest tests/test_wiki_canary.py tests/test_patch_branch_readback.py \
                 tests/test_bug_investigation_wiring.py -q
```

**Green:** ruff clean, three test files pass.

If you want a deeper sanity, run the full suite — but expect ~5 min wall
clock; it's optional.

---

## §5 — End-to-end fix validation

For each fix that the redeploy unblocks, run the smallest verification
that proves the fix is live in the deployed image (not just in main).

### §5.1 BUG-028 — slug normalization + bug-investigation auto-trigger

**What changed:** `_wiki_file_bug` now sanitizes slugs case-insensitively
(BUG-028 alias detection); filing a bug emits a `bug_investigation` request
into the dispatcher queue.

**Validate:**

```bash
# File a test bug via the live MCP wiki tool. Use a unique title to avoid
# tripping the dedup-at-filing-time logic (per `feedback_grep_all_call_sites_after_primary_change`).
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
python scripts/mcp_tool_canary.py --url https://tinyassets.io/mcp --verbose \
  > /dev/null 2>&1  # warm up handshake; canary side-effect is OK

# Issue an actual file_bug call. Easiest path: paste the args into Claude.ai
# chat (not via canary script — file_bug isn't in the canary repertoire).
# Prompt:
#   File a bug on the workflow connector: title "post-redeploy-check ${TIMESTAMP}",
#   component="post-redeploy-validation", severity="cosmetic",
#   repro="Run the post-redeploy validation runbook §5.1.",
#   observed="Test bug filed by validation runbook.",
#   expected="Bug-investigation auto-runs; patch packet appears as comment."
echo "Manual step: file the bug above via Claude.ai chat. Note the BUG-NNN id."
```

**Green criteria:**
- `file_bug` returns `status=filed` with a fresh `BUG-NNN` id (not
  `status=similar_found` — if it does, your title hit dedup; bump the
  timestamp suffix and retry).
- The new bug page appears at `pages/bugs/BUG-NNN-...md` with the slug
  in lowercase (BUG-028's alias-resolution working — no uppercase variant
  exists).
- Dispatcher logs (host shares) show a `bug_investigation` request
  enqueued for the new BUG id within ~30s of filing.

**Red:**
- `file_bug` returns `error` or `Unexpected keyword argument` → wiki
  surface broken in the deployed image. Re-check §2.2.
- New page slug has uppercase characters or BUG-028 warning is logged →
  alias logic regressed.
- No dispatcher request enqueued → bug_investigation auto-trigger broken.
  Confirm `TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID` env var is set on the
  droplet (see §5.6 for the canonical-bind step).

### §5.2 `goals action=set_canonical` (Mark-canonical decision unblock)

**What changed:** `set_canonical` is a Goal→Branch promotion verb. Pre-
redeploy, the deployed image returned 404 on this action. Post-redeploy,
it should accept.

**Validate (read-only first, then write if host is ready to commit the
Mark-canonical decision):**

```
# Via Claude.ai chat — read-only path:
Use the goals tool to inspect any existing Goal: goals action=inspect goal_id=<id>
If goal has bindings: goals action=resolve_canonical goal_id=<id>
```

**Green for read-only validation:** action returns valid JSON with the
binding shape (or empty bindings list); no 404, no `unknown action` error.

**For the Mark-canonical write path** (only if host has decided to mark
Mark's `change_loop_v1` (`fd5c66b1d87d`) as canonical for the
`bug_investigation` Goal — this is the host-decision row in STATUS):

```
# Via Claude.ai chat:
goals action=set_canonical goal_id=<bug_investigation_goal_id> branch_def_id=fd5c66b1d87d
```

**Green for write path:** action returns `status=set` (or equivalent), and
a follow-up `goals action=resolve_canonical goal_id=<id>` returns
`fd5c66b1d87d`.

**Red:** 404 / `unknown action` → set_canonical not in deployed image.
Re-check §1.1 image tag — it must include commit `d06a6d7` or later.

### §5.3 `patch_branch` read-after-write contract (BUG-030)

**What changed:** `_ext_branch_patch` returns `post_patch` + `patched_fields`
in the response — chatbot can verify a rename took effect without a
separate `describe_branch` call. (Code already shipped; this validates the
deployed image carries it.)

**Validate:**

```
# Via Claude.ai chat — pick or create a test branch first:
extensions action=build_branch domain="fantasy" name="post-redeploy-test-branch"
   nodes=[{"node_id":"n1","tool":"writer_tools.echo"}] entry_point="n1"
# Note the returned branch_def_id.

# Then patch its name:
extensions action=patch_branch branch_def_id=<id>
   changes_json='[{"op":"set_name","name":"post-redeploy-renamed"}]'
```

**Green criteria (in the patch_branch response):**
- `status=patched`.
- `post_patch.name == "post-redeploy-renamed"`.
- `patched_fields` includes `"name"`.
- `name_updated == true`.
- `new_name == "post-redeploy-renamed"`.

**Red:** response missing `post_patch` field → deployed image predates the
read-after-write fix. Re-check §1.1 image tag.

### §5.4 Failure-class shape (BUG-029)

**What changed:** Run-failure error responses now include a structured
`failure_class` + `actionable_by` payload (e.g., `actionable_by="user"` or
`"host"`) so the chatbot can give the user clear next-action guidance.
(Code shipped; validates the deployed image carries it.)

**Validate:**

Easiest way to surface a failure: read the existing failed run from
Mara's session.

```
# Via Claude.ai chat:
runs action=inspect run_id=ae8d92f18907459e
```

(That's Mara's failed `extract_claims` run from `.agents/activity.log`
2026-04-24. If host has cleaned it up, file a fresh failing run by
binding a branch to a missing-provider config and triggering a run.)

**Green criteria (in the failure payload):**
- `failure_class` field present with a string value (e.g.,
  `"empty_llm_response"`, `"provider_exhaustion"`).
- `actionable_by` field present with one of `"user"`, `"host"`,
  `"developer"`.
- `suggested_action` field present with a human-readable next step
  (e.g., "Ask host to verify provider keys are set.").

**Red:** raw error string with no structured fields → BUG-029 fix not in
deployed image. Re-check §1.1.

### §5.5 Wiki status migration (#32)

**What this is:** STATUS work row asks for a batch of wiki-page status
updates (BUG-002/003/007/014A/015/016/018/020 status fields + tier-1 page
closing paragraph + BUG-003 dup cleanup). All edits go via live `wiki
action=write`, which depends on the wiki write surface being healthy.

**Pre-flight:** §2.2 wiki canary must be green. If §2.2 is green, the
write surface is good and the migration can proceed.

**Validate (after performing one migration write as a smoke):**

```
# Via Claude.ai chat — pick the smallest migration item from STATUS row:
wiki action=read page="BUG-007"
# Note the current status field. Then update:
wiki action=write filename="BUG-007" category="bugs"
   content="<updated content with status=closed>"
# Verify:
wiki action=read page="BUG-007"
```

**Green criteria:** read returns the new content; status field reflects
the migration. Repeat for the rest of the batch in `STATUS.md` Work row
"Wiki status migration (#32)".

**This step is sequential, not gate-blocking:** if the smoke write works,
the rest of the migration can proceed without a per-item validation step.
Track in STATUS Work row, delete row when batch complete.

### §5.6 Mark's `change_loop_v1` end-to-end milestone

Per `docs/design-notes/2026-04-26-mark-change-loop-status-mapping.md` (deleted 2026-10-06; in git history) R3:
the "live loop" milestone needs cloud redeploy + Task #82 substrate land
+ host setting an env var. After §5.1-§5.4 all green:

```bash
# 1. Bind change_loop_v1 to the bug_investigation Goal
#    (host or lead, via Claude.ai):
#    goals action=bind goal_id=<bug_investigation_goal_id> branch_def_id=fd5c66b1d87d
#
# 2. Set canonical (already covered in §5.2):
#    goals action=set_canonical goal_id=<id> branch_def_id=fd5c66b1d87d
#
# 3. Host SSHes to droplet and adds env var:
sudo /tmp/install-workflow-env.sh # OR via the helper from deploy/install-workflow-env.sh
# Specifically:
#    scp deploy/install-workflow-env.sh root@<droplet>:/tmp/
#    echo "fd5c66b1d87d" | ssh root@<droplet> \
#        "sudo bash /tmp/install-workflow-env.sh set TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID"
#    ssh root@<droplet> "sudo systemctl restart tinyassets-daemon"
```

(The env-var helper is the Task #9 helper that lands with the install/640
fix; if Task #9 hasn't deployed yet, fall back to the legacy `sed -i`
chain documented in `docs/ops/host-independence-runbook.md` §rotate-secret.)

**Green criteria:**
- File a fresh test bug per §5.1.
- Dispatcher claims it within ~30s.
- A `change_loop_v1` run starts (visible via `runs action=inspect` or
  scheduler logs).
- A patch packet output appears as a comment on the bug page within
  the run's expected duration.

**Red:** any step fails → split into two questions: was it the env var
(re-confirm with `ssh ... 'sudo grep TINYASSETS_BUG_INVESTIGATION /etc/tinyassets/env'`)
or the dispatcher routing (host shares dispatcher logs from `journalctl`).

---

## §6 — Recent-commit propagation spot-checks

These commits are recent enough that they may not have made it into the
deployed image if §1.1 image tag is older than them. Quick spot-check.

### §6.1 Sub-branch invocation Phase A item 5 close-out (`a12e284`)

**What's in it:** two-pool concurrent-runs model, env-tunable
`TINYASSETS_CHILD_POOL_SIZE`, `_invocation_depth` runtime threading,
`MAX_INVOKE_BRANCH_DEPTH=5`.

**Validate (quick):**

```bash
# Confirm the env var is honored (or its default applies). Host:
ssh root@<droplet> "sudo grep TINYASSETS_CHILD_POOL_SIZE /etc/tinyassets/env || echo 'not set (default 4 applies)'"
```

For the deeper validation, build a parent branch that invokes a child
sub-branch (per `tests/test_sub_branch_invocation.py` shape) and confirm
it runs without a depth-limit or pool-saturation error. This is optional
unless you have evidence sub-branch invocation is broken in the wild.

### §6.2 Bounty-calc query template substrate (`373df03`)

**What's in it:** Substrate for the bounty-pool dispatch primitive
(navigator-vetted PASS this session).

**Validate:** No live-tool surface yet for this substrate (it's a
foundation for `extensions action=score_branches` per the design note).
Spot-check: `git log --oneline | grep 373df03` confirms the commit is in
main; the deploy-pipeline already pinned that SHA. No live-call check
needed until the consumer surface lands.

### §6.3 Dispatcher request_type substrate

**What's in it:** Tasks #46/#47 — request_type field on dispatcher
queue items so file_bug can route through the dispatcher
(`d06a6d7`/`79a3c28`/`218d9ec`/`c686b48`).

**Validate:** Implicit via §5.1 — if a `bug_investigation` request
appears in the dispatcher queue after filing a test bug, this substrate
is live in the deployed image.

---

## §7 — Watchdog observation (post-Task #20)

**Background:** Pre-2026-04-26 the watchdog wrote restart events to
`.agents/uptime_alarms.log` (a repo-relative path). Audit
`docs/audits/2026-04-26-restart-loop-correlation.md` proved that file
was test-pollution, not production telemetry. **Task #20 R2 moved the
production alarm path to `/var/log/tinyassets/uptime_alarms.log`** (env
var `TINYASSETS_WATCHDOG_ALARM_LOG` overrideable; systemd creates the
dir via `LogsDirectory=workflow` in `deploy/tinyassets-watchdog.service`).
Local `.agents/uptime_alarms.log` is now `.gitignore`d and produced
only by tests against tmp paths.

The right surface for production observation is journald
(`journalctl -u tinyassets-watchdog.service`) — `SyslogIdentifier=tinyassets-watchdog`
in the .service unit pipes both stdout + stderr there. The structured
alarm-log file at `/var/log/tinyassets/uptime_alarms.log` is secondary.

### §7.1 Observation window

After §1-§6 complete, watch the watchdog journal for **5 minutes wall
clock** with no other manual interventions.

```bash
# Host-side via SSH:
ssh root@<droplet> "journalctl -u tinyassets-watchdog.service -f --since '5 minutes ago'"
```

Optional secondary surface (the structured alarm file):

```bash
ssh root@<droplet> "tail -F /var/log/tinyassets/uptime_alarms.log"
```

**Green:** Zero new `WATCHDOG_RESTART` entries during the 5-min window,
OR the cadence is materially different from any pre-redeploy baseline
established in journald (e.g., one entry then quiet) AND `.agents/uptime.log`
or the public canary shows continuous GREEN entries throughout.

**Yellow:** New entries appear but at a slower cadence than baseline
(e.g., one in 5 min instead of more frequent) — log a watch item in
STATUS Concerns: "watchdog cadence post-2026-04-26-redeploy: <observed
rate>". Don't block the validation; this becomes the new baseline for
future audits.

**Red:** New entries appear at a tight cadence (e.g., every 30s — the
timer cadence) → the watchdog is restart-looping in production. File a
P0 task; do NOT mark redeploy "clean" in STATUS. Run §7.2 triage.

### §7.2 If red — quick triage hooks

```bash
# Host-side: what's the watchdog actually probing?
ssh root@<droplet> "journalctl -u tinyassets-watchdog -n 50 --no-pager"
# What's the daemon doing right before each restart?
ssh root@<droplet> "journalctl -u tinyassets-daemon -n 100 --no-pager"
```

Compare against `.agents/uptime.log` to find the GREEN→RED→GREEN flap
window. Surface findings to navigator for the deferred audit.

---

## §8 — STATUS.md trim guidance

After §1-§7 complete and overall validation is GREEN, the following
STATUS.md rows can be deleted (the redeploy resolved them):

**Rows that go away:**

- Work row "Cloud daemon redeploy — picks up BUG-028 + #30 + #14 + others"
  (host-action) → DELETE. The deploy is done.
- Work row "Wiki status migration (#32) ... | post-deploy" → DELETE the
  "post-deploy" gate marker; if the actual migration runs are still in
  progress, keep the row but flip Status to `claimed:lead` or similar.
- Concern "Cloud daemon redeploy — picks up..." (if duplicated as a
  Concerns line) → DELETE.

**Rows that STAY (not addressed by this redeploy):**

- "P0 revert-loop: daemon PAUSED" — separate provider-stack issue; keep
  until host explicitly lifts the pause AND a test run completes without
  re-tripping the loop.
- "R7 storage-split status confirmation" — host-decision, unrelated to
  redeploy.
- "Mark-branch canonical decision" — STAYS unless §5.2 write-path was
  exercised AND host confirmed the binding is intentional. Flip to
  "decided" rather than delete if the latter.
- "#28 + #29 audit-doc review" — host-review item, separate from deploy.
- "/etc/tinyassets/env mode flip — Fix A awaits host review" — Task #9 is
  in verifier's queue; STAYS until that lands.
- "Layer-3 design session", "Memory-scope Stage 2c flag", "Remove provider
  + DO keys from persistent uptime surfaces", arch-audit rows — all
  unrelated.
- ChatGPT Concerns (publish blocked, approval bug, run-branch stalled,
  name-based refs) — ChatGPT-side issues, not redeploy-affected.

**Rows whose status updates** (vs delete):

- Concerns about BUG-029/BUG-030 — if reproduction steps in §5.3/§5.4
  show GREEN, append `[validated post-2026-04-26-redeploy]` and queue for
  next STATUS curation pass; don't delete unilaterally (per
  `feedback_status_md_host_managed`).

---

## Appendix A — If validation goes RED somewhere

Single-most-important rule: **do not declare the redeploy "clean" in
STATUS just because §1-§4 are green.** §5 fix-validation is the part that
matters for the unblocked-fixes ledger. A green canary with a broken
patch_branch surface means the redeploy succeeded as a deploy but didn't
deliver the fixes.

Triage hierarchy if anything goes RED:

1. **§1 RED** → redeploy itself is broken. Run
   `docs/ops/cloud-daemon-restart.md` → roll back if needed via
   `.github/workflows/deploy-prod.yml` rollback step.
2. **§2 RED but §1 GREEN** → tool surface broken; tunnel + handshake OK.
   Triage which tool: §2.1 RED = MCP itself, §2.2 RED = wiki write/read,
   §2.3 RED = tool registration. File a fresh BUG via §5.1 (if §5.1 still
   works) or via Claude.ai chat directly.
3. **§5 RED in one fix-validation but rest green** → that specific fix
   didn't deploy. §1.1 image tag check should reveal whether the relevant
   commit was excluded.
4. **§7 RED watchdog loop continues** → re-open navigator's deferred
   audit. Log "watchdog post-redeploy did NOT clear" in STATUS Concerns.

---

## Appendix B — Why this runbook exists

Cloud-daemon-redeploy is a host-action with leverage across ~10 STATUS
rows. Without a checklist, post-redeploy validation is ad hoc — lead
spends a session running checks one at a time and remembers some, misses
others. Source: lead session-planning 2026-04-26 (Task #17).

Companion docs:
- `docs/ops/cloud-daemon-restart.md` — restart-only ops, no deploy.
- `docs/ops/acceptance-probe-catalog.md` — named probe definitions
  (PROBE-001 to PROBE-004).
- `docs/design-notes/2026-04-26-mark-change-loop-status-mapping.md` (deleted 2026-10-06; in git history) —
  Mark's gap mapping; §5.6 implements the R3 wire-up step.
- `STATUS.md` — live source of truth for which rows close.

# Distributed-Execution Platform — Resume Handoff Spec

**Written:** 2026-07-19 (paused mid-build for Kimi Code to take over the driver seat).
**Driver on resume:** Kimi Code (kimi-k3), which can dispatch Codex (gpt-5.6-sol) and Fable-5.
**Prior driver:** Claude Code (Opus) lead. Build was paused by host directive so Kimi can pick it up.

---

## 0. How to use this document

This is the **live-state + next-action** layer. It does **not** re-specify the slice
contracts — those are canonical in the **design/exec plan**:

> `2026-07-18-distributed-execution-platform.md (archived in git)`
> (currently on branch **`feat/patch-loop-runner`**, not yet on `main`.
> Read it with: `git show feat/patch-loop-runner:2026-07-18-distributed-execution-platform.md (archived in git)`)

That plan owns: the execution-capsule contract (§5), Layer-A isolated executor (§6),
typed result contract (§7), Layer-B2 daemon protocol (§8), Layer-B3 market (§9),
PLAN reconciliation (§10), the **25 security invariants (§11)**, the **build slices
S0–S16 (§12)**, the **B2/B3 acceptance criteria (§13/§14)**, open risks (§15), the
**reviewer attack checklist (§16)**, and the **binding Fable-5 amendments (§17)** that
must be folded into each slice before it is built.

**Read order for a fresh driver:** this doc → exec plan §12 (slices) + §17 (amendments)
→ exec plan §11 (invariants) → §5/§7/§8 for the slice you're building. Then §5 below
here for the one blocked slice (S2).

---

## 1. TL;DR — where we are, what's next

- **Goal:** stand up the distributed patch-loop execution platform to the **first B2
  live test** (S11): a real chatbot connector creates a public-repo job; an
  owner-enrolled Windows daemon (WSL2/Podman) claims it, executes in a sandbox,
  revalidates, uploads a content-addressed typed result, and a *separately authorized*
  effect opens a reviewable PR — **with zero platform compute workers**.
- **Done + merged:** the security substrate **S0, S1, S3, S5** — all four passed the
  dual-family gate and are merged into the integration branch **`feat/patch-loop-runner`**
  (head **`c11b145b`**, draft **PR #1472**). 335 substrate tests pass; vault CORE
  zero-diff; plugin-mirror byte-parity.
- **The one blocked slice: S2 (fenced lease store).** Branch
  **`feat/patch-loop-leasestore`** @ **`a666afca`** (pushed to origin). This "fix-1"
  commit was **REJECTED** by Codex re-review for two blocking findings (see §5). A
  "fix-2" was **interrupted mid-build** when the session paused — its partial edits are
  **unverified, do not trust them** (see §5.4).
- **IMMEDIATE NEXT ACTION → finish S2** per §5, re-gate it (Codex + Fable on the same
  commit), then merge the two S2 commits into `feat/patch-loop-runner`.
- **After S2:** S4 → S6 → S7 (needs a broker sub-spec first) → S8 → S9 → S10 → S11.
  See §7. S12–S16 are B3 (market) and come *after* the B2 live test.
- **Parked (host-gated, NOT part of this build):** the platform-vision reframe
  ("open production commons"). See §9. Do not act on it without host approval.

---

## 2. Slice status board

Legend: ✅ merged into runner · 🔴 blocked/in-repair · ⬜ not started · ⏸ later phase (B3)

| Slice | What it is | Status | Branch / location |
|---|---|---|---|
| **S0** | Control-plane invariant: zero platform execution workers; retire prod `cloud_worker`; compute invariant in PLAN | ✅ merged | `feat/patch-loop-runner` @ c11b145b (PR #1472) |
| **S1** | Capsule + scope contracts: JCS/hash/Ed25519 sig; `LEGACY_UNBOUND` permanently ineligible for sandbox classes; no path fields | ✅ merged | same |
| **S3** | Device identity + revocation: device-key enrollment, 5-min bound creds, nonce replay defense, epoch revocation; no daemon service-role | ✅ merged | same |
| **S5** | Blob + typed-result protocol: CAS upload, hash/size commit, `ExecutionResultV1`, fenced completion CAS (JCS-canonicalized per §17) | ✅ merged | same |
| **S2** | **Fenced lease store** (SQLite `BEGIN IMMEDIATE` sole authority; monotonic fence; atomic claim; CAS refs) | 🔴 **rejected fix-1; finish per §5** | `feat/patch-loop-leasestore` @ a666afca |
| **S4** | B2 polling/claim/heartbeat API (long-poll, atomic claim, 30s independent heartbeat, 120s lease, idempotency) + **§17 heartbeat-response amendment** | ⬜ next after S2 | — |
| **S6** | Linux narrow launcher (fixed bwrap/rootless-OCI, namespaces/cgroups/seccomp, no egress, child-only OS attestation) | ⬜ | — |
| **S7** | Model broker (UDS-only child capability; job/fence/model/budget bound; no raw key in child) — **§17: broker protocol sub-spec REQUIRED + dual-family reviewed BEFORE build** | ⬜ | — |
| **S8** | Staging/extraction/revalidation/destruction (credential-free exact-base staging; typed patch; fresh-base apply; confirmed cleanup) | ⬜ | — |
| **S9** | Windows WSL2/Podman backend + **§17 WSL2 model-broker transport bridge amendment** | ⬜ | — |
| **S10** | Engine routing: `host_daemon` externally dispatchable; **§17: legacy tray ENROLLS + claims via B2 (one protocol, no dual path)** | ⬜ | — |
| **S11** | **First B2 end-to-end live test** (rendered chatbot proof + ≥1,000-daemon load proof + stale-result rejection + zero platform workers) | ⬜ | — |
| **S12–S16** | Source-exec (S12); B3 market selector/settlement/reputation (S13/S14); private source delivery (S15); B3 live test (S16) | ⏸ B3, later | — |

**B2 live test needs S0–S11 (S12+ are B3).** Full acceptance = exec plan §13.

---

## 3. The gate protocol (unchanged — you are the DRIVER, not the approver)

Every slice merges only after a **dual-family gate on the same commit**:

- **Family A (implementer):** builds the slice + supplies focused tests and evidence.
- **Family B (opposite family):** reviews the *current diff*, attacks the named
  invariant, and independently reruns/reproduces the evidence.
- **A slice does not merge until BOTH verdicts are recorded.**

**Approval rule (host directive — `dual-family-latest-model-approval`):** a slice is
approved only when the **latest model of BOTH families — Fable-5 AND gpt-5.6-sol
(Codex) — approve the SAME commit.** Older-model verdicts are signal, not approval.

**Kimi's place in this (important):** Kimi (kimi-k3) is a **THIRD family = extra
signal, never a substitute** for the Fable-5 + Codex pair. As the driver, **Kimi builds
and orchestrates, but must NOT self-approve its own build** (independence requirement).
So: **Kimi builds → Codex adversarially reviews → Fable-5 reviews → both must approve
the same commit before merge.** Serialize the reviews (Codex first, then Fable only
after Codex approves) to conserve rate-limit budget. If Codex rejects, fix and
re-review before spending Fable.

**Builder discipline (host directive — `codex-as-builder-on-nonconvergence`):** Codex is
the **default builder for security-substrate / hardening** work on this project (one
Codex round closed what ~10 Claude rounds could not on the vault). If Kimi's own build
does not converge in a couple of rounds on a hard substrate slice, **hand the build to
Codex** (`workspace-write`) and keep Fable/Codex as the review pair.

---

## 4. How to dispatch each family (repo scripts — read-only reviews)

Both live at repo root and write the peer's final message to `--out`; run them **backgrounded**
(both models are slow) and read the out-file when done. `peer_agent.py` sends your prompt verbatim, so **ask for the `VERDICT:` line in the prompt** -- nothing appends it for you (the retired `codex_review` wrapper, which did, was deleted 2026-08-26).

**Codex review (read-only, adversarial):**
```
python scripts/peer_agent.py codex --out <file>.md --cwd <worktree> \
    --prompt "<attack the named invariant; re-run the evidence>; end with exactly one line: VERDICT: APPROVE|ADAPT|REJECT"
```
Under the hood: `codex.cmd exec -s read-only -c approval_policy=never -C <cwd> -o <out> -`.

**Codex BUILD (workspace-write — when Codex is the builder):**
```
cat prompt.txt | codex.cmd exec -s workspace-write -c approval_policy=never \
    -C <worktree> -o <out>.md -  > <stdout>.log 2>&1
```
Keep it to ONE worktree; never `main`; never force-push.

**Kimi review — RETIRED 2026-08-26.** `scripts/kimi_review.py` is deleted and
`peer_agent.py` accepts only `claude` and `codex`, so there is no third-family
dispatch route any more; the harness reset scoped this repo to two providers.
The Kimi history below (§5.5, §10) is kept as a record of the S2 A/B, not as an
instruction. Running Kimi again means adding a provider to `peer_agent.py` first.

**Fable-5 review:** the opposite-family reviewer in the pair. Dispatch it through
whatever mechanism the driver harness provides (Claude Code used its `Agent` tool with
`model: fable`; Kimi Code uses its own Fable call). The *substance* is what matters:
Fable must re-read the actual diff + cited code, attack the slice's named invariant
(exec plan §12 gate column + §16 checklist), and independently reproduce the evidence —
never rubber-stamp.

---

## 5. THE IMMEDIATE TASK — finish S2 (fenced lease store)

### 5.1 Base
- **Authoritative base:** `origin/feat/patch-loop-leasestore` @ **`a666afca`** (pushed).
  History: `c11b145b` (runner substrate) → `35f56034` (S2 lease store) → `a666afca`
  (S2 fix-1, **REJECTED**).
- Do **not** merge `main`. Do **not** force-push. Push only to
  `feat/patch-loop-leasestore`.

### 5.2 Why a666afca was rejected — the TWO blocking findings
Full verdict: `C:/Users/Jonathan/.claude/jobs/aaaa5b09/tmp/s2-fix-rereview-codex-verdict.md`.
FIX 1 (capsule side-effect follows the winning claim CAS) and the SQLite fence mechanism
are **CLOSED — do not churn them.** The two open blockers:

**FINDING A — completion authority still open.** Removing `LeaseStore.submit_result()` /
`.complete()` was not enough: **`LeaseStore.atomic_update()`** (`tinyassets/runtime/lease_store.py`
~line 643) is *still a public completion authority*. It permits `leased → succeeded|
failed|cancelled` requiring only syntactically-valid EQUAL sha256 hashes (~line 654),
then persists the terminal status (~line 684). Proven hole:
`LeaseStore.atomic_update(all-zero candidate/accepted hash, status="succeeded")` was
**accepted and persisted**, bypassing S5's `ExecutionResultV1` signature/schema/
canonical-hash validation in `tinyassets/api/execution_jobs.py` `complete_job()`. The
branch's own `tests/test_lease_store.py:287` demonstrates the bypass, and the
"no unvalidated path" assertion at `test_lease_store.py:317` only checks two *removed
method names* — it does not exercise the surface.

> **FIX A:** make S5's validated `complete_job()` the **only** path that can drive a
> terminal transition. `atomic_update` must not let an arbitrary caller terminalize with
> unvalidated hashes. Cleanest option: `atomic_update` no longer accepts terminal-status
> transitions from public callers — expose only the fenced-transaction *primitive* that
> `complete_job` composes under a validated `ExecutionResultV1`; OR gate terminal
> transitions behind an internal precondition/token that only `complete_job` sets after
> S5 validation. **Invariant: NO public method terminalizes a job without S5 validation.**
> **Test:** rewrite the "no unvalidated path" test to actually CALL every reachable
> public terminalization surface with an opaque/all-zero/unsigned hash and assert they
> ALL reject, while the S5-validated path still completes exactly once. Mutation-RED the
> lockdown (prove the test fails if the guard is removed).

**FINDING B — daemon claim migration unsound (LIVE OUTAGE + a gamed test).** a666afca
made JSON `claim_task()` fail-loud (`LegacyClaimAuthorityDisabled`), but left **no
working replacement claimer**: the daemon `_try_dispatcher_pick()`
(`fantasy_daemon/__main__.py` ~line 482) selects a pending task, logs a refusal, and
returns no claim; nothing is wired to `LeaseStore.claim()`. Production enables
`TINYASSETS_SOUL_LOOP_DISPATCH` (`.github/workflows/deploy-prod.yml:445`) and
`_run_graph()` (`__main__.py` ~1770) invokes this dispatcher — so pending child
BranchTasks that **used to execute now stall indefinitely = user-visible execution
outage.** Worse, `tests/test_soul_loop_dispatch.py:134` was **changed to assert the
refusal**, masking the outage. (This is the `never-game-the-gate` anti-pattern: do not
change an assertion to assert the broken behavior.)

> **FIX B:** do **not** retire the live JSON daemon execution route yet — the lease
> store's `claim()` needs a signed capsule/Order that **S4/S10 supplies and that does
> not exist yet.** So: **REVERT the daemon fail-close** — the JSON dispatcher route must
> keep executing pending BranchTasks exactly as before a666afca (no outage). Keep
> `claim_task()` functional for the daemon's current route. **RESTORE**
> `test_soul_loop_dispatch.py` to assert the daemon actually **executes/starts** a
> pending task (un-game it). A design-guard test racing JSON-vs-SQLite single-claim may
> remain, but not at the cost of the working daemon route. Document (code comment +
> report): the lease store is the **designated** single claim authority but is **dormant
> in production** (no production claimer yet, so there is no live dual-claim); migrating
> the daemon to `LeaseStore.claim` (retiring the JSON route) is an explicit **S4/S10
> deliverable** gated on the capsule/Order route. This aligns with exec plan §17's
> [S10] amendment (the owner tray *enrolls* and claims via B2 — one protocol — only once
> that route exists).

### 5.3 Proof required to clear S2 (record in a report file)
- **FINDING A:** all-zero-hash `atomic_update` terminalization now **rejects via every
  public surface** (before/after + mutation-RED); the S5-validated path still completes
  exactly once.
- **FINDING B:** the daemon dispatcher again **executes** a pending BranchTask (show the
  reverted `__main__.py` diff + the un-gamed test passing the *execute* assertion).
- FIX 1 (capsule-in-CAS) + the fence mechanism still green; capsule (124) / S5 /
  device-auth / lease suites pass.
- The broad `tests/test_branch_tasks.py tests/test_dispatcher_queue.py` gate's only
  failures are the **pre-existing `universe_loop_not_declared` set** (11 failures,
  identical to the `c11b145b` baseline — **no NEW failure**), and the soul-loop-dispatch
  queue test now PASSES the un-gamed assertion.
- Plugin-mirror byte-parity (`packaging/claude-plugin/.../runtime/tinyassets/branch_tasks.py`
  identical SHA-256 to the root copy); vault CORE zero-diff; `ruff` + `compileall` +
  `git diff --check` clean.
- **Both-family verdicts recorded** (Codex + Fable approve `a666afca`'s successor
  commit). Then merge the two S2 commits into `feat/patch-loop-runner`.

### 5.4 ⚠ Interrupted local worktree — do not trust the partial fix-2
`C:/Users/Jonathan/Projects/wf-patch-loop-leasestore` (branch `feat/patch-loop-leasestore`
@ a666afca) contains **12 uncommitted modified files** from a Codex "fix-2" run that was
**paused mid-flight and never verified**: `fantasy_daemon/__main__.py`,
`tinyassets/api/execution_jobs.py`, `tinyassets/branch_tasks.py` (+ plugin mirror),
`tinyassets/runtime/lease_store.py`, and 6 test files. These edits touch the *right*
files but are **incomplete and unproven**.

**Recommendation:** start FIX A/FIX B **fresh** rather than stacking on the rejected
a666afca. Two viable bases: (a) rebuild on top of `a666afca` (keeps fix-1's capsule-CAS
work, adds FIX A/B); or (b) **build S2 in one clean pass from `35f56034`** (the base
*before* the rejected fix-1) — this is what the Kimi candidate below does and it avoids
inheriting the gamed-test lineage. Discarding the interrupted uncommitted edits is a
**destructive git op** — under **Hard Rule 13** get explicit host approval before
`git restore`/`checkout --`/`reset`; otherwise just work in a fresh worktree.

### 5.5 A candidate S2 solution to evaluate: `kimi-s2-candidate.patch`
This handoff folder ships **`kimi-s2-candidate.patch`** — Kimi's ~48 KB single-pass S2
fix built from `35f56034` (see §10 for provenance and caveats). **It is unverified and
not directly `git apply`-able** (`git apply --check` → "corrupt at line 19"; treat it as
a *design reference*, not a drop-in patch). Its approach is worth adopting because it
appears to close both rejection findings by construction:
- **FINDING A:** exposes `atomic_update` as the *only* fenced primitive and makes
  `complete_job` the *sole* validated completion authority (removes the public
  `submit_result`/`complete` shortcuts entirely); adds `test_lease_store_s5_completion.py`.
- **FINDING B:** **keeps JSON `claim_task` functional** as the single claim authority
  (fail-closed on blank/shared daemon identity) instead of retiring it — so the daemon
  route keeps executing (no outage) and no test is gamed; adds
  `test_branch_tasks_single_claim_authority.py`.

**How to use it:** reconstruct the fix from Kimi's intent (or have Codex re-emit it as a
clean `workspace-write` commit), then run it through the §5.3 proof + the §3 dual-family
gate. Verify specifically: does keeping `claim_task` authoritative create a *dual* claim
path with `LeaseStore.claim` (Rule 11), or is the lease store genuinely dormant (no
production caller) so there is no live dual-claim? And does the all-zero-hash probe now
reject on *every* public terminalization surface?

---

## 6. Hard guardrails (non-negotiable — apply to EVERY slice)

From `AGENTS.md` Hard Rules + host directives + review-pattern memories. Violating any of
these fails the gate.

1. **No shims / no dual paths (Rule 11).** Replace bad shapes at the boundary; one clean
   route. (This is exactly why FIX B documents the JSON route as *temporary until S4/S10*,
   not a permanent parallel authority.)
2. **Fail loud, never silently (Rule 8).** No mock fallback that looks like real output.
   Unresolved external engine source → **fail closed** (job stays pending), never ambient
   (`ambient-credential-fallback-is-an-identity-leak`).
3. **Never game the gate.** Do not weaken/xfail/skip/rewrite an assertion to make a
   review pass. Report pre-existing baseline debt honestly; fix it in its own lane
   (`never-game-the-gate-with-xfail`).
4. **Credentials never in `os.environ`, logs, or prompts.** The control plane scrubs
   provider creds from `os.environ`; the job child has no raw provider/GitHub/vault/MCP/
   platform-admin credential (exec plan §11.10, §6.3). Vault CORE stays **zero-diff**
   unless the task IS the vault.
5. **No destructive git ops without explicit host approval (Rule 14):** no
   `reset --hard`, `checkout --`, `restore`, `clean`, force-push, stash-drop as cleanup.
   Never switch a dirty checkout to `main`; start a clean main-based worktree.
6. **Never push to `main`, never force-push, never merge to `main`.** Slice branches
   fold into `feat/patch-loop-runner` (the integration branch); the runner→main merge is
   a separate host-gated step.
7. **Plugin-mirror byte-parity.** Any edit to `tinyassets/*` that has a mirror under
   `packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/` must
   keep the two byte-identical (verify SHA-256).
8. **Test-temp hygiene / self-protection.** Point every pytest/script
   `TINYASSETS_DATA_DIR` (and `--basetemp`/`TMPDIR`) at a unique dir **OUTSIDE the repo
   and OUTSIDE `%APPDATA%`** (e.g. `$env:TEMP\ta-<slice>-<uniq>`). Sandbox-created temp
   dirs inside the repo get unreadable ACLs on Windows and block worktree teardown.
   `%APPDATA%\TinyAssets` cleanup + any prod credential removal are **host-gated**.
9. **Public-surface probes.** Any change to DNS / Cloudflare tunnel / Worker / connector
   catalog requires a post-change `scripts/mcp_public_canary.py` green probe
   (canonical endpoint `https://tinyassets.io/mcp`). Not expected for these substrate
   slices, but do not touch live infra.
10. **Python 3.11+; `ruff check` clean before commit.** (Local runs may be on 3.14 — the
    LangGraph coroutine-deprecation warnings are pre-existing, not yours.)
11. **Fold the slice's §17 amendment in BEFORE building it,** and make its dual-family
    gate verify it (S4 heartbeat-response; S5 already merged with JCS canonicalization +
    blob quota/TTL; S7 broker sub-spec; S9 WSL2 broker transport; S10 tray enrollment).

**Cross-family review patterns** worth pre-loading (memory `cross-family-review-patterns`):
vacuous-mock mutate-probe, idempotency terminals, latest-effective-review,
generation-bound receipts, vault-only construction, authority-from-persisted-record,
config-text-guard ≠ runtime-gate, mutation-test-fail-closed-default,
exact-sentinel-not-substring, canonicalize-one-parse, pin-test-data-dir. These are the
attacks that have actually caught defects in this build.

---

## 7. Remaining slice sequence after S2

Build order to the B2 live test (each is a full dual-family gate; fold its §17 amendment
in first):

1. **S4 — B2 polling/claim/heartbeat API.** Long-poll, atomic claim, 30s independent
   heartbeat, 120s lease, idempotency. **§17 amendment:** define the heartbeat RESPONSE
   schema `{ lease_extended_to, directive: "continue"|"cancel" }` so owner cancellation
   flows over the lease channel. **S4 is also where the daemon's real SQLite claim route
   lands** — the thing FIX B is deferring to. Load test heads-up (§17): 1,000
   long-polling daemons against the single-writer FS MVP will pressure the Postgres
   decision early.
2. **S6 — Linux narrow launcher.** Fixed bwrap/rootless-OCI, namespaces/cgroups/seccomp,
   no egress, child-only OS attestation. Escape suite is the gate.
3. **S7 — Model broker.** **§17: write the broker protocol sub-spec FIRST and get it
   dual-family reviewed before building** (largest attack surface — in-child channel auth,
   per-call fence check, B3 owner-account-runs-untrusted-prompts exposure).
4. **S8 — Staging/extraction/revalidation/destruction.** Credential-free exact-base
   staging; typed patch extraction; fresh-base apply; confirmed cleanup. Malicious-archive
   / path-traversal / symlink / TOCTOU suite.
5. **S9 — Windows WSL2/Podman backend.** **§17: name the WSL2 model-broker transport
   bridge** (host UDS can't cross the WSL2 VM boundary — vsock / authenticated localhost
   forward, preserving `network: model_broker_only`).
6. **S10 — Engine routing.** `host_daemon` externally dispatchable; **§17: the local tray
   enrolls + claims via B2 (one protocol, no dual path)** — this is where FIX B's deferred
   migration completes and the JSON claim route is finally retired.
7. **S11 — First B2 live test.** Rendered chatbot proof + ≥1,000-daemon / 10,000-job load
   proof + stale-result rejection + zero platform workers. Final acceptance = exec plan
   §13 (all 20 criteria) and a real browser-rendered chatbot conversation through the
   live connector (`ui-test`), logged to `output/user_sim_session.md`.

Then S12 (source-exec) and S13–S16 (B3 market) — later phase.

---

## 8. Branch / worktree / PR map

| Branch | Head | Meaning | PR |
|---|---|---|---|
| `feat/patch-loop-runner` | `c11b145b` | **Integration branch** — merged substrate S0/S1/S3/S5 | #1472 (draft) |
| `feat/patch-loop-leasestore` | `a666afca` | **S2** — rejected fix-1; finish per §5 | — |
| `feat/patch-loop-leasestore-kimi` | (local A/B) | throwaway A/B experiment lane; ignore/remove | — |
| `feat/kimi-direct-default` | — | `scripts/kimi_review.py` helper + doc | #1474 (draft) |
| `feat/patch-loop-capsule` / `-device-auth` / `-blobresult` / `-s1` | — | per-slice source branches already merged into runner | — |
| `feat/patch-loop-integration` | — | older full-build-out lane (superseded by the sliced substrate) | #1471 (draft) |

Local worktrees of note:
- `C:/Users/Jonathan/Projects/wf-patch-loop-leasestore` — S2 (has interrupted partial
  fix-2, §5.4). `_PURPOSE.md` present.
- `C:/Users/Jonathan/Projects/wf-s2-kimi-ab` — **throwaway** detached checkout at
  a666afca from the A/B experiment; safe to `git worktree remove`.
- Substrate reviewed via `scripts/worktree_status.py` — run it at session start.

---

## 9. Parked (host-gated — NOT part of this build resume)

A major **platform-vision reframe** was captured this session but is **not** authorized
for implementation. Do not build from it without host approval:

- **"Open production commons"** — one generic work-order primitive (Goal/Branch/Asset/
  Claim/Order+Offer/Pool kernel); commons + market + funding on one lineage/reputation
  ledger; the platform never executes or holds custody; the moat is the
  verifiable-provenance + funding layer above commodity compute/fab. Build order:
  coding-PR → open-model → open-hardware.
- Design artifacts (opposite-family-reviewed to closure but NOT PLAN truth):
  `tmp/design-note-democratized-commons.md`, `tmp/plan-foldback-proposal.md`,
  `tmp/platform-shape-synthesis.md`. Memories: `platform-shape-democratized-commons`,
  `enabling-primitives-not-prebuilt-complexity`.
- **Blocked on:** a host Q6 confirmation (private-fulfillment = opaque-reference-only,
  never platform-stored even encrypted) + explicit **PLAN.md foldback approval**
  (PLAN changes require user approval). Until then it is context, not a build queue.

The distributed-execution build (this doc) is the correct-shape substrate that the
commons vision would sit on; finishing S2→S11 is the right next work regardless.

---

## 10. Note for the driver: Kimi-vs-Codex A/B result (both built — read carefully)

The host asked to compare Kimi vs Codex **building the same S2 fix**. Both families
emitted a patch — this is NOT "Codex built, Kimi didn't." The useful differences:

- **Codex arm (`a666afca`, `workspace-write`):** produced a real, committed, *testable*
  tree — but its design (make SQLite the sole authority by fail-louding JSON
  `claim_task`) was **REJECTED** on correctness: the completion bypass moved to
  `atomic_update`, the daemon lost its only working claimer (outage), and a test was
  gamed to hide it (see §5.2).
- **Kimi arm (`kimi-s2-candidate.patch`, this folder — `-p` read-only):** emitted a
  ~48 KB, 8-file patch (with dedicated new tests `test_lease_store_s5_completion.py`
  and `test_branch_tasks_single_claim_authority.py`) built **fresh from the clean
  `35f56034` base** in a single pass. Its design is **different and, on paper,
  sidesteps both of Codex's rejection findings**: it makes `atomic_update` the only
  fenced primitive with `complete_job` as the sole validated completion authority
  (FINDING A), and it **keeps JSON `claim_task` working** but hardens it into a sound
  single-claim authority (fail-closed on blank/shared identity) rather than retiring it
  — so it introduces **no daemon outage and no gamed test** (FINDING B). See §5.5.

**Two honest caveats on the Kimi arm:** (1) it is **UNVERIFIED** — never applied, tested,
or gated, so its lockdown and single-authority claims are unproven; (2) the emitted diff
is **not directly `git apply`-able** (`git apply --check` fails "corrupt at line 19" — the
typical artifact of a model emitting a diff as chat text in read-only mode), so it is a
**design reference to reconstruct from, not a patch to apply blindly.** Kimi was also
**slow** (this emit took ~20+ min; its separate a666afca *review* timed out at 25 min).

**Takeaway for the driver:** Kimi *can* design a strong, differently-shaped fix — its S2
approach to FINDING B (harden `claim_task` instead of retiring it) is worth adopting. But
in `-p` read-only mode it cannot commit or self-test, and its diffs need cleanup. The
efficient pattern with the tools you have: **Kimi designs/reviews → Codex applies+commits
(`workspace-write`) → Codex + Fable gate the same commit.** This is consistent with
`codex-as-builder-on-nonconvergence` (Codex is the default *builder* for hard substrate)
while giving Kimi's design the credit it earned.

---

## 11. Artifact index (evidence, on this machine)

Job tmp dir: `C:/Users/Jonathan/.claude/jobs/aaaa5b09/tmp/` (ephemeral — copy anything
you need to keep).

- **S2 reject verdict (the two findings):** `s2-fix-rereview-codex-verdict.md`
- **S2 fix-1 (a666afca) report:** `s2-fix-report.md`
- **S2 original build verdict + report:** `s2-codex-verdict.md`, `s2-leasestore-report.md`
- **Kimi S2 candidate patch (design reference; unverified; not directly applyable):**
  `kimi-s2-candidate.patch` (in THIS handoff folder); raw emit
  `kimi-s2-patch-out.jsonl` in the job tmp
- **Merged substrate verdicts+reports:** `s0-*`, `s1-capsule-*`, `s3-deviceauth-*`,
  `s5-*` (each with fix rounds + rereview verdicts)
- **Runner architecture judgment (two-layer split, B1/B2/B3, sequence):**
  `runner-arch-judgment-report.md`
- **Design reframe (host-gated):** `design-review-codex-verdict.md`,
  `design-rereview-codex-verdict.md`, `design-final-codex-verdict.md`,
  `design-note-democratized-commons.md`, `plan-foldback-proposal.md`
- **Exec plan (canonical slice program):**
  `git show feat/patch-loop-runner:2026-07-18-distributed-execution-platform.md (archived in git)`
  (also copied to `execplan-runner.md` in the job tmp)
- **Review-dispatch script:** `scripts/peer_agent.py` (the retired `codex_review` and `kimi_review` wrappers were consolidated into it 2026-08-26)

Relevant memories (`C:/Users/Jonathan/.claude/projects/C--Users-Jonathan-Projects-TinyAssets/memory/`):
`dual-family-latest-model-approval`, `codex-as-builder-on-nonconvergence`,
`cross-family-review-patterns`, `never-game-the-gate-with-xfail`,
`ambient-credential-fallback-is-an-identity-leak`, `no-users-build-correct-shape`,
`kimi-code-cli-setup`, `platform-shape-democratized-commons`,
`enabling-primitives-not-prebuilt-complexity`.

---

*End of resume spec. Start at §5 (finish S2), gate per §3, honor §6 throughout.*

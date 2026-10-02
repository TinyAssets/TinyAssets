# Tasks — cloud-only runtime admission

No runtime guard lands until this change clears cross-family review. Task 1 is
the bounded hosted preflight — **PR #3914 merged, observed06:21UTC September22**; it
resolves the facts that could change the design, and task 4 does not start until
it has actually produced one. Task 6's refusal flip is gated on task 5 resolving
CLOUD on the real droplet. Record-only stages (tasks 1, 5) are observation and
are reported as **incomplete** and not risk-free, never as a closed boundary.
No new gate, workflow or proposal is introduced beyond what is listed here.

- [x] 1. Land `.github/workflows/cloud-only-preflight.yml` as designed
  (`ubuntu-latest`, `permissions: contents: read`, default-branch
  `workflow_dispatch` only, never `pull_request`) — **PR #3914 mergedfdb6ff15**.
  Hosted run35694437735 on main completed06:21UTC2026-09-22: container metadata
  reachable, expected droplet resolved, identity_match=true; public Worker paths
  checked3/bound=true. Tunnel connectors and internal DNS unknown because account
  and tunnel IDs are missing; credential custody unknown, SSH trust TOFU-unverified.
  Overall status unknown, boundary_closed=false, enforcement=none. This clears
  only the actual metadata observation prerequisite, not custody or enforcement.
  Ran `scripts/cloud_only_preflight.py` once after it
  merges and record sanitized verdicts here: remote container metadata
  reachability and expected-id match, connector in-set/out-of-set counts,
  internal-origin DNS binding and canonical MCP Worker routing. Do not emit raw
  identities or enumerable id digests, and assert no cloud fact before the run
  exists. A typed `unknown` blocks enforcement acceptance, not unrelated fixes.
  If metadata is unreachable, revise `design.md` § Evidence primitive before
  task 4.
- [x] 2. Re-read each provider URL in `design.md` § Source citations and their
  limits, correct any that moved, and record the read date. Lead read the
  official contracts on 2026-09-22 UTC and corrected metadata/API/OAuth links.
  Public contract reads do not establish deployed facts or approve runtime code.
- [x] 3. **Prevention (review finding 2):** delete the off-cloud ingress
  capability — daemon, tray and plugin-runtime tunnel startup, its call sites,
  the re-exports and the env gate that armed it. **Landed** in PR #3913, sha
  `dfa22598c35aabad7be27aacbff75d300e17b584`; hosted build `35689968798` /
  deploy `35690255704` passed public handles and the protected-SHA gate at
  05:20 UTC 2026-09-22. This closes an accidental-start path only: the
  cloud-side tunnel remains, tunnel-token custody is the separate Layer C
  invariant, application checks cannot establish exclusive custody, and the
  cloud access policy is still unverified.
- [x] 4. **Gated on task 1 having actually run.** Record the expected droplet id
  into dedicated typed expected-instance state in the canonical data volume
  before candidate startup, separate from the release receipt, then implement
  `resolve_platform_runtime_provenance()` — fail-closed, dedicated internal
  metadata client (literal address, no redirects, sub-second timeout, no user
  input, not reachable as a user capability, SSRF link-local classification
  unchanged), recorded-instance match and sanitized startup record. Resolution happens
  outside any database write transaction. Resolver is **injected** in tests,
  never satisfied by an environment variable. Do not build against a predicted
  metadata result. Prepare the expected-id state before candidate startup;
  preserve it across success-receipt replacement and compatible rollback.
  Resolve once per process; cached refusal never silently upgrades, and each
  explicit restart resolves anew. Operation-level authority checks remain live.
  Add the **non-mutating peek** on the existing process observation (never calls
  the resolver, never initializes the cache, refuses an inherited parent result
  after a PID change, explicit `unknown` for unobserved/failed) and the sanitized
  canary-only `platform_runtime_provenance` field on the existing authenticated
  `/mcp/pulse`, through the existing request-identity API with no auth or
  permission widening. A health GET SHALL NOT trigger a fresh resolve. Nothing
  branches on the field; it is not enforcement.
- [x] 5. Land the resolver in record-only mode; confirm on the droplet that it
  resolves CLOUD and the recorded id matches. Read it back from the **main
  serving process** — a startup log line is not evidence that the main process
  cached anything, and the hosted preflight's separate metadata child is a
  different process. Route: `scripts/deployed_sha.py --report-provenance`
  printing allowlisted typed fields from the pulse response it already fetched
  (no second call, no raw dict dump, unknown stays unknown, existing
  `--assert-contains` exit semantics unchanged), with the flag added to the
  existing hosted post-receipt `Verify protected receipt contains target
  revision` step — no new workflow, secret or desktop credential. Do not flip a
  resolver that cannot resolve CLOUD in production. Report this stage as
  observation, not enforcement and not risk-free — it still adds a read and a
  startup log record on a live path. Useful evidence is exactly "the responding process
  holds a cached CLOUD verdict"; it is **not** binary freshness (`git_sha` is the
  mutable receipt), **not** the current container incarnation (`uptime_seconds`
  starts at app construction), **not** all workers (one responding sample), and
  **not** attestation or custody. Prove expected-id presence/match across a real
  redeploy and restart, plus rollback-state compatibility, before enabling
  refusal.
- [x] 6. Flip claim admission onto the **non-optional** predicate
  `_transaction_allows_assigned_consumer` / `_assigned_consumer_refusal_reason`
  (`tinyassets/branch_tasks_v2.py:1225-1233` at042cdce8), not the optional `authority_claim` callback —
  `transaction_check` returns `allowed` unchanged when it is `None`
  (`tinyassets/branch_tasks_v2.py:482-520`). Evidence is resolved before the write transaction opens and only the
  resulting process-owned value is read inside the CAS; no HTTP I/O under the
  write lock. Keep `_consumer_skip_reason` as the diagnostic.
- [x] 7. Flip runtime registration to resolved provenance, checked against cached process admission on exact-worker eligibility reads so
  an existing row grants nothing, plus the origin-ingress backstop refusal. Do
  **not** derive anti-replay from `boot_id` (`uuid.uuid4().hex` in
  `tinyassets/runtime/assigned_queue_consumer.py`): it is incarnation/liveness only. Keep the
  existing descriptor expiry and add no storage schema for it.
- [ ] 8. Flip startup and the last provider-authority boundary — boot assertion,
  no degraded mode, the four `executor_class="cloud"` literals
  (`tinyassets/foreground_run_provider.py:515,631`,
  `tinyassets/background_served_provider.py:1383,1598` at042cdce8), which the queue path cannot reach —
  and the recovery paths, keeping the two acts distinct: *automatic* recovery
  (watchdog, assigned-consumer startup/poll) leaves work pending when no
  admitted successor exists, while *explicit operator retirement*
  (`tinyassets.runtime_reconcile stale-fleet --apply`) is admitted maintenance
  that cancels exactly the approved stale tasks and assigns nothing.
  `release-reconcile.yml` compares current main against the last successful
  `Deploy prod` run on main -- not directly against the running image -- and is
  not a reassignment path; `deploy/daemon-watchdog.sh` restarts the same cloud
  service/container. Repeated admission refusal after restart is intended
  fail-closed behaviour, never permission for a local fallback. Per-universe
  user-bound authority stays exactly as it is.
- [ ] 9. Write the test matrix from `design.md`. Negatives 1–7 **must be run
  against the unfixed tree and fail there**; the claim negative passes **no**
  `authority_claim` callback, or it is vacuous. Positives 8–9 assert preserved
  behaviour (restart, concurrent cloud workers, per-universe authority) and may
  pass at baseline. Add focused unit tests for the preflight
  with no real API calls. Run `python scripts/linux_oracle.py` on the new tests
  (they touch process/network syscalls Windows skips).
- [x] 10. Get the cross-family review verdict, merge, then prove deployment:
  `python scripts/deployed_sha.py --assert-contains <sha>` and
  `python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp
  --assert-handles` (export `TINYASSETS_WIKI_CANARY_TOKEN` first).
- [ ] 11. Rendered acceptance through the live connector (`ui-test`): a
  brand-new user completes their **own** OpenRouter OAuth PKCE authorization,
  returns via the automatic callback, approves an eligible free model, and gets
  a first actual tool-capable response — no borrowed subscription, no
  account-specific patch, no change to any existing user's workflow.
- [ ] 12. Sync this delta into `openspec/specs/cloud-only-runtime-admission/`,
  resolve the colliding as-built text named in `design.md` § Colliding as-built
  specs (`desktop-host-runtime:8,24,45,59` local MCP/tunnel serving — task 3
  removes the behaviour, so the spec text follows it;
  `daemon-identity-and-host-pool:76,97` host-pool registration — verify live
  before deleting), and archive the change in the same lane.


## September 23 deployed-slice evidence

[Release and rendered acceptance](../../../docs/reviews/2026-09-23-cloud-admission-deployed-acceptance.md)
records PR3919/042cdce8, exact-head cross-family approval, hosted Linux and
Docker results, deployment35805988252 and the original rendered retest11.

Tasks4/5: PR3917 first proved record-only cached CLOUD; subsequent real redeploy
prepared matching expected state before enforced startup. Rollback-state
compatibility is structurally tested, not an actual production rollback drill.
Tasks6/7: mandatory assigned claim, worker-slot provisioning and cached
exact-worker eligibility plus origin HTTP/websocket refusal are deployed.
Task10: protected SHA/public handles passed01:23UTC; ordinary owner workflows
completed01:29UTC. That is not the new-user acceptance required by task11.

Task8 remains partial pending this retirement slice's verified deployment and
the remaining evidence reconciliation. Startup/provider, descriptor publication,
legacy cloud claims and assigned-consumer startup/poll recovery are guarded by
PR3919/3921; those already-landed findings are not still missing code.

**2026-09-23 retirement slice (task8, still partial).** The explicit stale-fleet
retirement write boundary (`tinyassets/runtime_reconcile.py::_apply_plan`) now
requires process admission; `main` reports the refusal through its existing
sanitized JSON/exit-2 channel. Red-first and green evidence, with the exact
commands and counts, is in
[docs/reviews/2026-09-23-cloud-retirement-admission.md](../../../docs/reviews/2026-09-23-cloud-retirement-admission.md).
This closed `docs/concerns/2026-09-23-runtime-reconcile-retirement-ungated.md`
(deleted in the same change) and synced one scoped maintenance requirement into
the main spec. **Evidence class:** executable runtime proof for the retirement
CLI only. The watchdog and `release-reconcile.yml` statements in this change are
*source-wiring evidence* about scripts whose behaviour is unchanged here — this
slice adds no watchdog admission integration and claims none. No deployment, no
hosted Linux run are claimed for it; the candidate evidence records independent
review separately. Task9 remains open: earlier released slices passed hosted
Linux with no new regressions, but the complete planned negative
matrix/baseline mapping is not closed and local Linux oracle was unavailable;
no Docker Desktop/WSL startup is authorized. Skips are not coverage.
Task11 remains open; no free-account action occurred. Task12 is partially synced:
the main admission spec syncs this candidate's maintenance contract for landing;
other admission contracts are already deployed per
[PR3921's public receipt](https://github.com/Jonnyton/TinyAssets/pull/3921#issuecomment-5788453081),
pulse already matched,
and desktop/daemon collision text now separates process launch/discovery from
admission. REST primitives were preserved, not falsely reported removed. Full
delta sync/archive waits for custody, recovery coverage and free-user acceptance.

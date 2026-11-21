---
severity: P1
title: Scheduled uptime coverage remains incomplete
filed: '2026-09-08'
summary: deployed classifier honestly reports unavailable execution-quality and rendered observations; natural post-merge classification verified; cadence and independent coverage remain open
---

# Scheduled uptime acceptance lacks execution-quality and rendered coverage

**Classification update, 2026-09-19 06:20 UTC:** PR #3880 is merged and
automatic deploy-completion run 35426174606 reports explicit unknown for
missing legacy evidence and rendered capability, without incident mutation.
This repairs classification, not the missing observations. Superseded by the
September 19, 09:42 UTC natural scheduled proof and completed spec sync:
[natural classification receipt](../reviews/2026-09-19-natural-scheduled-classification-proof.md).
The bounded classification change is archived; coverage and cadence remain open. See
[`deployed proof`](../reviews/2026-09-19-monitor-classification-deployed-proof.md).
The following pre-fix diagnosis explains the remaining contract mismatch.

**Filed:** 2026-09-08. **Reverified:** 2026-09-19 01:03 UTC scheduled run
35411401518, source `bcac8d1a250350ff48da2cfda3bd428831d361ea`, read with
`gh run view 35411401518 --log`. No runtime restart or private-state edit.

The current scheduled probe reports handshake=0, tool=0, wiki=0, activity=0 and revert=5.
It is not evidence that every public surface is down, and those three passes do
not establish worker or background-run liveness.

- Current coordinator: alive, heartbeat age 1.1 seconds, no active work. PR
  #3869 repaired observation of the authoritative executor lifecycle; the old
  September 8 heartbeat diagnostic is superseded. This does not prove useful
  execution or authorize restarting any retired fleet.
- Revert-loop diagnostic: get_status has no `evidence` block. Returned keys
  include daemon, release_state, identity_evidence and schema_version. Reverify
  the current response contract and source of revert evidence; do not suppress
  the check or fabricate empty evidence to turn it green.
- Layer-2 browser probe exited 13 (`RED_browser_load_error`). The job installs
  Python but no browser/persona, while its subprocess harness needs Playwright
  and a signed-in visible Claude browser on local CDP. The concrete subprocess
  error is absent from Actions stdout/artifacts. Missing acceptance capability
  is not evidence of a provider outage.

The revert probe requests unscoped `get_status` then requires a private
`evidence.activity_log_tail` and fantasy-scene REVERT markers. The canary's
no-home response intentionally omits that tenant evidence. Current engine
execution does not publish those legacy markers as a platform-health contract.
Never grant private-universe access or synthesize empty evidence to pass it.

Dependencies: (1) classify unavailable monitoring separately from observed
outage and success in the existing scheduled result/alarm path; (2) define an
authoritative private-free current-engine execution-quality observation before
claiming sustained useful-work coverage; (3) obtain an authorized rendered
client acceptance capability, without converting infrastructure into an LLM
actor. None is satisfied by coordinator liveness alone. Intentional user
workflow failures and an individual provider's exhaustion are not platform
outage signals.

Detailed source inventory and reproduction:
[`2026-09-19-hostless-monitor-contract-diagnosis.md`](../reviews/2026-09-19-hostless-monitor-contract-diagnosis.md).
The bounded monitoring-classification change is archived at
[the `classify-scheduled-monitor-observations` change](https://github.com/TinyAssets/TinyAssets/tree/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/archive/2026-09-19-classify-scheduled-monitor-observations); it does not close
the execution-quality or rendered-acceptance gaps.

The deployed truthful unknown classification also makes automatic recovery
unreachable while required legacy evidence remains unavailable. Existing
false-era incidents need fresh, per-incident verification and an explicit
coordinator/incident-owner disposition; do not bulk-close or claim recovery
from the four green sub-probes. No issue mutation has been performed by this
lane. The missing capability remains actionable even if the classifier job
itself exits successfully.

# Review state

2026-09-09 UTC, Windows worktree `codex/retire-engine-mutation-subcap`.
Independent shape review dispatched through:
`python scripts/peer_agent.py claude --out output/engine-subcap-shape-review.md --prompt-file output/engine-subcap-shape-brief.md --timeout 480`
Terminal exit 1 after four seconds; no review produced. A minimal authenticated
CLI diagnostic using the same sanitized subscription environment returned:
`Failed to authenticate: OAuth session expired and could not be refreshed`.
This is not a review approval or a subscription usage-limit fallback. User
sign-in has been requested. No runtime enforcement change or deployment yet.

Baseline at runtime tree `df8d53cf`:
`python -m pytest -q tests/test_engine_admissions.py tests/test_engine_mcp_server.py tests/test_engine_mcp_hardening.py tests/test_resource_usage_status.py --junitxml=output/engine-subcap-windows-base.xml`
reported **144 passed, 7 skipped** on Windows. The new policy regression file
is prepared separately to demonstrate the old engine-only refusal before the
implementation; it is not baseline-green evidence or Linux proof.

Red-phase evidence:
`python -m pytest -q tests/test_engine_admission_resource_policy.py --junitxml=output/engine-subcap-red.xml`
reported **7 failed, 6 passed** on the unchanged runtime. Each failure is the
existing engine-category refusal (including when total should be named), rather
than a new implementation regression. Tests cover current 900/300/3600 values,
601st and 900th edits, mixed categories and cross-universe independence, a
12-way final-slot race, expiry/binding and the real engine wrapper's closed-ledger
path. Runtime remains untouched while shape review is unavailable.

Open: independent shape/basic-safety review, implementation, focused candidate
and Linux proof, exact-head approval, guarded merge and authenticated deployment.

Sign-in restored by the owner on September 9 UTC: `claude auth status` now
reports `loggedIn=true`, subscription auth `claude.ai`. The shape review was
restarted with output `output/engine-subcap-shape-review-restored.md`; the prior
failed file is not a review. The completed host sign-in action was removed from
`docs/host-actions.md` rather than left as a current blocker.

## Shape verdict: APPROVE

Claude completed the restored review with exit 0 after 228 seconds. Substantive
review in session `aab279ed-587f-4ccc-b54f-32b3a2728a2c`, final wrapper output
`output/engine-subcap-shape-review-restored.md`. It independently confirmed no
caller uses `engine_max`, both helper consumers are in scope, engine rows cannot
bind/refund, and the partition reserves one owner's allowance for that same owner.
It explicitly does not approve declaring wider consolidation complete.

Required implementation proof incorporated: scheduled work refuses at a total
filled by edits without pausing and resumes after expiry; all refusal-string
assertions updated together; write reason is explicit and unknown reasons report
unavailability instead of inventing a quota; observed engine count remains after
its limit key removal; touched stale numeric comments corrected. Authenticated
canary, exact-head approval, Linux test/skip evidence and live SHA still gate ship.
The reviewer noted increased possible branch-version growth within the same 900
total; retained-space enforcement remains part of the unfinished broader goal.

## Candidate evidence before exact-head review

Windows, September 9 UTC:
`python -m pytest -q tests/test_engine_admission_resource_policy.py tests/test_engine_admissions.py tests/test_engine_mcp_server.py tests/test_engine_mcp_hardening.py tests/test_resource_usage_status.py tests/test_automations.py --junitxml=output/engine-subcap-windows-head.xml`
reported **231 passed, 7 skipped**; the skips are the same existing platform
restrictions in the original four-file baseline. The scheduler file adds 74
passes, including the new full-engine-total/expiry recovery test. Ruff over all
changed canonical modules/tests passes. Plugin mirror rebuild/import probe and
strict OpenSpec validation pass. The first candidate run found four stale tests
whose bare boolean refusal mocked a ledger failure as a quota; these now provide
the actual typed ledger/total reason, preserving assertions that mutation never
occurs after refusal. No test was skipped or weakened to accept an admission.

Reusable Linux baseline: PR #3568 `required-tests` run 34306784859, actual checkout
2c321ed57e1225b4d42f2468c0023295b7c7a268 has the complete tree of bf39c447.
`git diff --exit-code bf39c447 df8d53cf --` over all five affected baseline test
files and canonical modules is empty. Its JUnit contains **224 focused passes,
zero skips** (73 automations, 26 admissions, 82 engine surface, 11 hardening,
32 resource status). Compare candidate exact tree/test identities against these;
only the deliberately replaced engine-share-reservation scenario may disappear.
The new candidate must add 13 policy cases and the scheduler recovery case.

## Exact-head and Linux verdicts

Claude completed its independent code review in 228 seconds with exit 0 and
APPROVE for d22c11054d08928246448e4b4a6d9e5720679a47 against c9fe06fb.
Full review: `output/engine-subcap-code-review.md`; durable PR receipt:
https://github.com/Jonnyton/TinyAssets/pull/3577#issuecomment-5595878957.
No blocking evidence findings. The reviewer independently ran 13 new tests,
Ruff, strict spec validation and four-file mirror parity.

Linux `required-tests` and `slow-tests` run 34311535677 succeeded September 9 UTC.
Actual checkout 55920e0d8874e4800edc11d8a76848cf5f7c879a is full-tree identical
to approved d22c1105 (`git diff --exit-code`). JUnit comparison using
`python output/compare_engine_subcap_junit.py output/storage-observation-linux-bf39c447/junit.xml output/engine-subcap-linux-d22c1105/junit.xml`
proves 238 focused passes, no skips or regressions: 13 added policy cases and
one scheduler recovery case; only the deliberately replaced engine-share
reservation scenario disappears. Linux receipt:
https://github.com/Jonnyton/TinyAssets/pull/3577#issuecomment-5596013939.

Normal guarded merge #3577 landed 8f1b47609330f06924246a692740448836672a48
at 2026-09-09T04:53:16Z. Image build 34312713141 started for that revision.
Deployment 34312901542 subsequently succeeded at 04:57:38Z, with authenticated
canary and protected containment proof for 8f1b47609330. Full dated commands,
scope and rollback: `docs/reviews/2026-09-08-engine-edit-subcap-proof.md`.
Owner-rendered acceptance remains a separate open gate.

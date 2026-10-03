# Executable gates

Not in this table = judgement, not a gate.

| Gate | Script | Runs |
|---|---|---|
| Rulebook byte ratchet | `scripts/check_context_budget.py` + `tests/test_rulebook_ratchet.py` | CI |
| Rule-file drift | `scripts/check_cross_provider_drift.py` | hook, CI |
| Skills valid + mirrored | `scripts/validate_skills.py`, `check_mirror_parity.py` | hook, CI |
| No mojibake | `scripts/invariants/mojibake.py` | hook, CI |
| Behavioural tests | `scripts/ci_required_tests.py`, `known-failing-tests.txt` (one-way) | required |
| Diff scope | `.github/workflows/pr-scope-guard.yml` | required |
| Review receipt (head or diff key), every PR | `scripts/drain_review_gate.py` | required |
| Public MCP handles | `scripts/mcp_public_canary.py --assert-handles` | deploy, DNS/tunnel edits |
| Merged is not deployed | `scripts/deployed_sha.py --assert-contains <sha>`: 0 shipped, 1 not, **2 cannot tell** (never collapse 2 into 0) | deploy, never required |

Receipt (PR body, so a head change voids it):

```
Drain-Review-Verdict: APPROVE
Drain-Review-Head: <40-char sha>
Drain-Review-Artifact: docs/... | https://github.com/...
```

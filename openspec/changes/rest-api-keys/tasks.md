## 1. Implementation

- [x] 1.1 Write proposal, design and authority/storage/public API delta before code.
- [x] 1.2 Implement hashed keys, protected lifecycle, scope generations, rate limits and existing outside-authority integration.
- [x] 1.3 Implement REST adapters, OpenAPI, protected owner UI and canonical Worker routing.
- [x] 1.4 Prove lifecycle, endpoint/level scope, owner isolation, approval refusal, parity and rate limits.

## 2. Delivery

- [ ] 2.1 Run affected Linux suites, ruff, structural guards, plugin build, hygiene, Worker tests and public canary.
- [x] 2.2 Commit explicit paths with Codex trailer, push and open a non-draft infra-change PR with at most eight release-critical files.
- [x] 2.3 Perform one cross-family floor/correctness review and resolve findings with AGREE/DISAGREE_EVIDENCE.
- [x] 2.4 Sync implemented spec; record pending deployed-SHA and naive-user live acceptance without claiming deployment.

Verification so far: Linux oracle 350 affected tests, then 70 key/tool/store tests
after the thin-loop authority guard; Worker 93 tests; ruff and plugin build pass.
After review fixes, Linux oracle 81 key/outside-authority/tool tests pass.
Structural guards: 587 passed. Hygiene and protected rendered owner UI lifecycle pass.
PR: https://github.com/TinyAssets/TinyAssets/pull/4589 (three release-critical files).
Claude cross-family review: APPROVE. AGREE with its credential, owner/session,
scope, saved-run, box-tool, approval, rate/revoke, MCP and Worker findings.
Accepted all three minor DISAGREE_EVIDENCE findings: operator outside deny now
also stops keys, account deletion between authentication/list returns 401, and
an unbound effect raises a permission refusal. Added focused regression tests.
No Drain-Review receipt is produced, per founder instruction; the repository's
receipt gate may continue to block merging independently of this implementation.
Public canary is blocked: no TINYASSETS_WIKI_CANARY_TOKEN in this session and
scripts/load_secrets.sh reports the 1Password CLI is not installed. No deployed
SHA or live naive-user acceptance is claimed; this delivery is a reviewable PR.
The latest successful production deploy's Public MCP canary (--assert-handles)
is green at https://github.com/TinyAssets/TinyAssets/actions/runs/38026603892
(2026-10-10 05:10 UTC); that is baseline evidence, not this PR's deployment proof.

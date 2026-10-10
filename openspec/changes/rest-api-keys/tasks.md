## 1. Implementation

- [x] 1.1 Write proposal, design and authority/storage/public API delta before code.
- [x] 1.2 Implement hashed keys, protected lifecycle, scope generations, rate limits and existing outside-authority integration.
- [x] 1.3 Implement REST adapters, OpenAPI, protected owner UI and canonical Worker routing.
- [x] 1.4 Prove lifecycle, endpoint/level scope, owner isolation, approval refusal, parity and rate limits.

## 2. Delivery

- [ ] 2.1 Run affected Linux suites, ruff, structural guards, plugin build, hygiene, Worker tests and public canary.
- [ ] 2.2 Commit explicit paths with Codex trailer, push and open a non-draft infra-change PR with at most eight release-critical files.
- [ ] 2.3 Perform one cross-family floor/correctness review and resolve findings with AGREE/DISAGREE_EVIDENCE.
- [x] 2.4 Sync implemented spec; record pending deployed-SHA and naive-user live acceptance without claiming deployment.

Verification so far: Linux oracle 350 affected tests, then 70 key/tool/store tests
after the thin-loop authority guard; Worker 93 tests; ruff and plugin build pass.
Public canary is blocked: no TINYASSETS_WIKI_CANARY_TOKEN in this session and
scripts/load_secrets.sh reports the 1Password CLI is not installed. No deployed
SHA or live naive-user acceptance is claimed; this delivery is a reviewable PR.

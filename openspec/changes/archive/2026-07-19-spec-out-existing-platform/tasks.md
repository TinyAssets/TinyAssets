# Tasks — Spec Out Existing Platform

## 1. Draft as-built delta specs (one per capability, code-verified)

- [x] 1.1 `live-mcp-connector-surface`
- [x] 1.2 `graph-execution-substrate`
- [x] 1.3 `universe-lifecycle-and-soul`
- [x] 1.4 `universe-personification-and-relay`
- [x] 1.5 `identity-auth-and-access-control`
- [x] 1.6 `credential-vault`
- [x] 1.7 `provider-routing`
- [x] 1.8 `daemon-runtime-and-dispatch`
- [x] 1.9 `wiki-commons`
- [x] 1.10 `knowledge-retrieval-and-memory`
- [x] 1.11 `shared-goals-and-convergence`
- [x] 1.12 `community-patch-loop`
- [x] 1.13 `evaluation-outcomes-and-attribution`
- [x] 1.14 `paid-market-economy`

## 2. Consistency corrections riding the baseline

- [x] 2.1 Correct stale "five canonical handles" wording in AGENTS.md Hard
      Rule #12 to the as-built handle set, deferring to the
      `live-mcp-connector-surface` spec (verify canary code first)
- [x] 2.2 Fix the canary `converse` drift (docstring, `--assert-handles`
      help, success suffix, test fixture) — review finding 5; suite green

## 3. Verify and sync

- [x] 3.1 `openspec validate` (or format lint) passes for all 14 delta specs
- [x] 3.2 Cross-family Codex accuracy review of the full spec set vs code;
      fix findings (verdict `adapt`, 5 findings, all addressed —
      `2026-07-19-spec-baseline-codex-review.md (archived in git)`)
- [x] 3.3 Sync delta specs into `openspec/specs/<capability>/spec.md`
      (create; forward-vision specs untouched)
- [x] 3.4 Draft PR opened with the convention (AGENTS.md), config, STATUS
      claim, and baseline specs (merged as PR #1476,
      `e2a30f216dd841b76797ef53b7778df7d21ca5c6`)

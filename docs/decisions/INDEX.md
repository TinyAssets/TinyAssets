# Decisions Index

Accepted and proposed first; superseded last. An ADR is never edited after
acceptance except its Status line (ADR-005).

- [ADR-005-retire-plan-md.md](ADR-005-retire-plan-md.md) — PLAN.md retired · one home per kind of fact · approval gate is README § Direction plus ADR acceptance
- [ADR-006-the-platform-has-no-llm.md](ADR-006-the-platform-has-no-llm.md) — only a powered command center calls an LLM, on its owner's credentials
- [ADR-007-vendor-neutral-connections.md](ADR-007-vendor-neutral-connections.md) — any model or platform through generic connection primitives · OAuth first · no vendor code
- [ADR-008-cloud-only-platform.md](ADR-008-cloud-only-platform.md) — the platform runs only in the cloud · the founder's desktop is never infrastructure
- [ADR-009-nothing-runs-outside-a-users-command-center.md](ADR-009-nothing-runs-outside-a-users-command-center.md) — every execution belongs to one owner, who can see, pause and delete it · no platform fleet
- [ADR-010-private-by-default.md](ADR-010-private-by-default.md) — every creation path writes `private` · exposure is an explicit owner act
- [ADR-011-owner-door-complete-model-door-bounded.md](ADR-011-owner-door-complete-model-door-bounded.md) — the app's reads are complete · only the model door bounds · `AccountType` is the one per-account input
- [ADR-012-sealed-box-target-architecture.md](ADR-012-sealed-box-target-architecture.md) — one sealed box per command center · control plane is the only always-on layer · capacity later
- [ADR-013-primitives-not-operators.md](ADR-013-primitives-not-operators.md) — ship the missing primitive, not an operator · irreducibility finding for new handles · no graph-size caps
- [ADR-014-an-agent-is-a-node.md](ADR-014-an-agent-is-a-node.md) — an agent node runs the `converse` turn under the owner's grant
- [ADR-015-only-the-floor-blocks-a-deploy.md](ADR-015-only-the-floor-blocks-a-deploy.md) — the six floor classes · everything else is tracked and re-judged after live use
- [ADR-016-identity-follows-the-account.md](ADR-016-identity-follows-the-account.md) — WorkOS AuthKit · one principal across every client · no anonymous principal
- [ADR-017-knowledge-is-files-okf-by-default.md](ADR-017-knowledge-is-files-okf-by-default.md) — OKF bundle is the default canonical knowledge form · indexes are derived
- [ADR-018-command-centers-are-exportable.md](ADR-018-command-centers-are-exportable.md) — one action exports a runnable, publish-ready folder · not yet built
- [ADR-004-merge-attribution-and-the-deploy-gap.md](ADR-004-merge-attribution-and-the-deploy-gap.md) — merged-is-not-deployed and PRs stuck BEHIND are ONE root cause: the default `GITHUB_TOKEN` cannot originate the events this repo's automation needs · measured #2259 vs #2260 · host mints a PAT/App token
- [ADR-003-required-test-aggregator.md](ADR-003-required-test-aggregator.md) — `required-tests`: the first required check that tests behaviour · runs unconditionally · no-new-failures ratchet · host `gh api` command to make it required
- [2026-07-13-paid-market-founder-signoffs.md](2026-07-13-paid-market-founder-signoffs.md) — founder sign-offs D1 write gate · D2 ledger coexistence · D3 droplet concurrency · D4 serialization rule + differential testing
- [ADR-001-truth-hierarchy-and-freshness.md](ADR-001-truth-hierarchy-and-freshness.md) — superseded by ADR-005
- [ADR-002-static-vs-dynamic-context-budget.md](ADR-002-static-vs-dynamic-context-budget.md) — superseded by ADR-005

## 1. Contract promotion

- [x] 1.1 Reconcile the April Track J pre-draft, PLAN §14, current capability-owned §14 tasks, and the user-growth concurrency audit against current main.
- [x] 1.2 Define the `production-load-evidence` ownership boundary, registry, immutable manifest, verdict algebra, substrate classification, recomputable evidence, oracle, fault, reconciliation, and baseline requirements.
- [x] 1.3 Obtain Opus 5 review of the drafted proposal, design, delta spec, tasks, and supersession text; resolve every Critical and Important finding.
- [x] 1.4 After review approval, mark `2026-04-18-load-test-harness-plan.md (archived in git)` superseded as implementation authority while preserving it as historical scenario research.
- [x] 1.5 After review approval, retire the reviewed §14 harness entry from `ideas/INBOX.md` with this change as durable build authority.
- [x] 1.6 Run strict OpenSpec validation and land this target change without syncing it into as-built specs.

## 2. Dependent protocol implementation

- [ ] 2.1 After the active `tests/load/` claim releases, create and claim the dependent `implement-production-load-harness` change for `tests/load/_protocol/` and `tests/load/README.md`.
- [ ] 2.2 Implement the versioned registry loader, canonical manifest writer and validator, content-digest verification, blocking-substrate registry, environment fingerprint collector, and failure-first rollup evaluator.
- [ ] 2.3 Implement conformance tests that reject unknown verdicts; mutable or digest-invalid manifests; shaped- or mock-labelled `passed`; `not_run` without machine-readable blocking-substrate codes; empty required/applicable selections reported `passed`; unversioned or unjustified applicability/required-status downgrades; incomplete required environment fingerprints; missing denominators; unevaluable invariants reported as held; empty required fault timelines; reconciliation discrepancies without `expected|loss|duplicate|unexplained` classification; missing required baselines; live-run manifests without authorization, isolation, cleanup, blast-radius, abort, or canary fields; raw user content or non-pseudonymous identifiers; and aggregates that omit constituent substrate classes or manifest digests.
- [ ] 2.4 Implement versioned invariant-oracle, fault-timeline, reconciliation-report, and raw-artifact interfaces without moving capability-owned predicates, thresholds, or adapters into the shared layer.
- [ ] 2.5 Prove the protocol conformance suite can pass while unavailable production scenarios remain independently `not_run`.

## 3. Connector-first baseline and owner adoption

- [ ] 3.1 After `test-identity-and-reset` and explicit host authorization of an isolated environment and traffic envelope, register and run a provider-free current-system baseline packet for concurrent canonical connector/MCP sessions and the current single-origin storage path; record blast radius, abort criteria, canary coordination, cleanup, exact environment, commands, raw evidence, and independently recomputed results.
- [ ] 3.2 File separately claimed adoption work for `operator-request-trigger-contract` and `distributed-execution` so their owners can reference the shared schema version without changing populations, thresholds, owner-local baseline semantics, adapters, choreography, or release gates; enumerate each additional adopting capability in its own owner-approved lane.
- [ ] 3.3 Emit machine-readable `not_run` evidence for PostgreSQL/RLS/pooling/CAS/outbox, configured Realtime, 500-daemon fleet, settlement replay, failure injection, and any connector path that would invoke a model/provider or maintainer quota until each required real substrate or provider-free adapter exists.
- [ ] 3.4 Add real-substrate or real-equivalent scenario adapters only in their owning capability lanes and prove that shaped/optional/inapplicable registry entries cannot create a false required green rollup.

## 4. Completion gates

- [ ] 4.1 Run focused protocol tests, the full applicable test suite, Ruff, and strict validation with dated environment and command evidence.
- [ ] 4.2 Obtain independent correctness, security/privacy, concurrency, evidence-integrity, and diff-simplicity review; resolve every Critical and Important finding.
- [ ] 4.3 For any public connector behavior changed by implementation, complete canonical `/mcp` canaries, rendered chatbot `ui-test`, and post-fix clean-use evidence.
- [ ] 4.4 Sync `production-load-evidence` into `openspec/specs/` and archive this change only after the protocol implementation, conformance suite, one real baseline adapter, and all applicable acceptance gates pass.

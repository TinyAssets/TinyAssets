## Context

The Forever Rule makes §14 concurrency/load evidence part of the definition of done for uptime-track capabilities. Today, capability changes carry distinct workload counts, thresholds, fault mixes, and invariants, but they repeatedly restate how evidence must be recorded. The only umbrella artifact, `2026-04-18-load-test-harness-plan.md (archived in git)`, predates the current architecture and assumes one monolithic S1–S8 implementation.

This change introduces a target-only `production-load-evidence` capability. It follows the layered registry/adapter pattern already used by `evaluation-runtime-and-scenarios`, but remains separate because it evaluates system capacity and failure behavior rather than daemon output quality.

The first-class product path is a user talking to a chatbot through the TinyAssets connector. That decision affects scenario roadmap ordering, not the generic evidence schema. Agent Village is deferred and does not shape this capability.

## Goals / Non-Goals

**Goals:**

- Make every §14 claim independently inspectable and recomputable.
- Preserve capability ownership of workload semantics while standardizing evidence.
- Distinguish an absent substrate from a passed or failed run.
- Permit protocol conformance to become green before every declared real substrate or real-equivalent isolated environment exists.
- Establish a truthful baseline for the current single-origin system before later distributed migrations.

**Non-Goals:**

- Owning capability-specific populations, thresholds, adapters, fault choices, or invariant predicates.
- Selecting k6, Locust, Supabase, or another permanent implementation stack.
- Implementing files under `tests/load/` while that path is claimed by another lane.
- Treating mock results as production capacity proof.
- Running unapproved load against the live public connector, invoking a model/provider during a load run, consuming maintainer quota, or using non-isolated user identities.
- Defining a web-app product or using Agent Village traffic to drive platform architecture.
- Syncing this target capability into as-built specs before implementation and real adapter evidence exist.

## Decisions

### 1. Shared contract owns how evidence is recorded

`production-load-evidence` owns the verdict vocabulary and rollup algebra, registry and manifest schemas, environment fingerprints, artifact retention, oracle result interface, fault timeline, reconciliation format, and baseline-comparison rules. Each capability owner retains its scenario definition, workload mix, thresholds, required/optional classification, adapter, injected faults, and invariant predicates.

Capability specs reference a shared schema version instead of copying its field list. This prevents evidence drift without centralizing domain judgment.

The active `operator-request-trigger-contract` currently owns its admission/claim populations, thresholds, adapter, and same-environment readers-only comparison. On adoption, only its duplicated evidence-field enumeration is replaced by a reference to this schema; its baseline rule and statement that baseline comparisons are not absolute §14 targets remain owner-local. The active `distributed-execution` change retains its S11 workload, thresholds, choreography, and adapter; its evidence manifest must conform to this schema rather than define a competing envelope.

Alternative: make one umbrella harness own every scenario. Rejected because it duplicates active capability work and turns cross-platform evidence infrastructure into a bottleneck.

### 2. Registry entries and run manifests are independently versioned

A registry entry pins `scenario_id`, `scenario_version`, owning capability, applicability, required/optional status, the justification for that classification, substrate requirements, adapter reference, oracle references, fault declaration, and threshold references. A run pins both the registry entry and `schema_version`.

Breaking schema or scenario changes create new versions. Changing applicability or required/optional status also creates a new scenario version with a justification. Historical runs remain interpretable under the versions they recorded.

Alternative: a mutable latest-only schema. Rejected because old evidence would silently acquire new meaning.

### 3. Run manifests are immutable and content-addressed

A completed manifest is written once, addressed by a digest over canonical content, and names the digests of all raw artifacts. Corrections create a new run with `supersedes: <run_id>`; they never amend the original.

The manifest records at least: run and schema identifiers; scenario and capability; verdict and reason; blocking substrates when applicable; timestamps; exact commands; deterministic seed when applicable; operator; environment fingerprint; artifact digests; oracle outcomes; and supersession.

Alternative: edit a run after investigation. Rejected because reviewers could no longer reproduce the evidence available to the original gate.

### 4. Verdicts are lowercase and roll up failure-first

The only terminal scenario verdicts are `passed`, `failed`, and `not_run`.

For required and applicable scenarios, rollup is:

1. any `failed` yields `failed`;
2. otherwise any `not_run` yields `not_run`;
3. otherwise all are `passed`.

An empty required-and-applicable set yields `not_run`, never a vacuous `passed`. Optional or inapplicable registry entries do not poison a rollup. Protocol-conformance verdicts are reported separately from scenario verdicts. Aggregate output retains every constituent scenario ID, verdict, substrate class, and manifest digest so a mock-sourced failure remains visible.

A `failed` or `not_run` required rollup cannot satisfy the Forever Rule §14 done-claim. Capability owners retain their activation mechanics, but they cannot describe the obligation as met.

Alternative: let any `not_run` dominate. Rejected because a known failure could be hidden behind an unavailable scenario and optional future entries could block green forever.

### 5. Substrate honesty is explicit

Every run identifies its substrate class as `real`, `shaped`, or `mock` and records machine-readable blocking-substrate codes. `real` means the deployed service or an isolated environment satisfying the scenario owner's predeclared equivalence profile for relevant released artifacts, engines/versions, service tier, topology, configuration, capacity envelope, and limits. `shaped` means at least one relevant equivalence condition is scaled, omitted, or substituted. `mock` replaces a production implementation with a simulator, stub, or in-memory stand-in. A production-evidence scenario executed on a shaped or mock substrate cannot emit `passed`. It can emit `failed` when it exposes a real invariant violation, or `not_run` with a substrate reason. Generator and validator unit tests are conformance evidence, not production-load results.

The initial blocking-substrate registry includes real PostgreSQL/RLS/pooling/CAS/outbox, configured Realtime capacity, fleet fixtures, settlement primitives, and fault-injection control. Codes are versioned rather than free-form-only.

Alternative: allow shaped or mock runs to pass with a caveat. Rejected because downstream rollups routinely erase prose caveats.

### 6. Raw evidence must permit independent recomputation

Reported p50/p95/p99/max values include operation names, populations, denominators, units, and the raw event stream or content-addressed source from which counts and percentiles can be recomputed. Environment fingerprints include source SHA, image digest, configuration and rollout identity, topology, region, database engine/version/pool settings, queue or Realtime tier and connection envelope, participant resources, network facts, and clock-sync evidence.

Missing denominators, missing raw artifacts, digest mismatches, or an incomplete required fingerprint make the scenario `not_run` unless the available evidence proves an invariant violation, in which case it is `failed`. Raw metrics, traces, and diagnostics exclude user-authored content and pseudonymize actor, account, universe, and node identifiers. Reproduction uses deterministic synthetic inputs, never captured user payloads.

Alternative: retain dashboards or percentile summaries only. Rejected because screenshots and aggregates cannot establish population completeness or detect denominator manipulation.

### 7. Oracles, faults, and reconciliation are first-class evidence

Capability owners register named, versioned invariant predicates. Each evaluation returns `held`, `violated`, or `unevaluable`; `unevaluable` never counts as held, and any violated required invariant fails the scenario.

Fault-declaring scenarios record an ordered injection/observation/recovery timeline. An empty or unverifiable required fault timeline yields `not_run`. Reconciliation reports classify admitted, committed, claimed, delivered, and settled discrepancies as `expected`, `loss`, `duplicate`, or `unexplained`; any `loss`, `duplicate`, or `unexplained` discrepancy that violates the owner’s declared invariant fails the scenario.

Alternative: infer correctness from latency and error rate. Rejected because fast systems can still lose, duplicate, leak, or corrupt work.

### 8. Baselines are same-environment evidence, not substitutes for targets

Comparative scenarios pin a prior same-environment baseline run and declare regression bounds before execution. A missing or incompatible required baseline yields `not_run`. Baseline regressions are reported separately from absolute §14 thresholds.

The first implementation establishes the current single-origin connector/MCP and storage envelope before claiming distributed capacity. Any live-public-surface run requires explicit host authorization, a declared blast radius and abort criteria, isolated test identities with cleanup, and coordination with uptime canaries. It must not invoke a model/provider or consume maintainer quota. Later Postgres, Realtime, fleet, settlement, and failure-injection scenarios remain `not_run` until their declared real substrates or real-equivalent isolated environments exist.

Alternative: wait to build evidence until the target architecture is complete. Rejected because the current system needs a measurable baseline and the validator can be hardened now.

## Risks / Trade-offs

- **[Risk] Capability owners keep duplicating evidence fields.** → Require registry references to a schema version and review later changes for copied field lists.
- **[Risk] `not_run` becomes a neutral-looking status.** → Require blocking codes and prohibit describing it as absence of risk or production readiness.
- **[Risk] Fingerprints, traces, or diagnostics leak secrets, user content, or identity.** → Store safe identifiers, hashes, tiers, resource facts, and pseudonyms; exclude credentials and user-authored payloads from load evidence.
- **[Risk] A public connector baseline disrupts users or triggers provider spend.** → Require host authorization, isolated identities, cleanup, a bounded traffic envelope, abort criteria, canary coordination, and no model/provider invocation.
- **[Risk] Raw artifacts become too large.** → Content-address and retain them under an explicit policy while keeping the immutable manifest small.
- **[Risk] Stale scenarios accumulate.** → Version registry entries and exclude optional/inapplicable entries from required rollups without deleting historical evidence.
- **[Risk] This contract blocks capability development.** → Allow owner-local adapters and thresholds; centralize only the evidence envelope and conformance validator.

## Migration Plan

1. Land this reviewed target OpenSpec and mark the April pre-draft superseded.
2. After the existing `tests/load/` claim releases, create the dependent implementation change for `tests/load/_protocol/` and its conformance suite.
3. Implement the manifest validator/writer, registry loader, fingerprint collector, artifact digest checks, oracle interface, fault/reconciliation formats, and rollup evaluator.
4. Add a current-system connector/MCP and single-origin baseline adapter.
5. Update capability-owned §14 changes to reference the shared schema without moving their thresholds or adapters.
6. Add real-substrate or real-equivalent adapters as their substrates land; retain explicit `not_run` results until then.
7. Sync and archive this change only after protocol implementation, conformance tests, one real baseline adapter, independent review, and dated evidence pass.

Rollback is removal of the unactivated target change or its later implementation before capability owners adopt it. Historical run manifests are never rewritten during rollback.

## Open Questions

- Which content-addressed artifact store and retention window will the implementation use?
- Which registry entries form the first required connector-first baseline packet, and does the inherited research target of 2,000 concurrent MCP sessions remain the accepted target?
- Which deployment environment is accepted as the same-environment baseline for canonical `/mcp`? This is owned by the STATUS `host-decision` row for the isolated `/mcp` baseline environment and traffic envelope.
- Which machine-readable blocking-substrate codes should be shared versus capability-specific extensions?

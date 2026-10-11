## Context

`2026-07-22-openspec-full-coverage-audit.md (archived in git)` classifies four
independent target groups as absent from both canonical as-built specs and
complete active changes. Their primary provenance is the integrated
full-platform architecture (§§5, 8, 13, 14, 24, 26, 27, 30, 32, and 33) plus
the legacy moderation, tray, authoring, and handoff execution specs.

Several neighboring capabilities already exist:

- `desktop-host-runtime` owns the source-installed tray that ships today;
- `domain-plugin-runtime`, `graph-execution-substrate`, and
  `evaluation-runtime-and-scenarios` own current plugin, graph, and evaluator
  behavior;
- `external-effect-adapters` and `external-effect-receipts` own generic
  post-run effects, consent, and receipt authority;
- `evaluation-outcomes-and-attribution` owns the current unverified outcome
  registry.

This change must extend toward the target without duplicating or rewriting
those as-built owners. It also must not import the PLAN-gated catalog,
private-data, portability, or collaboration decisions.

Owner-scoped authoring drafts use the already-shipped draft/ownership patterns
owned by `wiki-commons` and `identity-auth-and-access-control`; they are not a
new private catalog or private-content policy decision.

## Goals / Non-Goals

**Goals:**

- Preserve the four complete target outcomes as strict-valid executable
  requirements and file-bounded tasks.
- Make authorization, evidence levels, idempotency, irreversible-effect
  confirmation, failure behavior, and scale gates explicit.
- Cover browser-only, local-app, and contributor paths where the target spans
  user tiers.
- Keep every public chatbot behavior composable through the canonical handle
  routers instead of adding legacy standalone tools.

**Non-Goals:**

- Claim any target behavior is already built.
- Sync these requirements into canonical specs before implementation.
- Decide Postgres versus another canonical store, platform-private storage,
  account deletion/export semantics, or collaborative catalog topology.
- Add production paid-market inbox/matching behavior; that remains a separate
  market lane.
- Re-specify generic effects, receipts, evaluators, graph execution, or the
  current source tray.

## Decisions

### Each missing outcome receives one explicit target owner

Four new capabilities separate moderation workflow, installation/distribution,
authoring/optimization, and handoff/outcome lifecycle. **Moderation left this
change on 2026-07-25** for `openspec/changes/moderation-and-abuse-response/`
(task 6.3 discharge) once its implementation started on external draft PRs; the
one-owner-per-outcome decision is unchanged, the owner simply lives in its own
independently completable change. Three capabilities remain here. Cross-capability
integration is expressed as a dependency on current canonical primitives,
never by copying those primitives into a second owner.

### Public behavior composes under the canonical handle surface

The legacy documents named standalone RPC/tool families. This change specifies
actions and outcomes without creating advertised MCP handles. Implementation
must route chatbot calls through the canonical thin routers and keep web/tray
UI as alternate presentations of the same authorization boundary.

### Target guarantees are technology-neutral where PLAN has not chosen a substrate

Requirements name observable behavior, ownership, evidence, and failure
semantics. They do not require Supabase Edge Functions, a particular database,
or a particular updater library. Tasks may select replaceable implementation
components after verifying the final substrate against current PLAN.

### External claims use explicit evidence levels

A successful adapter receipt can prove that a destination accepted a handoff,
but it does not prove later publication, peer review, citation, or business
impact. Handoffs and user attestations therefore carry distinct submitted,
accepted, externally verified, user-attested, disputed, rejected, and orphaned
states. Ranking consumers must preserve those distinctions.

### The existing outcome registry remains the single generic outcome owner

The shipped `extensions` `outcome_event` registry is extended rather than
replaced. `record_outcome` remains the generic user-attestation entry point;
handoff acceptance writes into the same registry with receipt/source linkage,
and append-only evidence events advance its state. The existing `gate_events`
table remains the specialized cited-in Goal/Branch attestation lifecycle. An
outcome may cite a gate event, but neither registry silently mirrors the
other's verification state. The handoff package owns only external-effect
lifecycle/provider evidence; it does not create a second generic outcome table
or API.

### Draft execution is safe by default and publication is explicit

Authoring test runs default to simulated effects, run under isolation and
budgets, and never publish. Real effects require explicit authority and
confirmation. Published artifacts are new immutable versions with provenance;
optimization cannot mutate a fixed evaluator or merge a candidate without the
declared policy.

An installed sandbox binary, availability probe, subprocess boundary, or
draft-visible status or isolation flag is diagnostic only. It cannot satisfy
`requires_os_isolation`. The authoring owner must refuse such a test before
execution unless trusted composition admits the exact immutable draft/test
request against a real active distributed-execution backend binding and seals
that admission outside the draft. A successful test requires the binding's
reviewed verifier to accept launch evidence bound to the exact request,
execution, and result; neither draft input nor a host capability report can
attest that the selected execution used it.

Optimization-candidate leases are request-local scheduling records. The
implementation must compare them with the active `distributed-execution`
lease-store owner and reuse its generic lease/fencing primitive if it is
available and semantically compatible; it must not create a third general
distributed lease mechanism.

### Concurrency/load proof is part of implementation, not follow-up polish

Each capability has a concrete §14 proof task: installer/update fleet behavior,
authoring isolation and experiment leases, and handoff idempotency/provider-budget
behavior (moderation flag/decision races moved with the capability to its own
change). A feature is not done merely because its single-user happy path passes.
A *partial* proof does not check the task either: the authoring lane landed the
concurrency/isolation clauses of its §14 task on 2026-07-25 and left task 4.6
open because the experiment-lease clauses belong to the unbuilt optimization
half.

## Risks / Trade-offs

- **Legacy documents prescribe stale standalone tools** → Preserve behaviors,
  not those wire names; test that no new advertised handle appears.
- **Target specs accidentally become canonical truth** → Keep the change
  active and explicitly prohibit sync/archive until implementation lands.
- **Sandbox claims exceed the selected runtime** → Require adversarial
  filesystem, network, CPU, memory, secret, and cross-tenant tests against the
  actual runtime.
- **A handoff receipt gets inflated into an outcome claim** → Record the exact
  evidence level and advance only on provider-specific proof.
- **Moderator or updater authority centralizes in one operator/key** → Require
  council/release-key rotation and recovery paths with no host-only runtime
  button.
- **Scale tests become nominal assertions** → Require concurrent invocation,
  failure injection, and bounded resource measurements, not static config
  inspection.

## Migration Plan

1. Implement each capability in its bounded file set with storage migrations
   and focused tests.
2. Run capability-specific security and §14 concurrency/load proof.
3. Verify public chatbot flows through the live connector where applicable and
   clean-machine installer flows on Windows, macOS, and Linux.
4. Obtain independent code-to-requirement review.
5. Sync each implemented capability into canonical specs in the same landing
   lane, archive this change, and remove its STATUS row.

Before implementation, rollback is deletion of this active change. After
partial implementation, each capability can land separately only if its delta,
tasks, and acceptance evidence are split into an independently complete change;
partial code must not cause the whole target change to be synced.

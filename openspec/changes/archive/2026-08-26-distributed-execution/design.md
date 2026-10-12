## Context

The canonical `distributed-execution` spec describes what is actually on
`origin/main@405a7b7e`: a backend-neutral `runner/v1` seam, an unavailable
built-in backend, and a detached Linux diagnostic. Merged PR #1485 is the only
completed implementation item in this change. Main has no signed B2 authority
spine and no production caller of the runner.

The earlier program was developed on a stack of draft PRs. Their useful
security findings remain inputs, but their branches are not integration
candidates:

- #1472 spans the S0/S1/S3/S5 substrate and 104 files.
- #1477 carries the M1/B2 spine on top of more than 200 inherited commits.
- #1479, #1481, and #1487 stack domain contracts, evidence-ledger hardening,
  and blob/lock hardening on #1477.
- #1491 contains two useful daemon-identity commits inside a 386-file lineage.
- #1478 adds authority CI but is stacked on the old B2 branch.
- #1572 is explicitly design-gated, implements a different M2
  branch-version change, and deliberately breaks legacy IDs. It is excluded.

Meanwhile current main now contains the epoch-2 request/admission queue and the
#1697 trusted worker descriptors. That system is an internal scheduling
boundary. Distributed execution must compose with it later without treating
its receipts, queue rows, descriptors, or scheduling leases as B2 authority.

The detailed end state remains the host-approved V1-V8, S0-S16, B01-B44 program
in `2026-07-18-distributed-execution-platform.md (archived in git)`.
This design changes delivery order only. It does not shrink the destination.

## Goals / Non-Goals

**Goals:**

- Preserve the complete V1-V8 destination, S0-S16 stage map, and B01-B44
  anti-loss ledger in the active OpenSpec change.
- Define an immediate, bite-sized apply slice that builds a dark, test-only,
  final-shaped signed-authority spine.
- Keep M1 platform signatures, M2 content re-derivation, and M3 external
  re-confirmation honest and separate while giving consumers one sealed
  `Verified[T]` calling convention.
- Define immutable signed domains and canonical capsule, grant, candidate, and
  terminal records before any live route can consume them.
- Make replay, fencing, generation, idempotency, blob proof, and lock ordering
  fail closed under mutation and restart.
- Preserve #1697 worker descriptors and explicitly separate admission,
  scheduling, provider-attempt, and B2 execution evidence.
- Extract only proven behavior from stale PRs onto current main with failing
  tests first.

**Non-Goals for the first apply slice:**

- No real provider call or provider credential.
- No payment, escrow, settlement, reputation, or other money authority.
- No live credential, WorkOS, enrollment, bearer-token, or device-key rollout.
- No `run_graph` bridge, checkpoint resume, admission/queue integration, or
  graph-cycle claim integration.
- No HTTP/MCP/ASGI execution route and no production composition root.
- No real sandbox backend, daemon coordinator, source staging, model broker,
  platform worker, or owner-daemon execution.
- No deployment, feature flag, production trust root, real key custody, or live
  signing key.
- No defense against a malicious process running as the test harness's own OS
  principal rewriting the fake root's temporary SQLite namespace. D0 requires
  an exclusively owned test temp directory; a non-test persistent authority
  store requires OS custody or a proven descriptor-relative SQLite VFS.
- No GitHub effect, approval, merge, repository mutation, or token vending.
- No market selection, live enrollment, live acceptance, or user-surface claim.

These exclusions are slice boundaries, not program deletions.

## Decisions

### 1. Use a dark pre-V1 apply slice without redefining V1

The immediate apply slice is named **D0: dark authority contract spine**. D0
uses final-shaped interfaces but only fake, test-owned keys and storage. It is
not a new vertical slice and does not satisfy V1. V1 still ends only when a real
persisted job traverses authenticated claim, signed grant, candidate/blob
acceptance, fenced signed completion, and restart replay through a non-test
composition root.

D0 is intentionally narrower than the old #1477 end-to-end branch. It proves
that the authority contracts can reject forgeries and replay deterministically
without taking on transport, credentials, providers, queues, graph execution,
or production custody in the same change.

Alternative considered: revive #1477 as V1 immediately. Rejected because its
lineage is stale and broad, its production trust/custody story is incomplete,
and current main now has queue/runtime contracts that did not exist at its
base.

### 2. Keep M1, M2, and M3 separate

Positive authority is re-derived at the decision point:

- **M1 - platform signature:** verify purpose-specific canonical bytes against
  a release-pinned platform trust root. M1 covers platform-decided grants such
  as capsules, execution leases/grants, and terminal attestations.
- **M2 - content addressing:** read or resolve the exact object and recompute
  its digest. M2 covers blob bytes, result digests, source manifests, Git
  commits/trees/heads, and patches. M2 never gains a platform signature merely
  to restate a hash.
- **M3 - external re-confirmation:** freshly verify through the external
  authority's cryptography or protected API/transaction. M3 covers WorkOS
  JWT/JWKS identity, GitHub protected state and mutation, and externally
  authoritative payment facts.

One mechanism's evidence never promotes into another. A platform-signed hash is
not M2 proof of bytes. A row saying GitHub merged is not M3 confirmation. A
valid WorkOS token is not an M1 execution grant.

Alternative considered: one universal `RecordVerifier`. Rejected as false
unification because it would turn hashes and external facts into signature
lookups and hide which authority was actually re-derived.

### 3. Seal `Verified[T]` minting by mechanism

`Verified[T]` is frozen, final, non-subclassable, non-copyable, and
non-pickleable. Direct construction is unavailable outside the authority
package. A closure-held or otherwise package-private mint token is shared only
with reviewed mechanism adapters:

- the M1 record verifier mints after canonical signature and domain-contract
  verification;
- an M2 verifier mints after reading/resolving and hashing the exact content;
- an M3 verifier mints after fresh external verification.

The wrapper carries the verified value, mechanism, domain/purpose, evidence
digest, verifier/trust-set identity, and verification time where relevant.
These metadata are diagnostics and binding context; they do not themselves
grant authority. Python sealing makes misuse conspicuous and testable, not
cryptographically impossible inside a compromised process.

No fake may call the raw mint seam directly. D0 fakes must exercise the same
mechanism adapters with test-owned facts.

### 4. Signed domains have immutable contracts and canonical records

Every signed record uses RFC 8785/JCS canonical JSON, JSON-type-strict field
validation, bounded integers, lowercase canonical digests, and an exact domain
separator in the signature preimage. Unknown domains, versions, fields, or
unclassified fields fail closed. A caller cannot pass `unbound_fields`, a
field allowlist, a verifier key, or a custom binder.

Each domain contract classifies every payload field as:

- **row-bound:** must equal independently trusted durable/request state;
- **specialized-validated:** checked by the domain's fixed validator; or
- **inert:** signed but structurally prohibited from affecting authority, with
  a documented reason.

The initial final-shaped records are:

- `ExecutionCapsuleV1` (platform M1): binds purpose/schema/key ID, owner,
  audience daemon, job, capsule, attempt/generation, source and policy
  identities, issued/expiry time, and immutable execution limits.
- `ExecutionGrantV1` (platform M1): binds owner, daemon, job, capsule digest,
  lease ID, generation/fence, expiry, capability ceiling, and idempotency key.
- `ExecutionCandidateV1` (daemon device signature): binds the exact owner,
  daemon/device key, job, capsule, lease, generation/fence, result digest,
  canonical blob references, status, and submission idempotency key. A valid
  device signature proves possession only; acceptance still requires M1 grant
  and fresh M2 proofs.
- `ExecutionTerminalV1` (platform M1): binds job, capsule, lease,
  generation/fence, accepted candidate/result/blob-set digest, terminal state,
  and completion idempotency key.

Signature inputs are
`UTF8(domain_separator + "\0") || SHA256(canonical_payload)`. Purpose-specific
keys and domains prevent a valid record in one domain from authorizing another.
Derived values such as thumbprints are recomputed at every consumption site.

Alternative considered: generic signed dictionaries with consumer-provided
field bindings. Rejected because every future caller would recreate the
authority boundary and could neutralize it.

### 5. Generation, fences, idempotency, and replay are one contract

Claims allocate a monotonically increasing generation/fence within the job's
durable evidence namespace. The acceptance floor is at least the highest
persisted fence in the append-only evidence ledger, so restoring an older
mutable row cannot revive superseded authority.

Every state transition uses a stable idempotency key bound into the signed
record. A retry of byte/content-identical verified evidence returns the same
receipt. Reuse with different verified content fails closed. Candidate and
completion compare owner, daemon, job, capsule, lease, generation, fence, and
idempotency across independent sources.

Replay is verify-first:

1. load candidate terminal attestations without trusting row status;
2. verify domain, schema, signature, bindings, generation/fence, and content;
3. ignore unverifiable junk for positive authority while retaining diagnostics;
4. collapse byte/content-identical valid attestations; and
5. reject two distinct valid terminal facts as stored-state corruption.

Mutable terminal columns are projections only. They may narrow or report, never
create or replace the verified terminal fact.

### 6. Stage trust roots and custody before production activation

D0 uses an explicit `TestAuthorityRoot` containing deterministic test keys and
an in-memory or temporary test store. It is constructed only by the test
composition root.

Later non-test stages require, in order:

1. a release-pinned signed trust manifest with purpose-separated public keys;
2. local verification of manifest provenance and rotation/revocation rules;
3. non-exporting, schema-specific signer capabilities outside
   control-plane/user-code memory;
4. local re-verification of every returned signature before persistence; and
5. a single production composition root that constructs all authority routes
   from the pinned trust set.

No environment variable, mutable DB row, caller request, queue descriptor, or
auth grant selects an active signing/verification key. WorkOS stays M3 and is
not platform re-signed.

### 7. The fake composition root has hard production denial

The D0 composition root is named and packaged as test-only. Construction
requires an explicit test sentinel supplied by the test harness and rejects:

- production or unknown runtime mode;
- non-temporary durable paths;
- route/app registration;
- provider, credential, market, GitHub, or queue adapters;
- externally supplied issuer, key material, binder, or verifier; and
- import/use by the production composition module.

The production root remains absent until its custody prerequisites are
implemented. There is no "unavailable then silently fake" fallback. A
production call must raise a stable configuration error before signing,
leasing, dispatching, or persisting authoritative state.

### 8. Blob proof and lock order are decision-point requirements

Blob authority is M2. Candidate JSON and database bindings are consistency
checks only. Acceptance resolves the exact blob under the physical storage
root, reads its bytes, recomputes size/digest, and returns
`Verified[BlobRef]`. Completion consumes fresh verified proofs and never a raw
row reference.

All candidate/completion mutations use one lock order:

1. resolve and acquire the physical blob-root coordinator; then
2. open the SQLite transaction.

Physical identity, not a normalized path string, keys the coordinator.
Windows extended paths, case, separators, junction/symlink targets, and tested
UNC/drive aliases must converge; inability to establish identity fails closed.
The blob index is operation-local: reload, validate, mutate, and atomically
persist under the root lock. Evidence-table schema/trigger/index contracts are
validated exactly and never auto-repaired.

D0 narrows its fake candidate sink to exactly one result blob, so
`result_digest` must equal that freshly M2-proven object's digest. The
final-shaped carrier retains a blob tuple for later manifests/artifacts, but
multi-blob result designation is not invented by the fake root.

### 9. Receipts, scheduling leases, provider attempts, and B2 grants never promote

The following are separate domains with no promotion rule:

- request admission receipt: proves a canonical request was admitted;
- epoch-2 task/worker descriptor: proves current scheduling eligibility;
- internal scheduling lease/heartbeat: reserves queue work;
- provider-attempt receipt: records how a provider route was attempted and what
  result/error it produced;
- B2 signed execution grant/lease: grants a specific owner daemon authority
  over one job/capsule/lease/generation/fence;
- candidate and terminal records: prove submission possession and platform
  terminal acceptance under that exact B2 authority.

Any one may be an input to a later decision that independently verifies all
required domains. None is converted, copied, wrapped, or treated as positive
authority for another domain. In particular, #1697's server-derived
`queue_protocol_version=2`, capability, build/config SHA, boot/worker/runtime
IDs, universe, and 90-second liveness remain intact. They can make a worker
ineligible but cannot mint an execution lease, provider access, credential,
payment right, candidate acceptance, or terminal receipt.

### 10. Extract stale work onto current main in dependency order

No named stale PR is merged, rebased, or cherry-picked wholesale. Each step
starts from current main, ports a failing real-sink/mutation test, then writes
the least implementation needed:

1. **#1472 contract inventory:** extract only reviewed capsule/result/device
   domain vectors and mutation tests that still apply. Do not take its S0
   worker/deploy removals or broad runtime/config changes.
2. **#1477 M1/B2 primitives:** extract the smallest M1 signed-record foundation,
   immutable carrier shapes, and dark lease/candidate/terminal test spine. Do
   not take `run_graph`, transport, providers, production roots, or unrelated
   inherited commits.
3. **#1479 domain contracts:** remove caller-neutralizable binding policy and
   add the exact immutable domain partitions and owner binding.
4. **#1481 evidence ledger:** add monotonic generation floor,
   replacement-resistant append-only guards, and verify-first replay.
5. **#1487 blob/locking:** add fresh M2 blob proof, physical-root coordination,
   one lock order, operation-local index, and exact table validation.
6. **#1491 daemon identity:** later, once the relevant S3 carrier and
   consumption sites exist on current main, port only the thumbprint/key
   binding fix and non-vacuous per-fence probes.
7. **#1478 CI:** after focused test filenames and modules stabilize, recreate
   the Python 3.11 and semantic authority gates from current paths; keep the
   suspicious-read scan advisory.

PR #1572 is excluded from every extraction step. Its generic M2 branch-version
work, PLAN gate, and legacy-ID break are separate.

This order preserves stacked semantic dependencies while refusing stacked git
lineage.

### 11. Rollback removes dark wiring, never weakens verification

D0 has no production route, migration, or persisted production state.
Rollback is therefore deletion/disablement of the test-only composition and
its new test fixtures/modules as one change. Existing canonical runner and
diagnostic behavior remains untouched.

After durable B2 storage begins, rollback may stop new claims and leave work
pending, but must retain evidence tables, trust manifests, signatures, blob
bindings, generation floors, and terminal attestations for audit/replay. It
must never:

- reset a generation/fence;
- convert signed authority back into row authority;
- bulk-sign mutable legacy rows;
- auto-repair or recreate an evidence ledger;
- fall back to fake keys, unsigned mode, v1 JSON execution, or platform compute;
- reinterpret an epoch-2 scheduling claim as execution authority; or
- delete accepted evidence before an independently verified export/retention
  step.

Live cutover rollback is per surface and host-approved. Signer or external
authority failure leaves work pending rather than widening authority.

### 12. Review and live gates are staged

Every implementation change receives focused tests and independent diff
review. Confirmed authority findings require a decision-level mutation that is
proven red before the fix and green after it.

Before any production route, live enrollment, enforcement flip, credential
use, provider execution, GitHub effect, market/money behavior, deployment, or
rendered acceptance test:

- all required D0/V1 prerequisites and migrations are integrated on current
  main;
- the production trust root and custody design has explicit host approval;
- both current model families review the same integrated deploy candidate;
- the semantic mutation registry, CPython 3.11 gate, mirror parity, and
  concurrency/load gates pass;
- the affected public surface passes canary probes;
- the final user proof is a rendered chatbot conversation through the live
  connector; and
- post-fix real-user clean-use evidence is checked, or a dated watch remains.

D0 is not eligible for live acceptance because it intentionally has no live
surface.

### 13. Bind execution admission outside the frozen runner wire

Distributed execution owns four immutable, versioned records. Static release
policy, per-request preflight state, the outer seal, and actual-launch evidence
are deliberately separate:

```text
BackendProfileBindingV1 {                 # static release/policy record
  schema_version = execution-backend-profile/v1
  signing_key_id + binding_id + activation_generation
  backend_implementation_id + backend_release_digest + protocol_version
  execution_profile + supported_job_capabilities
  supported_guarantee_property_ids
  planned_configuration_schema_digest
  preflight_contract_id + preflight_contract_digest
  launch_evidence_contract_id + launch_evidence_contract_digest
  producer_identity + authenticity_mechanism + evidence_key_custody
  preflight_verifier_id + launch_verifier_id + trust_set_id
  revocation_ref + revocation_digest + issued_at + expires_at
}

BackendPreflightEvidenceV1 {              # fresh request-bound readiness
  schema_version = execution-backend-preflight/v1
  admission_id + job_id + inner_request_digest
  profile_binding_digest + backend_release_digest
  capability_self_test_digest + planned_configuration_digest
  producer_identity + verifier_id + trust_set_id + revocation_generation
  observed_at + expires_at
}

ExecutionAdmissionCapsuleV1 {             # final purpose-separated M1 seal
  schema_version = execution-admission-capsule/v1
  signing_key_id + capsule_id + admission_id + job_id
  inner_request_schema_version + inner_request_digest
  execution_requirement_value + execution_requirement_digest
  profile_binding_digest + preflight_evidence_digest
  planned_configuration_digest
  authority_evidence_ref + authority_evidence_digest
  authority_generation + issued_at + expires_at
}

BackendLaunchEvidenceV1 {                 # authenticated actual-launch proof
  schema_version = execution-backend-launch-evidence/v1
  evidence_id + admission_id + admission_capsule_digest
  job_id + inner_request_digest + backend_execution_id
  profile_binding_digest + backend_release_digest
  planned_configuration_digest + actual_configuration_digest
  result_schema_version + result_digest
  producer_identity + authenticity_mechanism
  verifier_id + trust_set_id + revocation_generation
  proved_property_ids
  property_proofs: sorted (property_id, evidence_kind, ref, digest) tuples
  started_at + finished_at
  cleanup_subject_digest + cleanup_ref + cleanup_digest + cleanup_observed_at
}
```

`BackendProfileBindingV1` is a release-pinned M1 signed artifact. It contains
no current self-test result and no per-job planned configuration. The active
trust manifest fixes its generation, revocation state, producer identity,
authenticity mechanism, evidence-key custody class, canonical preflight and
launch-evidence contracts, and reviewed verifiers. A request, worker, queue
row, provider/backend response, environment variable, mutable diagnostic, or
caller cannot create, select, modify, refresh, or widen it.

The exact serialized `os_isolated` property identifiers are
`kernel_process_boundary`, `filesystem_default_deny`,
`network_default_deny`, `cpu_limit`, `memory_limit`, `process_limit`,
`wall_time_limit`, `output_limit`, `platform_secrets_absent`,
`undeclared_devices_absent`, `bounded_cleanup`, and
`request_bound_evidence`. `vm_isolated` adds `guest_kernel_boundary` and
`host_devices_default_deny`. These identifiers encode the closed semantics
owned by `engine-os-sandbox`; unknown identifiers deny, and labels are derived
only after property-set inclusion.

Trusted code converts the complete existing `SandboxJobRequest.to_wire()`
object to RFC 8785/JCS bytes and hashes those exact bytes. Before admission it
decodes the bytes and recursively compares the decoded value to the source
with JSON-type strictness, safe-integer bounds, and IEEE-754 bit identity;
values that do not round-trip losslessly (including `1.0`, `0.0`, `-0.0`,
non-finite numbers, or unsafe integers) are rejected rather than aliased to a
different value. The canonical bytes cover schema, `job_id`,
`idempotency_key`, `owner_scope`, `capability`, derived `actions`, `payload`,
`workspace_ref`, and `credential_grant_ref`; they are the only dispatched
request representation. The backend verifies the digest before decoding and
executes only the value decoded from those same bytes, never the caller's
original Python object or a separately reserialized request.

Trusted code then creates a fresh unguessable `admission_id`, resolves the
active profile binding and every owner-defined requirement reference/digest,
derives the exact planned launch configuration, and obtains authenticated
`BackendPreflightEvidenceV1` bound to that admission ID, job, full request
digest, binding, backend release, and planned configuration. The record
includes observation/expiry and the exact revocation generation; it cannot be
reused after expiry, revocation, binding replacement, request mutation, or for
another admission.

The complete execution-authority composition root then seals the final
`ExecutionAdmissionCapsuleV1` under its purpose-separated M1 domain. Sharing
`admission_id` avoids a circular digest: preflight binds the admission ID and
request facts; the later capsule binds the same ID plus the preflight digest.
Both dispatcher and backend independently hash the identical canonical byte
carrier and compare every field before launch. The capsule expiry is no later
than the profile, preflight, or authority evidence expiry.

The capsule authenticates admission facts only. Dispatch re-verifies the
independent provider/B2 authority, its generation, expiry, and revocation at
the decision point; a valid admission capsule cannot promote or preserve stale
authority. Ordinary provider work retains #1784 `ProviderInvocation`, and
accepted-market work retains B2/B13. The four records above define the
runner-backed instantiation only; other owners use their native request
identity and carriers rather than inventing a runner `job_id`.

After launch, the selected backend's evidence producer emits canonical
`BackendLaunchEvidenceV1` under the producer identity, authenticity mechanism,
key-custody rule, verifier, and trust set fixed by the active profile binding.
For a local isolation backend, the evidence key/attester lives outside the
model-controlled child; for a remote/VM backend, the reviewed binding names
the remote/guest attestation root. Caller data, the result payload, and the
model-controlled process cannot choose a producer, key, mechanism, verifier,
trust set, evidence kind, or property identifier.

Every proved property has exactly one sorted tuple binding its canonical
identifier to a contract-allowed evidence kind and exact reference/digest.
The verifier checks admission/capsule/request/execution identity, binding and
revocation generations, planned versus actual configuration, result hash,
timestamps, freshness, each property proof, and cleanup subject/proof before
output is exposed. Evidence replay is accepted only when the canonical record
is byte-identical for the same admission and result; cross-admission reuse or
different content under the same `evidence_id` fails closed. Cleanup proof
must name the actual execution subject and be observed after finish.

Preflight proves only current capability and exact planned configuration. Only
fresh authenticated actual-launch evidence can prove the complete guarantee
set. `RunnerCapabilities`, `isolation_enforced`, executable probes,
`EnforcementReceipt`, tier labels, queue/admission receipts, or a valid outer
seal without launch evidence may veto or diagnose but cannot prove execution.
Any failure maps to shared terminal `ExecutionAdmissionError`; invalid launch
evidence uses `backend_evidence_invalid` and cannot become success or fallback.

This sidecar contract leaves `runner/v1`, `runner-job/v1`, `runner-result/v1`,
`SandboxJobRequest`, `SandboxJobResult`, `EnforcementReceipt`, statuses, and
the existing `JobCapability`/action mapping unchanged. Production construction
remains absent until task 7.2's trust distribution, purpose-separated custody,
rotation/revocation, and persistent-store boundary are explicitly approved and
implemented; no test/generated/unsigned fallback is permitted.

Alternative considered: add fields to the frozen inner wire. Rejected because
it breaks existing consumers. Alternative considered: place self-test and
planned configuration in one release binding. Rejected because it makes an
immutable policy artifact replayable as volatile per-request evidence.
Alternative considered: let each backend invent evidence later. Rejected
because untyped signed booleans cannot mutation-prove OS enforcement, request
identity, result integrity, or cleanup.

## Risks / Trade-offs

- **Risk: a dark fake is later wired accidentally.** -> Use a test-only package,
  explicit sentinel, production-mode refusal, import/wiring guards, and no
  production root until custody exists.
- **Risk: `Verified[T]` is mistaken for a process security boundary.** -> Treat
  sealing as misuse prevention; keep cryptographic/content/external
  re-verification at every authority sink.
- **Risk: stale PR code silently reverts current main.** -> Port tests and
  behavior only, record source commit/PR per extraction, and forbid wholesale
  stale lineage.
- **Risk: D0 is reported as V1.** -> Keep separate D0 naming, explicit
  non-goals, and V1 exit criteria in spec/tasks.
- **Risk: queue/admission evidence leaks into execution authority.** -> Preserve
  distinct types/domains and add a test where a valid #1697 descriptor plus a
  won scheduling claim still cannot execute without B2 authority.
- **Risk: signature correctness obscures content or external staleness.** ->
  Keep M2/M3 minters separate and require fresh proofs at the decision point.
- **Risk: physical blob identity is not portable on every filesystem.** ->
  Support only proven identity strategies and fail closed otherwise; never
  fall back to path-string locking.
- **Risk: D0 SQLite hardening is mistaken for hostile-host custody.** -> The
  fake root rejects aliases and owns its connection, but does not claim safety
  from same-principal path replacement or direct database mutation. Keep every
  production root absent until OS custody or a proven custom VFS closes that
  boundary.
- **Risk: strict replay lets junk accumulate.** -> Ignore unverifiable junk for
  positive authority but report/quarantine it separately; reject conflicting
  valid facts.
- **Risk: later live migration harms uptime.** -> Use bounded shadow/dual-verify
  windows, per-surface host go/no-go, pending-on-failure behavior, and explicit
  rollback without authority downgrade.
- **Trade-off: the complete ledger is large.** -> Keep it because prior
  unbundling lost work and falsely marked open branches as landed. A row leaves
  only with landed proof or an approved superseding decision.

## Migration Plan

1. Land this OpenSpec refresh only; it changes no runtime behavior.
2. Apply D0 from current main: test-owned canonical records, sealed evidence,
   fake composition root, generation/replay semantics, and blob proof/locking.
3. Independently review D0 and prove production denial plus authority mutations.
4. Continue V1 by extracting the remaining current-main-compatible behavior in
   the order above, re-claiming exact runtime/test files for each bite.
5. Design and approve the production trust root/custody before any route
   activation.
6. Integrate with epoch-2 scheduling only after PLAN/OpenSpec reconciliation;
   retain #1697 descriptors and require independent B2 signed authority.
7. Advance V2-V8 in order, retaining every open S-stage and B-row.
8. For each live surface, use shadow/dual verification, explicit host go/no-go,
   public canary where applicable, rendered-chatbot proof, and clean-use watch.

## Open Questions

- Which current-main package owns the sealed evidence core without making M2/M3
  depend on M1's `RecordVerifier`?
- Which deterministic test-signing implementation gives cross-platform vectors
  without becoming a production dependency or fallback?
- What exact OS-backed physical directory identity strategies are supported on
  Windows, Linux, and macOS in D0 and later?
- What is the durable schema namespace for dark lease/evidence tests, and what
  migration boundary will later carry it into production without accepting
  stale PR schemas?
- Which component will own public-source snapshot production (B43) before V1
  can use a real job?
- What is the production trust-manifest distribution, activation, rotation,
  and revocation protocol (B14/B40)?
- When PLAN's file-locked claimer wording is reconciled with epoch-2
  transactional scheduling, which adapter performs the scheduling-to-B2
  handoff without merging the two lease types?

None of these questions authorizes broadening D0. Unresolved production
questions keep production wiring absent and fail closed.

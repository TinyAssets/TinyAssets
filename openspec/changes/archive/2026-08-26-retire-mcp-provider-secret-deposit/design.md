## Context

The live implementation accepts a raw bring-your-own provider API key through the hidden
`universe action=set_engine` path, writes a base64-recoverable
`llm_api_key` record into a per-universe JSON file, and later overlays that
secret into a provider child environment. Slice A0 now prevents ambient host
and maintainer authority from entering a universe-scoped child, but selected
universe vault material remains recoverable control-plane custody.

This change is deliberately limited to `llm_api_key`. Existing
`llm_subscription`, `vcs`, and `social` records retain their canonical
behavior, storage limitations, and separate owners. In particular, this
change does not silently adopt #1736's account-token lifecycle or remove the
truth that retained vault secrets are protected only by filesystem
permissions.

The platform architecture requires requester-owned BYOC, no maintainer quota
consumption, minimal canonical MCP primitives, and private data remaining on a
host. The exact-seven public surface has no `set_engine` handle, and the active
legacy-tool retirement change will unregister the hidden `universe` tool.

Opus 5 reviewed this boundary on current main and returned ADAPT. Its durable
verdict is
`2026-07-24-retire-mcp-provider-secret-deposit-opus5-review.md (archived in git)`.

## Goals / Non-Goals

**Goals:**

- Remove raw API-key elicitation and reject unsupported `llm_api_key` fields at
  ingress without echo before application mutation or any inventoried
  TinyAssets-owned log, ledger, trace, capture, crash-report, or argv sink;
  client-owned upstream transcripts remain outside TinyAssets control.
- Keep requester-supplied provider API keys only in requester-controlled
  native OS secret storage.
- Carry a non-secret binding reference through merged PR #1784's
  `ProviderAssignmentAdmission` and requester-local assignment/launch barrier,
  or an owner-accepted production B2 market
  authority path; keep `runner/v1` opaque and D0 fake-only/production-denied.
- Resolve only behind `ProviderExecutor.start()`: inside executor
  child/request memory for CLI/local/in-process transports, or solely inside
  the outbound owner's credential-blind proxy for remote HTTP.
- Retire legacy recoverable records without decoding/exporting them.
- Fail closed through enrollment, migration, rotation, deletion, host expiry,
  and every crash/retry point.

**Non-Goals:**

- A new MCP tool/action or a second provider-routing/assignment contract.
- A second grant, lease, generation, or fence system.
- Server-side encrypted provider-secret custody, including #1469's backend.
- Organization-pooled provider credentials or cross-principal delegation.
- Subscription, VCS, or social credential retirement; those retain their
  existing canonical behavior and owners.
- Claiming the local custody race proof satisfies the broader §14 Track J load
  suite.
- Runtime edits while #1784's provider-authority implementation,
  `bind-host-principal-to-account`, #1736's client/native-store seam,
  universe-creation, #1484, and
  distributed-execution owners still hold their seams.

## Decisions

### 1. Provider custody is a separate capability

`credential-vault` describes the shipped per-universe file store;
`desktop-host-runtime` describes the source tray; PR #1736's runtime branch
owns account refresh tokens, native-backend allowlisting, and the client-side
onboarding protocol. It explicitly leaves the production server-side
principal-to-host route unfinished. None owns requester-supplied API-key
lifecycle across principal, host, universe, provider, assignment generation,
and execution.

This change therefore introduces `provider-credential-custody` and modifies
only the shipped capabilities whose current truth conflicts with it.

Alternative rejected: fold everything into
`constrain-set-engine-provider-authority`. Merged PR #1784 intentionally owns
provider destinations, `setup_required`, assignment admission, and invocation
shape—not credential-source isolation—and its provider-routing delta would
collide.

### 2. `write_graph` is the preferred successor candidate, not landed behavior

Current `write_graph(target="universe")` creates a universe and its
`changes_json` argument is documented and dispatched only for
`target="branch"`. It does not currently configure a provider. The preferred
successor candidate after the hidden legacy `universe` tool retires is a typed,
non-secret provider-binding mutation on `write_graph(target="universe")`, but
that is new behavior, not an already canonical sub-operation.

The owners of `openspec/changes/universe-creation/`,
`openspec/changes/retire-legacy-live-mcp-tools/`, and the live interface must
accept and specify the operation name, authorization, idempotency, create
versus update discrimination, response envelope, and malformed/unknown-op
behavior before landing it. This custody change does not modify
`universe_server.py`, does not create a new public verb, and does not treat the
candidate as accepted dependency state.

Alternative rejected: preserve or rename `set_engine` as another public tool.
Engine assignment is universe state mutation and composes through
`write_graph`.

### 3. Secret and binding references are distinct

The native store creates a random, versioned `native_secret_ref` in a
provider-secret namespace. It never overwrites or reuses #1736's predictable
account-token reference.

The control plane's versioned `provider_authority_bindings` map stores one
provider entry whose custody-side opaque-reference field is named
`credential_binding_ref`. That reference is:

- opaque, random, non-secret, and not a key-derivation input;
- bound to canonical provider, universe, credential-owner principal,
  stable `host_principal_id`, scope, provider-assignment generation, issue
  time, and expiry;
- a locator only, never a bearer grant.

The binding does not freeze an issue-time `host_principal_generation`.
Instead, each sensitive consumer presents fresh device/host proof whose
generation must equal the current active generation read from trusted
host-principal state. In-place key rotation keeps the same
`host_principal_id`, fences old proofs/sessions, and allows a fresh proof at
the new generation to keep using the binding. Revocation, expiry, and
lost-key recovery terminate the old principal; recovery creates a new
principal ID and therefore requires a new provider binding.
An active principal renewed before expiry keeps the same ID/generation and may
continue after fresh proof and extended-expiry checks. Missing that renewal
window makes expiry terminal; a later host principal requires fresh provider
enrollment rather than reactivating the expired binding.

The unversioned `.credential-vault.json` SHALL NOT be authoritative binding
state; it may contain display-only non-secret metadata that is safe to lose,
but provider launch never trusts it. Only the bound host maps
`credential_binding_ref` to `native_secret_ref`. Neither identifier is copied
to another host or principal.

Enrollment is a two-store commit-token protocol, not a cross-store CAS. Every
enrollment, rotation, retirement, and compare-delete operation first acquires
exclusive `ProviderAssignmentAdmission` for the canonical universe and
validates expected assignment generation plus affected provider-binding
digest. Enrollment, secret use, binding creation/rotation, and cutover also
read trusted host-principal state and require the exact principal to remain
active and the presented host proof generation to equal the current trusted
`host_principal_generation` immediately before protected work starts or
commits. Revoked, expired, lost-key-recovered, or stale-generation host
consumers fail closed; same-ID in-place rotation resumes only with fresh
current-generation proof. Terminal-principal retirement/compare-delete may
instead use same-subject step-up recovery or a separately authorized internal
exact-tuple cleanup consumer, under the same exclusive assignment admission,
only after trusted host-principal state proves the exact bound principal
revoked, expired, or recovery-superseded. Active, unknown,
mismatched-subject, or unprovably terminal state refuses cleanup. The admitted
path can only tombstone/delete and can never dereference, transfer, rebind, or
launch with the secret. Only then
may it acquire the narrower local pending-index/keyring
locks. Reverse acquisition and untracked reentrancy fail loud. A local
pending-index/keyring lock is released before any control-plane CAS or other
operation that could reacquire admission; the outer assignment admission
remains the serialization owner across the protocol.

1. under exclusive assignment admission, the local host atomically records a
   random `native_secret_ref`, enrollment
   id, and one-use commit token as `pending` in a bounded local pending index,
   writes the exact secret under that native reference, and releases the
   narrower local locks;
2. the control plane compare-and-swaps a pending binding carrying that
   enrollment id and commit-token digest through
   `ProviderAssignmentAdmission` into the expected provider-assignment
   generation only after fresh presented host proof matches current active
   host-principal generation;
3. the host reads that exact committed binding and records a local
   acknowledgement before marking the mapping `committed`;
4. a bounded reconciler may tombstone a timed-out pending local reference only
   after an authenticated control-plane read finds no matching binding;
5. because those stores share no transaction, reconciliation re-reads after
   local tombstone; if a late binding appeared, it holds provider launch and
   compare-clears only that exact control-plane enrollment id, token digest,
   and provider-assignment generation after rechecking current
   host-principal status/generation, or under the narrow terminal-principal
   step-up/internal cleanup authority;
6. the split-brain path never restores the deleted local secret and requires a
   fresh enrollment.

Native keyrings expose exact-reference set/get/delete, not portable
enumeration. The bounded local pending index is therefore the only
enumeration source for reconciliation. A native reference absent from that
index is unreachable, not discoverable by scanning the keyring. The index
contains no secret material and is updated atomically with the local lifecycle
state.

### 4. The owning authority path depends on fulfillment class

Merged PR #1784 (`constrain-set-engine-provider-authority`, Opus-approved head
`abdca5fe`, merge `620fed5a`) owns requester-owned local assignment,
`ProviderAssignmentAdmission`, and its shared-reader
`ProviderInvocation -> ProviderLaunchHandle` launch barrier. The shipped D0
path is fake-only/production-denied and therefore cannot be required as
ordinary requester-provider authority. Accepted-market remote execution must
instead use the owner-accepted production B2/distributed-execution authority
contract. The shipped `runner/v1`
`credential_grant_ref` is only a nine-field wire carrier; it lacks universe,
provider, host, and assignment-generation fields, and `ExecutionGrantV1` does
not contain a credential reference. `SandboxRunner` therefore cannot validate
the full custody tuple and this change does not pretend that it can.

Consuming D0's landed `Verified[T]` and execution-record types is type reuse,
not a D0 authority grant. It does not create a production D0 route, make D0
live-acceptable, or upgrade an opaque locator into provider authority.

For requester-owned local execution, the implementable seam is
`ProviderExecutor.start()`. It receives the frozen `ProviderInvocation` and
exact `credential_binding_ref`, validates their complete tuple under shared
`ProviderAssignmentAdmission`, and crosses the launch barrier before
constructing a provider child or obtaining an outbound proxy. No separate
adapter independently validates that tuple. For accepted-market
remote execution, the binding may compose only with the future owner-accepted
production B2 authority envelope. If either path later routes through
`runner/v1`, its owning adapter may copy an already-validated opaque reference
into `credential_grant_ref`; the runner field remains a locator, never a bearer
grant. Fail-closed is absence of the authority required by the selected
fulfillment class, not an empty capability ceiling or fake-only D0 record.

### 5. Secret resolution is transport-owned behind one launch boundary

For requester-owned CLI/local/in-process invocation,
`ProviderExecutor.start()` validates destination, credential-owner principal
from verified request/assignment authority, stable `host_principal_id`, fresh
presented host proof at the current active `host_principal_generation`,
provider-assignment generation, and
the provider-authority owner's shared
`ProviderAssignmentAdmission`. Only then may the selected executor resolve the
binding once inside provider child/request memory. The secret is never placed
in argv, persisted child config, logs, or server state.

For requester-owned remote HTTP, `ProviderExecutor.start()` obtains only the
non-serializable, per-universe grant-bound credential-blind proxy handle owned
by `outbound-boundary-layer`. The proxy alone resolves the credential
reference and performs network I/O; provider/executor code sends only a
redacted request through it. For this `llm_api_key` path, the proxy and native
secret store must run on the same attested requester-controlled host, and the
proxy resolves through `provider-credential-custody`'s native reference—not
through the retiring `credential-vault` `llm_api_key` record. The active
`outbound-boundary-layer` owner must accept that narrow custody-source
adaptation before runtime; retained subscription/VCS/social custody behavior
remains unchanged. Missing, expired, revoked, ambiguous, or wrong-universe
outbound grant/proxy authority holds before provider, credential, or network
access. This change creates no second outbound ledger, grant, proxy, secret
path, or ambient fallback.

The keyless `ollama-local` supplement is outside this `llm_api_key` custody
rule: it has no `credential_binding_ref`, and the planned
`activate-requester-host-engines` owner selects transport solely from its
attested requester endpoint and executor-host identity.

Accepted-market remote execution SHALL use its separately accepted B2
authority and SHALL NOT receive a requester-local secret merely because
`runner/v1` carries a locator.

For every requester-owned launch, `ProviderInvocation` carries only the opaque
binding reference/digest plus credential/auth provenance. It never carries
resolved secret material. Only executor-local `start()` may resolve material
for CLI/local/in-process transport; remote HTTP delegates resolution and I/O
only to the outbound proxy.

Background, resumed, retried, and scheduled launches derive the credential
owner from that persisted verified authority. They never use an ambient HTTP
subject, daemon process identity, current workspace member, or newly changed
universe ACL as the credential principal.

Rotation/removal fences new launches. In-flight launches either drain under the
captured generation or are cancelled by the explicit compromise policy before
the old native reference is deleted.

Host-principal rotation, revocation, expiry, or lost-key recovery is a separate
fence from provider-assignment rotation. `ProviderExecutor.start()` rechecks
the presented host-proof generation against current trusted host-principal
generation and independently rechecks provider-assignment generation before
irreversible launch; every custody/assignment commit repeats both checks. A
superseded proof or revoked principal cannot dereference a new secret, start a
new launch, or commit an in-flight result/cutover; the transport is cancelled
where possible and otherwise its result is discarded and recorded held.
Same-ID in-place rotation does not orphan the native reference: fresh
current-generation proof may continue using the binding. Lost-key recovery
creates a new principal ID, permanently fences the old binding, and requires
fresh enrollment while the old reference follows the safe rotation/tombstone
path.

### 6. Retirement is a monotonic saga

Each legacy `llm_api_key` slot is keyed by immutable migration id, expected
record digest, assignment generation, and the environment-variable slot plus
stored occurrence derived from exactly `anthropic`, `claude`, `claude-code`,
`openai`, `codex`, `gemini`, `google`, `groq`, `xai`, or `grok`. Missing or
unsupported service is terminal `held_ambiguous`. The exact states are:

`discovered -> held -> notified -> replacement_pending ->
replacement_verified -> rotation_required -> revoked_upstream ->
cutover_committed -> artifacts_deleted -> record_deleted -> closed`.

`held_ambiguous` and `closed_without_replacement` are explicit terminal held
outcomes. `failed_held` is not a backward state transition; it is a retryable
overlay carrying the last committed state and a sanitized failure class, and a
retry may attempt only that committed state's next legal edge. No state other
than `closed` with a current replacement assignment permits provider launch.

Invariants:

- inventory records metadata/field names/digest/byte length only and never
  decodes secret fields;
- no plaintext export or silent migration exists;
- a replacement local reference remains `pending` until exact local provider
  authentication succeeds;
- owner notification, replacement verification, and provider-side
  rotation/revocation precede assignment cutover and deletion;
- `cutover_committed` is a compare-and-swap of the provider assignment binding and
  generation; failure remains held and cannot delete the legacy record;
- terminal assignment vocabulary remains provider-owner-defined:
  `closed` publishes `engine_source=requester_local` through the atomic
  post-custody writer and is `ready` only when the binding plus all live-role
  coverage are complete, otherwise `held + []`;
  `closed_without_replacement` retains `engine_source=byo_api_key` with
  `engine_assignment_state=failed` and `allowed_providers=[]`; and
  `held_ambiguous` likewise remains `byo_api_key`, `failed + []`, while
  retaining the unclassifiable source record;
- an owner who intentionally declines replacement may reach
  `closed_without_replacement` only through `discovered -> held -> notified ->
  rotation_required -> revoked_upstream -> artifacts_deleted ->
  record_deleted -> closed_without_replacement`, and the assignment remains
  permanently held/setup-required;
- every transition holds the exclusive writer from
  `ProviderAssignmentAdmission`, owned by
  `constrain-set-engine-provider-authority`;
- delete is compare-and-delete against the inventoried digest;
- stale generation re-inventories rather than acting;
- idempotency keys are scoped to universe, slot, and generation;
- every materialized artifact is inventoried by canonical path, content or
  metadata digest, credential slot, owner, generation, and reference count;
- a shared, mixed-owner, mixed-generation, changed, or ambiguous artifact is
  refused and keeps the saga held—whole provider homes are never recursively
  deleted merely from a service name;
- only exact owned, digest-matched, single-reference artifacts are deleted
  before the source record;
- malformed or unclassifiable existing vault state blocks every ordinary
  write/replacement and global reset before mutation;
- `openspec/changes/test-identity-and-reset/` must route any legacy
  `llm_api_key` cleanup through this saga and cannot clear the vault directly;
- retries converge to one monotonic state and never resurrect raw material;
- rollback rebinds a new local generation or remains held.

### 7. Administration is not credential authority

A universe admin may configure non-credential universe state but may not
attach an arbitrary binding, resolve, rotate, delete, replace, or use a binding
owned by another principal. Every binding mutation verifies the binding owner
against the persisted request/assignment principal. A shared universe without
an exact persisted credential-owner principal/host/provider/generation match
remains held.

Organization-owned pooling and delegation remain deferred until an accepted
organization identity/authority model exists.

### 8. Prompt prevention is load-bearing

The current first-contact prompt invites a founder to supply an API key.
TinyAssets can stop eliciting it, remove supported secret fields, reject a
supplied canary without echo before application mutation, and protect every
TinyAssets-owned sink. It cannot redact bytes already sent to a client-owned
upstream chatbot transcript. Before runtime, the
`openspec/changes/retire-legacy-live-mcp-tools/` live-MCP owner and production
gateway/deployment owner must inventory the gateway/access logs, MCP middleware
and structured request capture, traces, ledgers, exceptions, and crash
artifacts and assign an owner plus canary proof for each TinyAssets-controlled
sink.

`tinyassets/api/prompts.py` and its packaged mirror require a future exact
claim; they are not part of this review/spec-only write set.

### 9. Concurrency proof is custody-specific

Acceptance requires a named local multi-process race covering pending
enrollment, commit-token publication, local acknowledgement, local tombstone,
late control-plane binding, split-brain compare-clear, rotate, dereference,
revoke, delete, retry, stale-host expiry, and the provider-authority launch
barrier.
It must prove a unique usable binding, coherent generations, no torn secret
read, and no orphaned or resurrected active native reference.

The full-platform §14 Track J scenarios remain a separate explicit obligation;
they exercise subscribers, bidding, read storms, and presence rather than
credential dereference.

### 10. Engine OS consumes an opaque credential requirement

The Engine OS logical `ExecutionRequirement` carries a
`credential_requirement_ref` plus digest and an independently owned
`egress_requirement_ref` plus digest. This change owns the credential pair for
requester-supplied provider API-key custody. The engine caller, graph, provider,
backend, and adapter treat it as opaque; none may supply the resolved object,
replace its digest, or treat a `credential_binding_ref` as a bearer grant or as
the requirement itself.

For `source_exec/runner_source_exec`, an admissible owner-published pairing
must resolve to no credential available to the workload. For
`inference_only/provider_cli`, the resolved credential requirement must prove
that model-controlled work receives no raw key, token, auth file,
`native_secret_ref`, `credential_binding_ref`, or other recoverable material.
CLI/local/in-process resolution remains inside the authorized executor launch
boundary. Remote HTTP remains credential-blind and may resolve the provider
reference only inside the outbound owner's non-serializable proxy on the same
attested requester-controlled host as native custody, never through a legacy
vault `llm_api_key` record.

The credential and outbound owners must publish an exact compatible pairing
before either profile is admissible. This decision accepts the opaque
reference/digest handoff but does not publish a complete credential taxonomy or
compatibility matrix, does not change retained subscription/VCS/social custody,
and does not answer the still-open classification of provider model calls under
outbound caps, request-lineage idempotency, reconciliation, receipts, or batch
holds. Missing or mismatched owner bindings remain held without fallback.

## Risks / Trade-offs

- **Legacy users lose immediate raw-key setup** → return existing typed
  `setup_required/held` guidance to requester-local enrollment; never preserve
  the unsafe path as a shim.
- **Unowned legacy secret cannot be safely revoked** → remain terminally held;
  do not export or delete a still-live upstream key.
- **Control-plane binding is mistaken for authority** → require exact
  provider-authority local invocation authority or the separately accepted production B2
  authority for market execution; fake-only D0 never upgrades a locator.
- **Rotation races an in-flight launch** → use shared
  `ProviderAssignmentAdmission` through the launch barrier, then drain or
  explicitly cancel before native deletion.
- **Native store unavailable** → fail closed with no file/env fallback.
- **Provider revocation cannot be machine-verified** → accept only an honestly
  labeled owner attestation and remain held until recorded.
- **Existing full-platform load gate is misreported** → name the local proof
  accurately and retain the real Track J deferral.

## Migration Plan

1. Consume merged PR #1784 (`constrain-set-engine-provider-authority`,
   Opus-approved head `abdca5fe`, merge `620fed5a`) for requester-owned local assignment
   admission and launch; obtain distributed-execution/B2 owner acceptance for
   accepted-market production authority; keep D0 fake-only/production-denied
   and the runner opaque.
2. Preserve PR #1736's account-token lifecycle, native-backend allowlist, and
   client-side `OriginClient` protocol unchanged. Land
   `bind-host-principal-to-account` as the authenticated server-side route
   that derives the account principal from verified identity, issues a stable
   server-attested host principal distinct from per-session host-pool rows,
   supports idempotent re-registration/revocation, and exposes the
   authenticated binding read needed by reconciliation. Keep the disjoint
   provider-secret namespace, pending index, commit-token protocol,
   split-brain compare-clear, tombstones, deletion, and rotation in this
   change.
3. Have `openspec/changes/universe-creation/`,
   `openspec/changes/retire-legacy-live-mcp-tools/`, and the live-interface
   owner accept and land a canonical non-secret setup successor—preferably the
   typed `write_graph(target=universe)` candidate—before hidden-tool retirement.
4. Remove raw-key elicitation and supported input fields; reject any supplied
   `llm_api_key` canary at ingress before mutation or durable diagnostic sinks
   and return the existing `setup_required/held` contract.
5. Create a pending local replacement and commit token, publish its binding
   through the provider-authority assignment CAS, acknowledge the exact token locally,
   reconcile any late-binding/local-tombstone split brain to held, and verify
   exact provider authentication without reading legacy secret bytes.
6. Hold legacy assignments, notify owners, require upstream
   rotation/revocation, commit the replacement cutover, then delete only
   exact-owned artifacts and the digest-matched source record.
7. Enable launch-time local dereference only in `ProviderExecutor.start()`,
   under shared `ProviderAssignmentAdmission`, for
   requester-owned execution. Keep accepted-market remote execution blocked
   until its production B2 authority owner accepts the composition.
8. Complete local concurrency, parity, exact-seven `/mcp`, rendered-chatbot,
   and post-fix organic-use gates.

Rollback never re-enables raw deposits or uses legacy records. It disables new
bindings and leaves affected universes held until a corrected local generation
is enrolled.

## Open Questions

- Will the universe/live-interface owners accept the preferred
  `write_graph(target=universe)` successor candidate, or name another existing
  canonical handle before hidden-tool unregistration?
- Will distributed-execution/B2 owners accept the remaining
  accepted-market side of the requester-local/accepted-market authority split
  while D0 remains fake-only/production-denied and the runner stays opaque?
- Will `outbound-boundary-layer` classify requester-owned provider model calls
  as quota-consuming connection actions under its unprompted-action cap? It
  must either define confirmation, request-lineage idempotency,
  journal-before-fire, ambiguous-outcome reconciliation, terminal receipt,
  and batch-hold composition for this non-goal/schedule-shaped call class, or
  explicitly carve the class out without creating a second outbound
  authority/receipt system.
- What provider-revocation attestations are machine-verifiable per provider,
  and which require explicitly labeled owner attestation?
- Which later capability will own organization-pooled credential delegation?

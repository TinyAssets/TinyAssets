## Context

This design is grounded against `origin/main` commit
`7b451b2c98abb9b411d35b32def96a319d721594` (2026-08-10). The checked-out
`main` was 689 commits behind that revision and contained unrelated team work,
so source claims below come from read-only `git show origin/main:<path>` reads.
The local `byo-llm-connect-flow` proposal and tasks are the change inputs.

The live 2026-08-10 diagnosis establishes the product failure: a private agent
binding may contain an opaque `provider_ref`, but the only real persisted
provider binding is scoped to GitHub repository-spec delivery. Substituting its
identifier into the agent binding grants no conversational authority. No
Anthropic/OpenAI connection is present, and the only public mint operation is
the automation-shaped `write_graph target=automation operation=bind_provider`.

### Objects and authority today

#### `ProviderWorkBinding` (the object exposed live as a provider binding)

`tinyassets/provider_work_authority.py` defines the durable binding used by
cloud automation. Its exact stored fields are:

- integrity/lifecycle: `schema_version`, `binding_id`, `generation`,
  `binding_digest`, `state`, `revocation_generation`, `created_at`, and
  `updated_at`;
- subject/destination: `owner_user_id`, `universe_id`, and `provider`;
- credential/assignment evidence: `credential_reference_digest`,
  `assignment_generation`, and `assignment_digest`;
- authority ceilings: `allowed_operations`, `allowed_roles`,
  `max_invocations`, `max_tokens`, `max_cost_microunits`, and `expires_at`.

There is no stored field literally named `scope`. The binding's effective scope
is the exact `(owner_user_id, universe_id, provider)` root plus its assignment
generation/digest, operation/role sets, budgets, expiry, and current state.
`binding_id` is deterministic from the root; the record digest covers every
field except itself. The SQLite store repeats the root, state, generation, and
digest as indexed columns and rejects a record whose JSON and columns disagree.

The binding is not a bearer capability. `ProviderWorkBindingService.issue()`
accepts only a `ProviderWorkBindingRoot`, asks an injected server-owned resolver
for a complete `ProviderWorkBindingSeed`, checks that the seed's owner,
universe, and provider exactly match the root, and only then persists it.
`cloud_automations` follows that pattern: the request may name `provider`, but
owner comes from the authenticated actor and all credential, operation, role,
assignment, budget, expiry, and binding fields come from the server enrollment
resolver. The currently deployed enrollment is automation-shaped:
`allowed_operations=("repository_spec_delivery",)`, role `writer`, and a
bounded invocation cap. It cannot authorize `converse`.

#### `agent_binding`

`tinyassets/custom_agents.py` stores:

- `agent_binding_id`, `universe_id`, and `agent_definition_id`;
- arbitrary validated `configuration_json`;
- `revision`, `status` (today constrained to `configured`), `created_by`,
  `updated_by`, `created_at`, and `updated_at`.

`provider_ref` is not a typed database column or an authority object. It is an
opaque value inside the private `configuration` JSON. The generic create/update
path checks JSON shape, secret-looking content, private operational fields,
universe ACL, authentication, and revision CAS, but it does not resolve or
validate `provider_ref`; there is no `provider_ref` symbol in current source.
Therefore a placeholder or string-swapped `pwb_...` reference is only caller
intent. It grants nothing and must never be promoted into authority by trusting
the string.

#### `connections`

`read_graph target=connections` currently delegates to
`tinyassets/api/cloud_connections.py`. It lists only current requester-owned
WorkOS Pipes GitHub grants for the exact authenticated actor and universe. Each
projection contains `connection_id`, `grant_id`, `provider`, `destination`,
`connection_class`, `scopes`, `action_cap`, and `status`. The authoritative
connection and grant records live in `ConnectionLedger`; their owner and
universe are rechecked server-side. There is no LLM connection class or
Anthropic/OpenAI entry today. Slice 1 must not disguise a vault record as a
`connections` row; provider connection objects remain Slice 2.

#### `engine_source`, preferences, and `allowed_providers`

`UniverseConfig` today has `preferred_writer`, `preferred_judge`,
`allowed_providers`, and `engine_source`. `engine_source` defaults to
`byo_api_key` and currently accepts `byo_api_key`, `self_hosted_endpoint`,
`market_rented`, and `host_daemon` through the hidden legacy `set_engine`
action. That action writes a preference but does not write
`allowed_providers`. There are no landed `engine_assignment_state`,
`engine_assignment_generation`, or `provider_authority_bindings` fields.

The router's current safety patch is narrower than the target authority model.
For an explicit requester `UniverseContext`, it uses a non-empty
`allowed_providers` list, or otherwise the universe's preferred writer/judge,
as a ceiling; if neither exists it raises `ProviderAuthorityHeldError` with
"Connect your provider" before provider access. A process-global/contextless
call may still use legacy platform routing. `providers/base.py` resolves an
explicit universe's CLI auth only from that universe's vault/materialized
credential directory, but its no-universe branch intentionally retains the
host environment, including host `CODEX_HOME` / Claude subscription state.
`credential_vault.resolve_codex_home(None)` itself returns `None`; the ambient
borrow is the no-universe provider-environment branch, not that helper.

`allowed_providers`, `preferred_writer`, `engine_source`, an ACL, an actor ID,
an agent `provider_ref`, or a serialized binding are not request authority.
The keystone change specifies `ProviderRequestCapability` as the request-scoped
authority derived from authenticated server-held state, but
`ProviderRequestCapability` has no implementation on current `origin/main`.
The existing `ProviderInvocationCarrier` is a separate background/agent-runtime
carrier and is not a substitute.

### Stable terminology

| Term | Meaning | Not this |
|---|---|---|
| serving binding | Durable, inert provider ceiling for one requester, universe, and provider, permitted to support conversational turns | bearer token, credential, agent-binding string, automation grant |
| provider selection | `agent_binding.configuration.provider_ref`, resolved afresh as a candidate binding for the current binding revision | authority |
| request capability | Non-serializable, one-request capability minted by a trusted authenticated transport boundary and revoked at turn completion | actor string, ACL result, founder tier, request payload |
| engine assignment | Coherent server state containing source, state, generation, exact provider ceiling, and binding-map digests | provider preference alone |
| serving state | Durable intent that an exact agent-binding revision may receive turns | proof that a Slack socket or provider is healthy |

## Goals / Non-Goals

**Goals:**

- Mint one requester-owned, forgery-proof serving binding from an existing
  `llm_subscription` credential in the exact universe vault.
- Atomically publish the requester-local engine assignment and wire an exact
  agent-binding revision to the binding without accepting authority fields from
  the caller.
- Let a current authenticated MCP `converse` request, and the founder Slack
  path used by the live proof, carry request-local authority through the router
  to one exact provider launch.
- Add a controlled serving-state write so a connected binding is eligible to
  answer, with no dogfood-only code or hard-coded universe/binding IDs.
- Fail before credential, auth-health, quota, or provider access whenever any
  subject, scope, state, generation, role, operation, or request lease is wrong.

**Non-Goals:**

- Provider OAuth/device-flow or API-key ingress, LLM `connections` rows, and
  GitHub-project connection (Slice 2).
- Removing all legacy/host/writer-fleet routes (Slice 3), except that the new
  Slice-1 path itself may never fall back to one.
- Giving visitors or collaborators permission to spend a founder's provider.
  Slice 1 proves an authenticated owner/founder turn; broader audience-serving
  requires a separately bounded owner grant and spend policy.
- Treating an agent binding, connection projection, config preference, or
  provider binding identifier as a bearer capability.

## Decisions

### 1. Reuse `ProviderWorkBinding`; do not invent a second binding type

The serving binding uses the existing integrity-checked persistence and service
with this exact semantic shape:

- `owner_user_id`: current authenticated subject, re-derived at mint time;
- `universe_id`: exact resolved universe, checked against current admin
  ownership and the current agent binding;
- `provider`: canonical runtime name, initially `claude-code` or `codex`;
- `allowed_operations`: exactly `("converse",)`;
- `allowed_roles`: exactly `("writer",)`; both reply generation and the
  current post-reply learning extraction call use role `writer`;
- `credential_reference_digest`: digest of a server-owned opaque credential
  custody record, never a raw path/token hash supplied by the request;
- `assignment_generation` / `assignment_digest`: the coherent requester-local
  engine assignment published by the keystone admission owner;
- `state=active`, a bounded server-policy expiry, and server-policy ceilings
  for per-turn invocations/tokens/cost. The request capability narrows those
  ceilings for each turn; request payloads cannot widen them.

The binding remains universe-scoped, not agent-specific. The agent binding
chooses which current universe binding to use; at turn resolution the server
also freezes `agent_binding_id` and revision. A pointer swap across users or
universes fails the binding tuple. A swap between two serving bindings owned by
the same subject in the same universe is still checked against the current
server-published assignment map and provider ceiling, so an old or unassigned
binding fails. This avoids changing the established binding identity solely to
encode a selector.

Alternative rejected: add `chat_completion` to the existing automation grant.
That would let an automation enrollment silently become interactive spend
authority and would couple unrelated budgets/lifetimes.

Alternative rejected: trust `provider_ref` after checking its `pwb_` format.
The identifier is public, copyable, and explicitly non-authorizing.

### 2. One atomic connector operation mints and wires

Add a target-specific operation:

`write_graph target="agent_binding" operation="bind_serving_provider"`

Inputs are limited to `graph_id`, `agent_binding_id`, `expected_revision`, and
`payload_json={"provider":"claude-code"|"codex"}`. Owner, credential
reference, binding ID, operation/role sets, assignment fields, budgets, expiry,
and `provider_ref` are rejected if supplied, not ignored ambiguously.

The server performs under the keystone's per-universe exclusive
`ProviderAssignmentAdmission` writer:

1. Re-derive the authenticated subject and require a current `admin` ACL row.
2. Load the exact current agent binding; require `created_by` to equal that
   subject and `revision == expected_revision`.
3. Map `claude-code -> llm_subscription/service=claude` and
   `codex -> llm_subscription/service=codex`; require exactly one current,
   usable custody entry in that universe.
4. Resolve or create the server-owned opaque custody reference and its current
   generation/digest. Raw vault paths, `CODEX_HOME`, OAuth tokens, and auth JSON
   never enter the binding or response.
5. Quarantine the assignment as `pending + []`, mint/rebind the serving
   `ProviderWorkBinding` from a server resolver, then publish in one journaled
   commit: `engine_source=requester_local`, incremented
   `engine_assignment_generation`, `engine_assignment_state=ready`,
   `preferred_writer=<provider>`, `allowed_providers=[<provider>]`, and the
   matching non-secret `provider_authority_bindings` entry.
6. Replace `agent_binding.configuration.provider_ref` with the minted binding
   ID and increment the binding revision. On a provider switch, revoke the old
   serving binding before the new assignment becomes visible. On any partial
   failure, publish `failed + []`; never restore a wider prior ceiling.

The response contains only the redacted binding projection, new agent-binding
revision, assignment generation/state, and next action. It never returns the
credential reference itself, a filesystem path, auth-health internals, or
secret-derived exception text.

Generic `create_binding` / `update_binding` must stop accepting a direct
`provider_ref` mutation. Existing values remain readable for diagnosis and
migration, but new writes to that field go only through
`bind_serving_provider`. Otherwise the specialized validation would be
optional.

### 3. Vault presence is not yet sufficient custody evidence

The current vault is a universe-scoped JSON file and its LLM records have no
server-authored owner, credential ID, generation, or opaque reference. A plain
hash of a caller-shaped record or path would not fix that: it would be replayable
and would not give atomic replacement/revocation semantics.

Slice 1 therefore consumes the keystone/custody-owned opaque-reference seam. A
small server-owned custody index must bind an opaque credential ID and
generation to `(authenticated owner, universe, credential type, canonical
service)` and validate the current vault slot under assignment admission. For a
pre-existing vault record, adoption requires an existing server-held depositor
record created by the authenticated credential-connect/deposit boundary; an
admin or ACL collaborator cannot claim ownerless or another principal's
material. The custody digest covers both normalized record metadata and the
actual credential bytes, including path-backed `auth.json`, and is revalidated
at reservation immediately before launch. It does not trust `owner_user_id`,
credential ID, or generation found in vault JSON. Missing, duplicate,
unreadable, path-escaping,
symlinked, replaced-during-resolution, or auth-unusable records hold with no
artifact creation and no binding mutation.

This is not an optional hardening follow-up. Without that opaque custody record,
task 1.1 cannot truthfully claim requester-owned or generation-safe authority.

### Custody owner (decided)

The existing `credential_vault` capability is the custody owner: it already
validates, stores, rotates, and materializes universe-scoped
`llm_subscription` records for the provider processes that need the secret.
Slice 1 therefore extends that subsystem to adopt a validated vault record and
issue its opaque, generation-bound reference; the keystone/provider-authority
layer stores and revalidates only that reference and digest. This keeps secret
material and path validation in the subsystem that already owns them, while
preventing API or routing code from becoming a parallel credential custodian.

### 4. The router resolves a served turn from selection + capability + fresh state

For the canonical MCP `converse` path:

1. The keystone FastMCP middleware mints a `ProviderRequestCapability` from the
   exact authenticated `tools/call` message, claims it in the actual worker,
   and binds principal/session/request/tool/lease.
2. `converse` resolves the target universe and the caller's one current
   serving agent binding (or an explicit server-routed binding), freezes its ID
   and revision, and calls universe intelligence with operation `converse`.
3. Universe intelligence constructs the explicit `UniverseContext` from that
   universe and passes the sealed internal carrier through `call_provider`,
   `call_sync`, retry closure, and provider invocation. It does not rely on a
   copied `ContextVar`; the router thread pool does not propagate one.
4. Immediately before provider access, the sink reloads the agent binding,
   assignment, serving binding, request lease, and custody reference. It
   requires: exact current principal; target universe; agent binding/revision;
   `provider_ref`; active binding owner/universe/provider/generation/digest;
   operation `converse`; role `writer`; exact assignment generation/digest and
   `allowed_providers` membership; non-expired budgets; and current credential
   custody.
5. The sink copies current credential bytes into a unique per-launch directory,
   derives the custody generation/digest from those copied bytes, and gives the
   adapter only that snapshot. Direct source-path rotation cannot change the
   in-flight launch; the next admission revalidates the source. Cleanup is
   best-effort on every result/error path.
6. The router uses only the binding's provider. Preference, fallback chains,
   environment pins, process-global config, auth-health fallback, and another
   universe's binding cannot add a destination. The executor materializes only
   that universe's matching vault auth after all checks.

The Slack live proof needs the same model, not a side door. Current Slack
Socket Mode evidence and `FounderGrant` are sealed and revalidate the current
admin ACL, agent binding, revision, mapping generation, and universe, but they
do not mint provider request authority. Extend the keystone's one capability
registry with a second trusted transport adapter for an admitted live app
event, using a distinct fixed mechanism/issuer pair and an event/worker lease.
It must still produce the same `ProviderRequestCapability` and internal carrier;
do not create a `SlackProviderCapability`. Actor strings, `tier=FOUNDER`, HMAC
payload fields, or a serialized founder grant remain non-authority. The
keystone spec must approve this additional mint domain before code is written.

### 5. Serving state is a first-class server write, not an environment claim

Today an agent binding can only be `configured`; there is no connector write
that makes it serving. Separately, the Slack transport uses the host-only static
`TINYASSETS_SLACK_UNIVERSES` list. Writing an arbitrary
`configuration.serving=true` would not start a socket and would be a false
success.

Add:

`write_graph target="agent_binding" operation="set_serving"`

It accepts `graph_id`, `agent_binding_id`, `expected_revision`, and only
`payload_json={"enabled":true|false}`. The server re-derives the authenticated
admin owner, current binding/revision, active serving binding, ready assignment,
and required chat-surface credential/routing before changing the binding's
server-owned state between `configured` and `serving`. Enabling fails closed if
any prerequisite is missing; disabling is always allowed to the current owner
and revokes new-turn admission immediately.

For generic 24/7 Slack serving, the Slack worker must consume a signed internal
daemon projection of current `serving` bindings instead of treating
`TINYASSETS_SLACK_UNIVERSES` as the durable universe list. The projection
contains only universe/binding/revision/connection identifiers; the existing
credential endpoint continues to vend only the socket credential needed by the
transport. Worker add/remove/restart reconciliation must be idempotent and must
not broaden provider authority. `serving` means eligible; health remains a
separate observed fact.

If Slice 1 limits itself to MCP `converse`, this worker reconciliation can be a
follow-up, but then task 1.3 and the generic Slack proof are not complete. The
existing u-tiny environment entry may support a diagnostic proof; it cannot be
used as evidence that the generic serving path exists.

### 6. Composition with `constrain-set-engine-provider-authority`

Slice 1 consumes, without redefining:

- `ProviderRequestCapability`, its message/worker lease registry, explicit
  internal carrier, and revocation semantics (keystone task 5.1);
- the canonical requester-local resolver and strict service/writer mapping
  (5.3);
- assignment state/generation, journaled deny-all publication, and
  `ProviderAssignmentAdmission` lock order (5.4-5.5);
- sink validation, provider-layer propagation, `ProviderInvocation`, and sole
  executor launch boundary (7.1-7.4);
- direct `ProviderAuthorityHeldError` mapping for `converse` (5.2).

Slice 1 adds:

- a vault-backed serving-binding resolver at the custody seam;
- operation `converse` / role `writer` binding policy;
- atomic `agent_binding.provider_ref` wiring and direct-pointer-write refusal;
- connector operations `bind_serving_provider` and `set_serving`;
- a live-app-event mint adapter using the keystone's capability type and lease
  registry; and
- dynamic host transport reconciliation for generic Slack serving.

The following cannot be implemented correctly without advancing the keystone:

- task 1.1 cannot mint a forgery-proof binding until assignment admission and
  opaque custody generation/digest exist;
- task 1.2 cannot safely publish config + binding + pointer across files/stores
  without the keystone transaction/journal owner;
- a served MCP or Slack turn cannot dereference the binding until request
  capability propagation and sink validation land;
- Claude requester-local readiness conflicts with the keystone's current
  role-completeness mapping: `claude-code` is absent from current judge/extract
  chains, so Anthropic alone is specified as held. Either advance the keystone
  role inventory to prove Claude can cover every live role, or explicitly keep
  Anthropic held. Slice 1 must not silently bypass that invariant merely because
  `converse` currently asks only for `writer`.

Until all of those pieces are active for the routed universe, fail closed with
the typed setup-required envelope. In particular: no binding on plain vault
presence; no caller-supplied binding fields; no cross-owner/universe/provider
reference; no stale/revoked/expired/generation-mismatched state; no direct
agent-binding pointer write; no request capability outside its live lease; no
fallback to another provider, host config, `CODEX_HOME`, Claude token, local
model, API-key provider, or platform subscription; and no "serving" success
when only intent was stored.

### 7. Implementation plan by Slice-1 task

#### 1.1 Mint a requester-owned serving binding

Files/functions:

- `tinyassets/credential_vault.py`: add a secret-free, exact-service lookup
  used only by the trusted custody resolver; preserve existing materialization
  containment and never return secrets to API code.
- new `tinyassets/provider_serving_binding.py`: own canonical provider/service
  mapping, the vault-backed `ProviderWorkBindingResolver`, fixed
  `converse`/`writer` policy, custody-reference validation, and redacted
  projection. Reuse `ProviderWorkBindingService`; do not duplicate its model or
  store.
- keystone-owned assignment/custody modules (as resolved when tasks 5.3-5.5
  land): add the opaque custody record and transactionally resolved seed.
- `tinyassets/config.py`: add and strictly validate the keystone fields
  `engine_assignment_state`, `engine_assignment_generation`, and
  `provider_authority_bindings`; `allowed_providers=[]` is deny-all and
  non-empty ready state is replacement-only.

Acceptance: provider is the only caller selector; every authority/budget field
is server-derived; missing/ambiguous/unusable custody yields no binding and no
mutation; replay returns the same current binding; replacement advances
generation and invalidates the old digest.

#### 1.2 Wire the agent binding

Files/functions:

- `tinyassets/custom_agents.py`: make `provider_ref` reserved for the
  specialized writer; add an internal CAS that updates only the current
  binding's server-resolved provider reference and revision.
- `tinyassets/api/custom_agents.py`: implement
  `bind_serving_provider`; require authenticated current admin/creator and use
  the assignment admission service. Return redacted state only.
- `tinyassets/universe_server.py`: document and dispatch the new target-specific
  operation under the existing `write_graph` handle; add no eighth tool.
- `tinyassets/api/universe.py` and `tinyassets/config.py`: replace legacy
  preference-only publication with the keystone requester-local assignment for
  this operation; do not route through legacy `set_engine`.

Acceptance: placeholder, automation-only, wrong-role, wrong-operation,
cross-owner, cross-universe, stale, revoked, expired, or unassigned references
are rejected. Config, provider binding, and agent pointer expose either one
coherent generation or deny-all recovery, never a half-wired ready state.

#### 1.3 Controlled serving path

Files/functions:

- `tinyassets/custom_agents.py` plus its SQLite schema migration: add the
  server-owned `serving` state and revision-CAS transition; keep status
  projections explicit.
- `tinyassets/api/custom_agents.py` and `tinyassets/universe_server.py`: add
  `agent_binding/set_serving` with the prerequisite checks above.
- `tinyassets/api/chat_surface.py`, `tinyassets/app_channel_routing.py`, and
  `tinyassets/app_ingress.py`: resolve only a current serving binding and carry
  exact binding/revision into the turn.
- `tinyassets/app_ingress_http.py` and `tinyassets/slack_agent_worker.py`: add a
  signed internal serving-assignment projection and idempotent socket
  reconciliation so static `TINYASSETS_SLACK_UNIVERSES` is not the generic
  authority source.

Acceptance: `enabled=true` with no active serving binding or ready assignment
holds; disable blocks the next turn; a stale worker projection cannot keep a
revoked binding serving; intent is never reported as observed health.

#### 1.4 Correct live drift through the generic operation

No dogfood migration branch or hard-coded IDs belongs in source. After the new
path deploys, the authenticated u-tiny owner invokes
`bind_serving_provider` on
`agent_binding_01kz0k6mwe61a0ph60a2hzp01x` using its current revision and the
provider whose subscription custody is present, then invokes `set_serving`.
The operation replaces the placeholder/mis-scoped pointer and revokes any old
serving assignment. The GitHub `repository_spec_delivery` binding remains
unchanged for automation and cannot satisfy the new prerequisite.

Acceptance: read-back shows an active `converse`/`writer` binding owned by the
authenticated requester, exact agent `provider_ref`, matching assignment
generation/digest, and serving state; no code contains the u-tiny or binding
identifier.

#### 1.5 Tests and independent review

Files:

- new focused tests for serving resolver/service, agent-binding operations,
  assignment transaction/recovery, MCP request capability, Slack request
  capability adapter, router sink, ambient credential isolation, serving-state
  reconciliation, and concurrent bind/turn/disable races;
- existing provider, credential-vault, custom-agent, app-ingress, router,
  converse, and package-mirror suites;
- packaged Claude-plugin mirror rebuilt from canonical source.

Required negative cases include caller-supplied owner/credential/digest/
operations/roles/budgets; a genuine binding ID copied across owner, universe,
agent revision, assignment generation, or operation; generic `update_binding`
pointer injection; ACL `write` without current admin/creator ownership; stale or
replayed request lease; copied context/thread; post-response provider work;
vault replacement during mint/launch; `UniverseContext(None)`; process-global
`CODEX_HOME`/Claude token; pin/fallback-chain escape; and mixed concurrent
universes. The load proof must show interleaved turns never cross principal,
universe, binding, credential, or provider. Run focused pytest, full pytest,
Ruff, mirror parity, OpenSpec validation, and dual-family security review before
rollout.

#### 1.6 Live proof

Deploy through the normal immutable-image path and verify the production SHA
contains the implementation. Run the public MCP canary and exact-handle
assertion, then a rendered authenticated chatbot `converse` showing the user's
own connected binding answers. Enable u-tiny through the same generic
operations and send a real founder Slack turn; capture the reply, provider
binding/assignment redacted evidence, request-capability trace, and durable
memory advance/recall in `output/user_sim_session.md` plus screenshot/trace.
Revoke or disable and prove the next turn holds before credential access. Check
fresh post-fix real-user evidence; if none exists, leave the required watch item
instead of claiming organic clean use.

## Risks / Trade-offs

- **Binding ID forgery or pointer substitution** -> Treat every ID as a lookup
  hint; reload and validate the full owner/universe/provider/operation/role/
  assignment tuple at the sink.
- **Caller-authored scope escalation** -> Reject authority-shaped fields on the
  public operation and make generic `provider_ref` writes impossible.
- **ACL overreach** -> Require current authenticated admin plus binding creator
  for Slice 1; a generic collaborator-spend policy is separate.
- **Vault presence mistaken for ownership** -> Require a server-owned opaque
  custody record and generation; never infer ownership from path or JSON fields.
- **TOCTOU across vault/config/SQLite** -> Use assignment admission for
  cooperative writes, journaled quarantine, exact generation/digest checks,
  and per-launch credential snapshots whose custody is derived from copied
  bytes; direct path writes cannot alter an admitted launch.
- **Ambient host credential leak** -> An armed served turn always carries an
  explicit universe and exact provider; no-universe/contextless resolution,
  host `CODEX_HOME`, Claude token, pin, and fallback chains are ineligible.
- **Request capability replay** -> Bind to exact transport message/event,
  worker, tool/operation, principal, universe, agent revision, and lease; revoke
  synchronously on every return/error path.
- **Slack identity confused with TinyAssets authority** -> Mint only after
  sealed event admission plus current principal mapping/admin/binding
  revalidation; actor IDs and founder labels alone grant nothing.
- **Claude appears connected but remains held** -> Resolve the keystone
  role-completeness conflict explicitly before advertising Anthropic as ready.
- **Static host list creates a dogfood-only success** -> Dynamic serving
  reconciliation is required for the generic Slack claim; an existing u-tiny
  env entry is diagnostic evidence only.
- **Long-lived subscription spend is unbounded** -> Durable binding ceilings
  are high-water server policy; every request capability has its own smaller
  invocation limit for reply/learning calls, plus token/cost and expiry
  narrowing, accounting, and immediate revocation checks.
- **Secret disclosure through errors/digests** -> Return only opaque IDs and
  redacted classifications; do not log vault records, paths, subprocess env,
  provider stderr, or raw secret-derived exception text.

## Migration Plan

Keep provider-authority V2 dark globally. Land the keystone request,
assignment, custody, and sink prerequisites first or in the same reviewed
stack; then enable Slice 1 only for a server-owned isolated universe/principal
canary. Adopt its existing vault subscription into opaque custody, mint/wire,
enable serving, and prove MCP plus Slack. Inventory every existing
`provider_ref`: placeholders remain held, automation bindings retain only their
existing operation, and no row is auto-upgraded. Rollback disables serving,
revokes the serving binding, and publishes held/deny-all assignment; it never
restores ambient fallback. Global enablement waits for role-complete Claude and
Codex mappings, generic serving enrollment, concurrency proof, independent
review, rendered chatbot evidence, and production-SHA verification.

## Open Questions

- Will the keystone role inventory add `claude-code` to every current live role,
  or must Anthropic remain held until a requester-owned supplement exists?

The remaining questions were decided for Slice 1: `credential_vault` owns
custody; acceptance is owner/founder-only; and `set_serving` includes dynamic
Slack-worker enrollment. Visitor spend remains a later bounded-grant change.

## Smallest correct slice-1 recommendation

Land one vertical, owner-only path that co-advances the keystone: implement its
request capability, requester-local assignment admission, opaque credential
custody reference, and sink validation; add the single atomic
`agent_binding/bind_serving_provider` operation producing an active
`converse`/`writer` `ProviderWorkBinding`; add `agent_binding/set_serving`; and
thread the same request capability through authenticated MCP and founder Slack
turns. Prove it first on an isolated canary and then invoke those generic
operations for u-tiny. Do not ship an intermediate pointer/config patch: without
the keystone carrier and custody generation it is selection without authority,
and without dynamic serving enrollment the Slack success remains a dogfood-only
host configuration rather than Slice 1.

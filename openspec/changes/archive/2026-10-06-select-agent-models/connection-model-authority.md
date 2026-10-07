# Connection authority and dynamic model selection

September 9, 2026. Exact proposed adaptation of the candidate-manifest review,
for pre-build review. No new top-level MCP action, custody mechanism or provider
SDK. The request launch allowance, additive manifest storage and multi-member
publication are now implemented locally with focused Windows/Ubuntu evidence;
publication implementation review returned ADAPT with one ordering/equality bug,
now regression-tested and corrected. There is no app/MCP activation. Selected
HTTP text calls now validate an accepted member, refreshed catalogue, cost bounds
and source/agent freshness through the real router/executor. Unselected v2
execution and full-agent HTTP tools remain held. Policy, CLI/local selection and
tool continuation are still unfinished; no live account-access claim.

The discovery transport/decoders and existing-capability metadata publication
are locally tested and independently APPROVED; see discovery-profile.md. Fresh
profile-bound snapshots are implemented and tested but have no app consumer.
Per-attempt HTTP text selection is locally integrated, tested and independently
APPROVED at83b19b49 (349s,42 reproduced tests). Live account evidence remains a gate. General readiness
and all production readers must be updated before activating the new policy.

September9 execution integration: request-local ModelRef is not authority. The
router overwrites ModelConfig.selected_model from freshly validated serving
facts. Current membership/custody is checked before and after discovery without
holding a SQLite read transaction over network IO; source age, profile and agent
are checked again after slot admission. Revoked anchors do not block independent
accepted members; budget reservation and non-authorizing config projection now
recognize each accepted binding. Explicit allowlists still narrow membership.
The protocol boundary applies request-side price ceilings, with downward-only
float conversion, and selected cost bounds constrain reservation. Unknown actual
cost remains unknown in response receipts and conservative in accounting.

Remaining integration constraint: the current synchronous serving context enters
the synchronous discovery path, as the existing HTTP executor also uses blocking
broker IO. Move these reads off the event loop during async ingress/tool-loop
integration without transferring thread-owned admission locks or trusting caller-
supplied catalogue data. This implementation is not ready for app activation.

## Why candidate identity cannot be a compiled model list

Existing HTTP ProviderDefinition identity includes model; serving bindings name
that immutable definition. Requiring a separately published manifest member for
every discovered model would make a catalogue refresh a permission rebind. The
owner instead asked to select new models through an already connected source.
Preserve legacy definitions and pins, but separate model choice from the accepted
connection when the owner enables the new model-selection policy.

## Concrete shape

Use the prior review's additive assignment manifest and child records. A child
names the existing provider-specific binding and exact custody tuple. For HTTP,
that provider is an existing definition identifying the protocol and grant; its
legacy model remains unchanged. New signed constraints add `model_scope`:

- `legacy`: no override, preserving the exact existing behavior.
- `explicit`: an accepted set of opaque model identifiers, or a native-default
  choice only when the connected executor supports it.
- `discovered`: models from the connection's owner-filtered, fresh catalogue,
  within the signed cost/capability restrictions and supported executor shape.

Each child commits to its connection/protocol/grant through the unchanged provider
definition and custody identity, plus model_scope and cost restrictions. Canonical
manifest hashing commits to the root identity followed by members sorted by
stable identity, not preference order. Child rows are keyed by universe,
assignment generation and provider, with no position field. The
root's provider_ref is a legacy structural anchor, not the policy's first choice.
Selecting/reordering an accepted model changes only preference generation.
Changing accepted connections, allowed model scope or spending restrictions
changes assignment generation and invalidates stale authorization. Keep v1
digests byte-compatible when no manifest is present; verify v2 assignment,
manifest, candidate identity and anchor-child/root equality on every read (the
anchor child is the one whose provider equals assignment.provider).

Publication is the existing two-phase pending/ready transaction, extended over
all children. Replay compares complete signed membership/constraints as well as
custody and budgets. Failed/pending/non-ready roots deny every child, including
leftovers from older generations. Disconnect/revoke uses existing live grant or
subscription-custody checks at each attempt, not eager destructive child cleanup.
Do not consult revoked primary custody before checking another accepted child.

## Per-attempt model choice

The shared validator resolves a member under the exact current assignment and
request carrier. It additionally validates the requested opaque model against
that member's model_scope and refreshed catalogue/cost evidence. It returns the
authorized model selection as a field on ServedProviderAuthority (None is legacy).
The router, not a user-supplied ModelConfig field, propagates that result into
the provider call. No global provider.model mutation and no activating old ignored
CLI definition.model fields. The adapter passes an explicit CLI model as a separate
argument, never shell text; native default omits the model flag. HTTP uses the
selected model in the encoded body without changing host, path, auth or definition.
Legacy callers without this new authorized selection keep today's exact behavior.

Discovery and inference share the existing credential-blind scoped proxy. A
connection declares its discovery protocol/path as metadata, but metadata never
grants an endpoint: GET must already be in the live owner-approved allowlist.
No guessed vendor host, remote response URL following, anonymous global catalogue
substituted for account filtering, or arbitrary executable discovery commands.
Protocol adapters normalize discovery; the core only sees opaque identifiers and
capability/price/freshness facts. New models in an existing protocol need no code
change; a genuinely new wire or CLI protocol still needs a compatible adapter.

For OpenRouter, use its authenticated user-filtered catalogue and enforce accepted
price ceilings in the inference request as well as local selection. Its documented
provider max_price supports prompt/completion/request/image limits; free-only text
agent calls set those to zero and do not enable paid server plugins. Other charge
components or executors without enforceable cost bounds must not be presented as
safe free-only fallbacks merely because a stale catalogue said zero. The concrete
normalizer must establish complete relevant pricing; missing evidence is unknown.
References checked September 9:
https://openrouter.ai/docs/guides/routing/provider-selection
https://openrouter.ai/docs/client-sdks/typescript/api-reference/models/models

## Bounded launch accounting

Keep legacy request limit 2. For new policy turns, seal one finite candidate plan
once under the authenticated request, with launch allowance twice its eligible
model-candidate count: at most one traversal for reply and one for learning.
Store the allowance in the existing request registry as set-once launch_limit;
sealing again with a different value fails. Sealing after any unsealed launch
also fails. Consumption uses the sealed value when present, never a later
caller's proposed larger value; unsealed legacy calls retain their existing cap.
This is not an arbitrary two-model limit. Do not increase the allowance after a
launch or mint another carrier to replenish it. Candidate refusal, budget refusal
or slot refusal before launch consumes no invocation. Reserve first, obtain the
slot, consume the request launch immediately before the provider call, and release
the reservation if that pre-launch gate fails. Keep binding token/cost/concurrency
ceilings in addition to this bound. HTTP tool-loop subrequests remain separately
bounded by their turn journal and outbound grant, not an unmetered inner loop.

## Integration gates to prove

1. A newly discovered authorized model can be selected without a new assignment,
   provider definition or deployment; legacy explicit pins remain unchanged.
2. Policy/current-choice input cannot forge manifest membership, choose an
   ungranted endpoint, broaden pricing, or cross owner/universe boundaries.
3. Primary grant revocation permits only independently authorized alternatives;
   removal of a member or failed publication cannot resurrect its prior binding.
4. Empty fallback order stays empty; preference reorder does not revoke an
   in-flight call; authority changes stop the next attempt, not replay past tools.
5. Three or more candidates can be tried within one sealed request; pre-launch
   refusals do not burn launch count; concurrent calls cannot inflate the count.
6. Actual model metadata and completed tool results remain per-turn; a switch
   preserves completed work and holds any ambiguous tool outcome for recovery.

## Review disposition

Claude's second candidate-authority shape pass returned ADAPT with two exact
corrections: provider-keyed children/explicit root identity, and a set-once launch
allowance in the existing request registry. Both are incorporated above. The
review accepts a model field on trusted ServedProviderAuthority, overwritten
into per-call configuration by the router, without a redundant token scheme.
OpenRouter zero-price enforcement is an inference from documented max-price
semantics, not live free-model evidence. Full review is preserved separately.

# Connection-authored discovery contracts — pre-build review

September 11, 2026. Correction to the closed discovery seam, partially implemented
locally, not deployed or a claim of universal CLI compatibility. Independent
pre-build review of 1b9afd38 returned ADAPT in 122s; its four required corrections
are incorporated below. This is not implementation or release approval.

Compiler progress:777ff9ba and correction315a0bba compose the reviewed bounded
interpreters without adding a provider registry entry. Publicationca73f353 now
accepts this document through the existing owned connection action, including
non-writing preview, and captures it in fresh discovery snapshots. Routing614d1709
now consumes that captured contract for selection, encoding, aggregate reservation,
usage and capacity. Real writer/broker/tool/journal tests pass; independent review
APPROVE135s covers that exact commit, not a live rollout. The document currently
requires `version`, `transport`, `catalogue`, `prices`, `inference`,
`quantity_model`, `extension_quantities`, `charge_bindings`, `capacity`, and
`price_bound_basis`; benchmark and usage are optional. Completeness pointers
live with the catalogue/benchmark row shapes. The whole document is JSON-only,
at most64KiB and16 nested levels; unknown fields refuse.

`extension_quantities` must cover exactly every constant's pointer: explicit
`quantity_neutral` or a nonempty list of aggregate dimensions compatible with
that constant's declared charge components. `charge_bindings` maps the three
supported canonical price components to input tokens, output tokens and requests.
The derived Interaction uses required advertised prices and all request ceilings;
the descriptor cannot mark additional charges excluded or supply unmetered status.
Base dispatch quantities cannot be removed. Additional billing dimensions remain
unsupported, not silently omitted; this is not complete future-provider support.

The installed WireProtocol now optionally supplies a request validator and its
protected request fields. Missing local validation refuses source compilation;
the legacy encoders/decoders and their existing callers are unchanged. Final
envelope validation compares exact canonical JSON with the compiler-generated
request, preserving types (true is not1), ceilings, constants and base fields.
Benchmark comparability uses declared source+score_schema+scale identity, never
source name alone. Current custom-source price basis accepts only
`source_request_caps`; tariff-only authority remains the separate prerequisite
below. Every one of these declarations remains non-authoritative until the
existing current-grant/assignment/dispatch path admits it. Publication verifies
current owned definition/admin/resource/grant and exact existing GET endpoints;
it does not create those permissions or establish answering-agent readiness.

The new outer descriptor has `schema_version:1`, exact catalogue URL, optional
benchmark URL and compiled `contract`; its internal `protocol` selector is empty,
not a new registry alias. Legacy descriptors retain their exact serialization.
`preview:true` is optional only with enabled model discovery and follows the same
BEGIN IMMEDIATE grant/resource/endpoint checks before returning without INSERT.
Both preview and publication return whole-descriptor identity and an explicit
source-semantics caveat, not a token authorizing inference. The source's
`availability_basis` is server-derived `owner_configured_contract`, while
`owner_filtered` remains false. Current local admission uses that declared-source
basis without granting independently verified privacy or account identity. Picker
inventory visibly distinguishes configured availability/privacy/charge claims.
Public authoring guidance/schema
discovery must still make the new descriptor understandable without reading
platform source; do not count hidden support as a completed user-facing tool.

## Intent and existing homes

An owner can describe an unfamiliar connected source's catalogue, prices,
ranking, capacity and supported request ceilings as bounded data. A newly
discovered model on that source can enter the existing picker and actual
authorized agent route without adding provider-named Python callbacks.
Do not solve the channel ratchet by renaming functions, exemptions or baselines.

Reuse `write_graph(target=connection, operation=configure_provider_capability)`
and `connection_capabilities.descriptor_json` with kind `model_discovery`.
The primitive checker currently misses this explicit dispatch; source confirms
it in `universe_server.py` and `api/provider_capability.py`. No new MCP handle,
connection registry, credential path, provider-definition field or SQL column.
This nevertheless changes a public/storage contract and needs pre-build review.

Existing descriptor `{protocol, catalogue_url, benchmark_url?}` keeps its exact
validation and interpretation. Local52f56a94 now resolves its protocol name to
installed JSON through the shared catalogue/request/capacity/usage compilers used
for new descriptors. The installed compatibility composer retains all14 price
fields,4 ceilings and the original Interaction exclusions/derived bounds. No rewrite
of old rows or definition IDs. Initial review APPROVE140s confirmed shared execution
but corrected the exact-byte claim: cap-key order differed. Correction9963d04b
preserves caller order;287 frozen-before/data-after comparisons now include24
unsorted byte-order permutations. Expanded870Windows/870actualLinux pass, zero
skips. Exact correction APPROVE59s reproduced287cases. The
internal legacy parsing/component parameters cannot be selected by owner JSON.
The old functions remain only as compatibility callers where actually needed;
do not retain a second working decoder or move brand branches into another file.

## Versioned document and bounded interpreter

New closed outer document: `{schema_version: 1, catalogue_url, benchmark_url?,
contract}`. It has no `protocol` field. Mixing versions/legacy fields refuses.
The contract has these closed groups:

| Group | Declared data |
|---|---|
| transport | Existing inference wire-protocol ID and catalogue auth scheme; exact catalogue/benchmark path and query already present in the granted URLs. |
| catalogue | Rows pointer, model ID pointer, optional canonical join-key pointer, optional default-ID pointer, tools declaration, input/output modality pointers and positive context-length pointers. |
| completeness | Optional total-count and next-page pointers. A present count must equal row count; a present next page must be empty. No automatic pagination or response URLs. |
| prices | Price-object pointer; closed mapping of source field to unit-bearing component and exact decimal scale; required fields; bounded override-list pointer; recognized non-price condition keys. |
| benchmark | Optional rows/source/ID/score/time pointers, exact source ID and score scales; one comparable source, no source-name inference. |
| inference | Required body-key set, excluded model-ID literal prefixes/suffixes/substrings, price-cap output paths and exact scales, required constant request fields, and charge-component relationships. |
| quantity_model | Versioned finite aggregate input/output/request bounds; explicit extension effects and dimension-compatible charge bindings are checked against them. |
| capacity | Finite HTTP status-to-scope/reason mapping plus optional standard Retry-After interpretation. |
| usage | Optional exact total-cost pointer and scale; absent/invalid cost remains unknown. |

Selectors are JSON Pointer paths only: object keys and nonnegative array indices,
at most 16 segments/512 characters; no wildcards, expressions, templates, regex,
callbacks, imports, transforms that execute code, response-driven URLs or loops
other than bounded catalogue/override lists. Document <=64 KiB, <=128 extraction
fields, <=64 price components, <=32 capacity cases. Wire response retains current
transport size/deadline limits; new-version contracts additionally permit at
most 10,000 model rows and 128 overrides per row. Legacy descriptors retain their
current transport-bounded behavior rather than silently acquiring new row caps.
Exceeding an applicable limit refuses the snapshot instead of publishing a prefix.
Unknown fields/version/operators refuse at publication and readback.

Exact scalar operations are finite: required/optional lookup, positive integer,
opaque identifier with surrounding whitespace rejected (never trimmed), string set, list-membership or boolean tools evidence,
minimum known positive contexts, and nonnegative decimal scaling. Amounts use
the existing exact integer-micro units. Scales are integer powers of ten within
the current bounded Decimal range; price normalization permits no rounding,
underflow-to-zero or invented prices. Benchmark scalar encoding retains legacy
numeric compatibility. Observed usage is separate: original JSON numeric tokens
are parsed as Decimal and positive fractional micros round UP, as before, never
down to free. New contracts specify accepted scalar encoding; floats must not be
an intermediate representation for money parsed from the wire.
Optional unknown capabilities remain unknown, never a successful default.

## Price closure and inference ceilings

Every key encountered in a declared price object or override must be mapped or
an explicitly recognized non-price condition. Unknown keys make pricing
ineligible, including additional zero-looking charges. A malformed base or
override invalidates that component; valid overrides take the maximum, never
the current cheaper time/tier. Required fields absent at base remain unknown.
New contracts cannot declare a source unmetered; local/subscription unmetered
evidence continues to come from the installed executor/owned binding.

Inference operates on the already validated installed codec body. Cap mappings
inject only explicit approved price ceilings, using exact output units. Constant
fields are literal JSON scalars/objects with bounded depth/size. Output paths
must be pairwise nonoverlapping and must not overwrite model, messages, tools,
tool_choice, credentials, headers, endpoints, temperature or token limits.
No remote catalogue entry may supply these fields. Conflicting existing body
fields refuse rather than merge. Every constant extension also declares its
effects within charge closure: plugin/routing options are unsupported unless
the compiled quantity model bounds their declared aggregate quantities and the
contract caps every charge. Each extension explicitly declares quantity-neutral
behavior or references its affected quantity bounds; there is no implicit
neutrality. These are owner-configured source promises, not installed-codec
knowledge of arbitrary future fields. A harmless-looking unknown constant is
not automatically permitted.
Supported model indirection restrictions are literal contract data; aliases
cannot bypass a known restriction, including legacy substring exclusions.

The existing `Interaction` structure stays the sole charge/capability policy:
required, excluded, ceiling, output-modality and bounded-extra components are
validated references to declared unit-bearing components. Missing caps or an
unrepresentable/unenforceable ceiling means ineligible, not "try and see".
Catalogue price <= permitted price is necessary but is not a spending grant.
Every declared charge must have an enforceable request cap and a dimensionally
compatible conservative reservation bound, or be established impossible by the
installed executor. A descriptor's exclusion/condition assertion alone cannot
establish that a charged operation is impossible. For v1, unfamiliar field names
normalize to existing supported reservation units: input/output per million
tokens in USD and USD per request; cache/reasoning charges use the existing
conservative bounded relationships. Additional quantities are unsupported until
the executor has a finite declared quantity bound, not a general formula language. An
existing component identifier can never acquire a new unit meaning.

`quantity_model` version 1 has exactly three aggregate dimensions: input tokens,
output tokens and requests. Each uses nonnegative integer coefficients for
`Q = a*input_bound + b*output_limit + c*attempts + d`; coefficients are at most
1,000,000 and runtime facts at most 10^18. Requests cannot depend on token facts.
All source-internal work, defaults and extensions must be included: eight
internal output samples require eight outputs in the reservation even when only
one is returned. Charge bindings retain the existing three unit meanings and
must match these dimensions. Exact integer arithmetic rounds each token charge
up; reservations must fit signed 64-bit micros. Affordability is bounded by the
actual executor/model output limit, never an unlimited free-output allowance.
This arithmetic can be checked locally; its remote semantic accuracy cannot.

Validation first checks the installed base wire body, then the final envelope's
exact extension paths, values/types, protected fields and complete quantity/charge
bindings. The codec need not understand source-specific extension meanings.
The compiled quantity model is shared by affordability and reservation, with a
fresh reservation per outbound attempt and no request mutation afterward.

Price-bound basis must be explicit. Current authority requiring source-promised
request caps remains required. Sources without such caps stay ineligible under
that authority. Supporting owner-accepted remote tariff ceilings is a separate
public/storage/money-authority design and review prerequisite, not permission
implied by configuring a descriptor. Neither basis independently proves a remote
server honors its tariff. No existing accepted consent changes meaning here.
The same compiled contract validates `SelectedModel.cost_upper_bound`,
`affordable_output`, dispatch ceilings and settlement as one accounting path;
replacing protocol lookups alone is insufficient. Free-only remains exact zero
ceilings; unknown usage never becomes zero usage. These are source-declared
ceiling semantics: writing a field does not prove a remote server honors it.

## Availability and semantic trust — explicitly separate from authority

No schema can independently prove a remote service's privacy or charging
promise. The legacy adapter already trusts the owner-chosen host to implement
its pinned credential-filtered contract; a compatible JSON body alone proves
neither that promise nor account identity. Do not turn a caller's
`account_filtered=true` into trusted evidence.

New custom contracts use the existing authenticated owner/admin configuration
action; that existing authority suffices, with no new human approval ceremony.
An optional `preview: true` validates without writing and returns the normalized
descriptor's digest and scope/cost summary. Publication returns the same digest;
it is identity/integrity data, never an approval credential. The summary states
that the connected source, not TinyAssets, declares availability, privacy and
ceiling semantics. It names the exact endpoints and notes the contract's
connection-wide sharing. An agent may compose/configure within its existing
authority; remote catalogue data cannot publish or change configuration.
Legacy calls keep their exact shape and gain no required preview or digest.

Authenticated configuration is NOT a credential, inference/spend grant,
human-review proof or independent validation of provider promises. No account IDs,
executor-tools flag or source-kind priority may come from the contract.
Subscription/local priority remains server-derived. Actual inference still
requires an accepted assignment/model, current grant/custody and price ceilings.

Replace the internal ambiguous `owner_filtered` truth projection with an
explicit availability basis for the policy boundary: `credential_filtered`,
`owner_configured_contract`, or `unverified`. Legacy adapters retain their old
credential-filtered meaning. A custom source is eligible only after authenticated
transport to the exact configured endpoint, the compiled structural checks and
all existing admission rules; its UI basis is "owner-configured source contract", never
"verified account availability". Unverified sources stay display-only. This is
an intentional reviewed distinction, not silently relabelling a declaration as
proof. No broader privacy guarantee is claimed than the owner's chosen source.
Eligibility relies explicitly on that configured source's declared semantics;
any independently required privacy restriction remains required and cannot be
marked verified by configuration. Missing evidence for such a restriction holds.

Identity covers normalized bytes including version, URLs and the whole
contract. Validate under the metadata write's existing BEGIN IMMEDIATE
grant/resource fence. No caller approval flag or trusted-basis enum is accepted.
Readers derive configured-source provenance from current authenticated
publication/authority and exact-endpoint transport, and compute the descriptor
digest from validated bytes. An unsupported contract never publishes a ready snapshot. Deletion,
revocation and owner/connection changes retain their current fail-closed rules.

## Ranking, capacity and freshness

Benchmarks join exact declared IDs; no model-name heuristics. Scores retain
source and scale identity, and source-supplied aware timestamp controls age.
Transport time cannot freshen old scores. Duplicate/ambiguous IDs are unranked;
future, missing, malformed or stale times are not fresh. Scores with a different
source/schema/scale identity are incomparable, even if source labels match.
Keep the existing stable ranked/unranked ordering and subscription/local
preference, allowing explicit user selection regardless of unknown ranking.

Capacity source scope is server-derived, distinct from descriptor identity and
authenticated account identity. Legacy protocol scopes retain current behavior.
For custom HTTP sources v1 uses one conservative `custom-http` scope across
connections when no independent account evidence exists: different hosts, keys,
definitions, contract digests or edits do not prove independent capacity. This
may conservatively hold another source after an unknown/account-wide refusal;
model-local refusals still advance normally. Do not invent narrower independence
from an arbitrary endpoint hostname or metadata. A future trusted account fact
can narrow grouping through the existing typed capacity mechanism.

Capacity status mappings may emit only the existing bounded reason vocabulary
and scopes `model`, `account`, `unknown`. They do not create an authenticated
account identity. Current unknown/shared-capacity conservative fallback rules
remain; no rotating credentials or infinite retries. Usage decoding never
supplies an actual answering model; that remains the installed response codec's
receipt. Capabilities are local executor facts combined with advertised model
support, not remote permission to activate a missing tool loop.

## Integration and compatibility

- `ModelDiscoveryCapability` carries and round-trips the full validated optional
  versioned contract, preserving legacy descriptor serialization exactly.
- Publication/readback use one compiler. Snapshot context includes the entire
  descriptor and current authority, not just the old protocol string. Current
  context is checked after fetch and immediately before every actual attempt.
- Replace protocol-name lookups in discovery, selected-model constraints and
  HTTP usage/capacity with the captured compiled contract. No process-global
  mutable profile registration or caller injection of executable objects.
- Installed wire protocol still must match the definition and supply an agent
  codec. This descriptor does not teach arbitrary CLI syntax or invent support
  for an unknown inference wire format. Those remain separate provider-agnostic
  capability work, not a reason to report the whole feature complete.
- No migration of saved choice/order, journals, definition IDs or existing
  candidate digests. Snapshot-only availability/comparability fields are not
  added to the existing persisted ModelAccess document. Unsupported versions
  hold on rollback; old binaries must not reinterpret them as legacy profiles.
- Bundled legacy service metadata becomes declarative data only after the shared
  compiler can consume a user-supplied unfamiliar contract. This is substantive
  extensibility, not moving strings solely to satisfy the ratchet.

## Required tests and rollout evidence

1. Strict publication/readback, optional preview no-write, exact digest identity, owner/admin
   and grant fences, same-connection sharing, invalid contract no overwrite,
   legacy byte/behavior compatibility, unpowered configuration.
2. Frozen old decoder/constraint/usage/capacity differential tests over existing
   fixtures and malformed inputs; new fields never hide unknown charges.
3. A distinctly shaped unfamiliar catalogue and source name, with a new opaque
   model ID, goes through public configure -> discovery -> picker -> saved/current
   policy -> actual writer/router/HTTP tool loop. No preset registration or source
   code edit. Include two separate connections whose contract changes must not
   create independent capacity, plus model-local refusal continuing safely.
   Synthetic wires are supporting evidence, not live acceptance.
4. Stale/incomplete/unknown data, incompatible codec, missing or unenforceable caps,
   altered contract or revoked grant cannot yield an inference call. Price/cap
   field conflicts and descriptor-origin URLs never escape existing permissions.
5. Windows/Linux focused groups, plugin parity, unchanged neutrality ratchet and
   exact-head independent release review/CI before deployment. Authenticated SHA
   and canary after deploy; ordinary rendered app/connector conversation is final
   proof. Do not operator-edit workflows or approve a new account/grant for it.

Pre-build reviewer must challenge the availability-consent distinction, bounded
contract expressiveness, exact ceiling safety and compatibility. Approval of this
document is not proof that arbitrary providers work or that the picker is live.

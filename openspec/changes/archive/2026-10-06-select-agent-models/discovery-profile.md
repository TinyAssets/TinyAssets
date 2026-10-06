# Discovery profile publication — implemented locally, not deployed

September 9,2026. The exact metadata home is already present:
`storage/outbound_connections.py` has `connection_capabilities`, keyed by
connection_id and capability_kind, storing a validated descriptor_json document.
Today its sole kind is realtime_voice. `api/provider_capability.py` and the
existing write_graph connection/configure_provider_capability branch publish it.
ProviderDefinition identity needs no extension. The primitive checker reports
no handler-map match for that operation, but direct source inspection confirms
the explicit universe_server branch; do not propose a duplicate action.

## Bounded extension (independent ADAPT corrections incorporated)

Add model_discovery as another typed descriptor in the existing table, not a
second connection registry. Use a separate ModelDiscoveryCapability value type;
do not overload realtime voice's session_url or change its existing projection.
One in-module capability spec table maps each kind to its value type, validator,
verb and URL fields; kind validation, publication and readback dispatch through
that same table. Model discovery uses GET; voice keeps its existing POST behavior.
Closed fields: protocol, catalogue_url, optional benchmark_url. Initially the
protocol adapter is openrouter_user_models_v1 (the documented authenticated
user-filtered response), with the existing artificial-analysis benchmark decoder.
Opaque model releases remain remote data. This adapter name is not a core routing
brand list. Unknown wire protocols must be reported unsupported, not interpreted
as one of the known schemas or activated from remote executable instructions.

All configured URLs are canonical bounded HTTPS destinations. Publishing the
descriptor checks the existing active connection, GET scope and allowlist for
each destination in the same write transaction. It adds no endpoint, scope or
credential permission. Removing metadata leaves the underlying connection and
all serving assignments unchanged. Realtime_voice behavior remains unchanged.

Extend the existing configure_provider_capability operation only for this kind
with a definition_id reference. Its handler derives principal and universe from
authenticated request context, requires the same explicit owner/admin ACL used
by connect_compute, loads the verified definition from that universe, and derives
the actual grant/connection from it. Do not accept a caller-supplied connection,
grant or owner identity. Revalidate live grant/resource ownership and universe
scope at publication. This new kind must not depend on a currently functioning
serving LLM or a ready serving assignment: unpowered users need to configure it.
Bypass the current-serving resolver entirely for this kind. Only its closed
payload shape admits definition_id; verified lookup checks content address and
universe bucket. Use a discovery-specific error for an unsupported connection,
not provider_voice_unsupported. Recheck the exact expected grant/owner/universe
and connection under the metadata write transaction to fence changes after the
handler's read. Legacy voice callers do not acquire a new required input.
The existing realtime voice shape and current-serving resolution stay intact.
Inaccessible resources receive the existing uniform not_found envelope.
The row is connection-scoped: all definitions/grants sharing that owned connection
share the profile; removing it through one removes it for all. Revocation leaves
metadata stored, but all discovery readers recheck the live grant/resource.

## Discovery evidence and execution boundary

read_http_discovery_document is implemented as internal transport only. It checks
server-derived context, current grant/resource, GET permission and exact URLs,
then uses the existing credential-blind resolver/broker. It reads one JSON
document, closes its proxy, rejects partial/redirect/malformed/oversized responses
and follows no response links. There is no external caller or profile activation.

The integration must load the current typed profile, read only its configured
destinations and compare profile plus current authority after the reads, before
publishing a snapshot. Initially fetch fresh rather than adding another durable
cache store. Carry source URLs, fetch time and a digest of the profile/current
connection authority into the snapshot; recheck at actual model authorization.
Read grant/resource/profile in one SQLite snapshot and verify the definition
before and after transport. Reuse the existing secret-free grant-identity digest
for credential-reference lineage; never read or hash credential material. This
records connection identity, not independent account-capacity evidence.

The pure policy's historical connection_id field is an opaque selector-source
key, not necessarily a raw HTTP ledger id (CLI sources have no such id). Use the
exact provider reference, api_key_http:<definition_id>, for that key so several
accepted definitions sharing physical custody cannot combine bindings or model
permissions. Keep the physical connection/grant separately in source provenance;
profile sharing does not merge assignment members. This matches provider-keyed
manifest authority while preserving legacy definition identity and opaque model
IDs. Snapshot output stays advisory and cannot grant inference by construction.
Never mark arbitrary same-schema JSON as verified account-filtered availability.
The protocol adapter pins /api/v1/models/user and owns account-filtered semantics;
the host remains the owner's explicitly granted choice. A global /models path,
different path, arbitrary query or caller/body owner_filtered flag is refused.
The adapter alone sets that fact after a successful200 through the exact live
credentialed proxy, never from response metadata. Account identity remains None;
do not invent independent account capacity. Require the protocol's bearer auth
shape; an anonymous endpoint cannot establish account-filtered availability.

One evidence-backed refinement of the review's no-query recommendation: the
owner wants ALL available choices. Official docs rechecked September9,21:43 UTC
explicitly provide output_modalities=all; without it the endpoint defaults to
text-output models. Pin that single exact query in this adapter as well as the
path, rather than permitting arbitrary queries or silently showing a partial
catalogue. Publication requires the existing grant to admit it. Pin the optional
benchmark path to /api/v1/benchmarks without a query. This is a fixed protocol
contract, not a model-release list or new endpoint permission. Documentation:
https://openrouter.ai/docs/api/api-reference/models/list-models-filtered-by-user-provider-preferences-privacy-settings-and-guardrails

Benchmark as_of controls score freshness; a new HTTP fetch does not freshen old
scores. Missing benchmark coverage leaves models visible but unranked. Missing
or unsupported price components never become zero. Decoder output cannot assert
executor tool support, independent account capacity, assignment membership or
spending permission. The HTTP executor remains text-only until its real tool loop
is implemented; do not advertise HTTP full-agent readiness from catalogue tools.

## Acceptance before activation

- Existing realtime capability tests pass unchanged; owner/universe isolation and
  unpowered access use authenticated handler tests, including malformed/copied
  definitions, revoked grants and connection ownership changes.
- Idempotent configure/read/remove uses the existing table and leaves identity,
  grants, endpoints and assignment generation unchanged. Missing GET authority
  refuses publication, including optional benchmark URLs.
- Profile mutation or authority revocation during discovery prevents publication
  of a fresh snapshot. A schema-compatible global catalogue cannot claim filtered
  availability. Concurrent refresh is single-flight; async ingress offloads the
  blocking broker operation without spawning duplicate calls on cancellation.
- Fresh unknown model ids become selectable only through the separate per-attempt
  model validator and approved cost/capability limits. Prove actual execution,
  UI and rendered app behavior later; these unit fixtures are not that proof.

Profile publication now follows this adapted design and received independent
APPROVE atc8d0dbab. Profile-bound refresh/snapshot provenance and per-event-loop
single-flight are also implemented locally, with no app consumer yet. Combined
tests pass301 Windows/Ubuntu, no skips. Actual selection remains unfinished.
Evidence: docs/reviews/2026-09-09-discovery-snapshot-proof.md and the publication
proof/review artifacts it links.

Review: docs/reviews/2026-09-09-model-discovery-review.md. Internal transport and
decoders APPROVED at4e21c3d5; publication APPROVED atc8d0dbab. Snapshot/selection
integration still needs independent review before landing. Actual account pricing may contain
additional charge components; do not claim automatic eligibility from fixtures.

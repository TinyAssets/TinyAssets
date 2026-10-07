# Request-local answering-model receipt

September10,2026 UTC; task3.1 prerequisite, no policy/storage/authority change.

## Source and contract

The successful ProviderResponse is discarded by providers.call before reaching
universe_intelligence.converse. universe_server.converse currently returns JSON
reply/universe_id; app MCP.converse consumes that envelope. Learning extraction
after _call_writer can replace the global last-provider label. Reusing that
label would misattribute an answer across users or to a later learning call.

Keep existing string-returning Python APIs and the public MCP input signature.
Add an optional internal response observer through call_provider/_call_writer/
universe_intelligence.converse, passed only when requested by the authenticated
server caller. The observer receives a completed writer ProviderResponse before
it is reduced to text; it is not passed to extract_learning. Each server request
owns a fresh collector, no global, context-local last-call slot or shared cache.
Observer failure must never discard the earned answer or cause an inference
retry. Mock/skipped/failed calls supply no successful execution receipt.

ProviderResponse gains optional reported_model evidence (empty by default),
separate from legacy model which some CLI adapters fill with requested/default
labels. HTTP sets it from the same validated response field as its current
model; adapters lacking trustworthy model telemetry remain explicitly unknown.
No scraping stderr or guessing resolved names from a requested alias. This
adds a generic evidence contract, not provider names to the core.

Public successful converse JSON adds optional execution object with provider,
model and model_status (reported/unknown), paired with this exact reply. Omit
execution if the collector received no successful provider envelope. These are
bounded printable labels only, never raw output, credentials, costs, authority,
tool results or a request carrier. Existing reply/universe_id and direct str
return remain unchanged; no top-level MCP handle or decorator change. A model
receipt cannot change any saved/current choice or authorize the next attempt.

This slice bridges truthful answer metadata, not the complete picker. Later UI
uses this response-local receipt separately from preferred/attempting state and
persists any historical labels only through a separately reviewed storage seam.
No decorative enabled selector, full HTTP agent claim or automatic fallback yet.

## Verification

Concurrent collectors cannot see each other's provider/model; later learning
cannot overwrite the writer's receipt. A failed/skipped/mock attempt doesn't
reuse prior metadata. Callback exceptions do not rerun inference. Unknown,
malformed and missing reported_model stay unknown without dropping reply.
HTTP requested alias differs from reported model and legacy positional response
constructors remain valid. Existing converse permission/error/string/structured
adapter behavior remains intact. Independent public-envelope shape review before
server integration; implementation tests and review before landing, rendered app
proof only after deployment. This is not model-selection completion.

## Independent shape disposition

Review12667 completed APPROVE231s, including inspection of the in-flight internal
implementation. Server integration remains the same small optional-envelope
addition and has direct regression coverage. UI must map opaque provider refs to
owned display names. CLI evidence is unknown only until a verified structured
signal is implemented, not a permanent CLI exclusion. Keep reported/default
labels distinct, including when an init event describes a model before an
internal CLI fallback. No new review round solely for the approved shape.

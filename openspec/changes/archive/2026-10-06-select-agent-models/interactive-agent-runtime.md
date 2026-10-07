# Interactive agent runtime: join the existing pieces

Proposed implementation within tasks2.3/2.4 of select-agent-models. September10,
2026. This feature branch is unmerged; nothing below claims deployed behavior.
The source checks in http-agent-loop.md and approved journal/client/codec reviews
are prerequisites, not a substitute for a working agent. No new MCP handle.

## Entry point and one-inference boundary

Keep universe_intelligence._call_writer as the ordinary conversational entry.
Introduce one engine-owned HTTP agent runner called there for an authorized HTTP
serving selection with engine tools enabled. CLI sources retain their native
agent execution; do not wrap a CLI turn in the HTTP tool protocol. The existing
whole-turn no-replay rule remains for CLI attempts and unknown tool outcomes.

ApiKeyHttpProvider must continue to perform exactly one network inference per
complete call, through the current exact connection grant and credential-blind
broker. Add an immutable internal agent-request value to ModelConfig and an
optional typed AgentReply to ProviderResponse. It contains only the prompt/system,
advertised tool definitions and validated completed history, not authority or
arbitrary transport headers/URL. Legacy text calls remain unchanged. The HTTP
adapter encodes/decodes this value with the approved codec and still drains its
owned executor on cancellation. No tool dispatch occurs inside provider.complete.

The runner calls the existing router for EACH inference. Router selection,
assignment admission, reservation, slot, final prelaunch check, invocation
consumption and actual settlement remain authoritative. Do not put a second
reservation/settlement implementation in the runner. Extend the router with a
private synchronous pre-dispatch observer receiving the admitted reservation and
fresh served authority; the runner commits journal.begin_round there. Observer
placement is AFTER successful capability consumption and BEFORE provider.complete;
keep the existing before_provider_launch selection recheck at its original point.
Observer failure prevents launch and releases the unspent reservation. Store no live
capability in that callback's journal values. The callback is internal, not an
external hook supplied by ModelConfig or workflow data.

The full encoded request (tools plus completed transcript) must determine the
input token reservation estimate, not just the original short user prompt.
Likewise missing-usage output settlement sizes the complete assistant continuation
(including tool-call JSON), not resp.text, which may be empty for a tool batch.
Prepare the immutable request before reservation, then apply the reserved output
cap without widening any input/cost bounds. A tool-request response is a valid
single inference result, NOT the terminal writer reply or its display receipt.
Only the final response is sent to the existing writer response observer.

## Finite plan and per-attempt authority

At the trusted served boundary, resolve the current/saved/automatic candidate plan
against the actual accepted assignment manifest. Seal the genuine request's
launch allowance once, before its first launch. Resolve the accepted manifest
at FIRST served_provider_authority resolution; seal refusal is
an ordering error, never permission to fall back to the fixed two-call default.
Derive a finite ceiling from distinct accepted bindings' existing max_invocations,
deduplicating bindings and checking integer overflow. This is a runaway upper
bound, not additional budget: every inference still reserves current rolling
usage/token/cost allowance. Do not add a hidden arbitrary max-tools/max-rounds
constant or refill a spent carrier. Keep the existing user interrupt and served
absolute safety cap; absence of a new model response is not proof of free usage.

Every selected attempt goes through fresh owner/home/deletion, agent binding,
assignment member, grant/custody, capability and price validation. No stable
serving binding is rewritten just to traverse fallbacks. Existing explicit pins,
explicit empty fallback tails and source/account exhaustion behavior must remain.
The current dynamic prepare_selected_model accepts HTTP only; subscription/local
model discovery and selection still need native executor support for the final
picker. Do not claim the HTTP runtime alone supplies that broader capability.

## Durable effects and safe inference continuation

Create the journal with server-derived owner/universe after genuine request
authorization. Journal mutations used by the runner recheck current home and
the deletion tombstone within the same database transaction (reuse the existing
home guard); no authenticated state is inferred from the turn id. Respect the
service's existing writer barrier. Do not hold a SQLite transaction across IO.

Open the existing engine_tool_client against its freshly owner/graph-checked
route, fetch the tool inventory, then advertise exactly that inventory. Before
each requested tool, recheck current execution authority, commit the next
journal start, and dispatch only if it returned applied. Exact finalized tool
results are journaled before another inference. Known isError is a completed
result; known nontext is preserved and held until supported, not replayed.
Post-dispatch exception/cancellation becomes unknown. If result persistence fails,
the committed started row stays ambiguous. There is no automatic tool retry.

An inference-only capacity failure cannot have dispatched an engine tool. After
conservative settlement, a fresh eligible candidate may continue using completed
history. Extend begin_round to permit this explicit inference retry only after a
recorded failed inference, with the current-home guard and genuine caller's
fresh admission. Earlier failed inference rows remain immutable history; teach
the reader to validate them without requiring them to contain tool results.
Use explicit after_failed_inference=True, accepting held_transport only with a
failed last inference having neither reply nor tools. The ordinary ready path
must not accept that flag. Runner and journal remain on the capability-claiming
thread; only the wire request moves to the existing owned executor. The existing
all-skipped retry reuses the same turn and is allowed only with zero rounds.
Started/unknown tools, incomplete batches and corrupt rows remain nonresumable.
No stored retry bit or turn id grants new execution authority.

Do not implement automatic post-crash resurrection. Recovery into a new genuine
owner request may load completed progress, but must never claim/replay an old
started tool or recreate a revoked capability. Define a non-executing abandon
transition for quiescent ready roots, including a fully settled prefix, so a
later pre-intent failure cannot block scoped reset forever. Preserve all known
history and refuse resume after close. Never close an in-flight or ambiguous
frontier. Independent implementation ADAPT567s identified and required this
extension beyond unused roots. Any later public resume UX needs its own contract.

## Portable history, price guard and observed usage

Extend the pure codec with an explicit portable history renderer. Validate each
historical reply against ITS captured tool schema, and its exact ordered results;
current tool discovery controls only NEW tool requests. A tool removed from today's
inventory must not invalidate an already-known historical result. Identity is
per round/call ordinal, with within-batch unique wire IDs; preserve those returned
IDs verbatim and allow repeated IDs across distinct completed rounds.

Same source/model may retain its supported reasoning continuation. A source/model
switch projects only standard assistant text/tool-call fields and corresponding
known result messages, dropping provider-specific reasoning and opaque extras.
Never forward another source's encrypted/private reasoning format or relabel it
as the new model's own result. Preserve exact tool arguments and user/tool text.
Unknown/unrepresentable stops hold; this is supported-protocol compatibility,
not a promise to understand arbitrary unknown executable protocols.

Extend the OpenRouter price-constrained body validator to admit only this exact
agent request shape: declared function tools, validated assistant tool calls,
ordered tool-result messages and controlled tool_choice. Preserve complete
max_price and require_parameters, no plugins/search, no models array, no arbitrary
provider object and no model alias that bypasses bounds. Shared engine code
does not select vendor model-release names. This boundary adapter owns wire rules.
Tool-using selection requires fresh discovered tools support; text-only
Interaction.needs_tools=False is insufficient and require_parameters is not an
eligibility substitute.

## Independent shape disposition, September10

Claude reviewed exact a56893124bf4398d3ea72789f08c3042dd58e125 read-only in294s:
ADAPT, seven required corrections. The requirements above incorporate observer
ordering, first-resolution sealing, explicit failed-inference continuation,
transaction-local home guard/unused abandon, both-side accounting, claiming-thread
runner placement, and tools-capable constrained selection. Build these in the
existing integration lane; no further proposal layer was requested. Full
evidence: docs/reviews/2026-09-10-interactive-agent-runtime-shape-review.md.

Keep actual token/cost observations nullable. OpenRouter's current documented
usage.cost is account charge, not upstream cost; usage is now automatic. Parse
only finite nonnegative validated cost evidence with explicit units/conservative
integer conversion. Missing usage stays unknown and existing settlement policy
applies; a paid fallback is never authorized by unknown/zero substituted data.

## Acceptance

Test the REAL runner/router/HTTP adapter/journal/engine client composition using
synthetic authorized fixtures and local mock HTTP/MCP servers: two inference
rounds around a genuine MCP result; capacity fallback after a completed result
without replay; account-wide exhaustion skipping siblings; revoked candidate;
free-only cap preservation; cancellation and failed result storage; missing
actual usage; no body/grant widening; concurrent per-turn choices and owner
isolation. Keep all existing text/CLI tests. Windows plus actual Linux and an
independent review precede activation. Then deploy the real runtime and verify
through the owned app using ordinary user language. No operator workflow fixes,
owner permission approvals or background-self edits are authorized by this plan.

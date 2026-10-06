# Capacity integration evidence, September10 2026

Implementation within existing tasks2.2/2.3, not a new proposal or deployed claim.
The source-derived shape received opposite-family review before implementation.
Scoped candidate continuation is now built locally; public policy consumption
and the model picker remain unwired. Exact964c58f2 implementation independently
APPROVE417s; follow-ups retained in docs/reviews/2026-09-10-model-capacity-review.md.

Official sources read September10 05:56UTC:

- https://openrouter.ai/docs/api_reference/limits documents account/key credit
  limits and distinguishes platform429 from upstream429; status alone cannot
  prove model-local exhaustion. Platform limit responses carry X-RateLimit fields.
- https://openrouter.ai/docs/api_reference/errors-and-debugging documents typed
  error.metadata.error_type, Retry-After, and errors inside successful HTTP bodies
  after processing began. Empty output can still incur charge.

Implemented implications, pending independent implementation review: normalize at the protocol boundary,
not by model names or arbitrary error-message substrings. Preserve unknown scope
conservatively; a second connection/key is not independent account capacity.
Carry typed evidence through router aggregation without cooling every model for
a proven model-only failure. Do not treat a200 error body as pre-dispatch zero
usage, or moderation/auth/schema errors as permission to cycle models. Honor
retry hints and accepted free-only ceilings without raising credit limits or
requesting broader authority automatically.

Implementation seams: model_policy.Exhaustion/order_models orders scoped advisory
candidates; selected HTTP diagnostics now retains normalized capacity scope.
Legacy text/CLI classification remains unchanged. universe_intelligence.converse constructs
UniverseContext without model_selection, so saved/current/default consumption
still needs authenticated ingress integration. Do not enable a picker merely
because its preferences can be stored.

## Concrete next runtime contract for review

Add a private immutable normalized capacity signal: scope model/account/unknown,
sanitized failure kind and optional finite nonnegative retry delay. A protocol
boundary may supply it only for an admitted selected HTTP agent inference; legacy
text/CLI classification remains unchanged. No raw message substring, remote model
name or caller config grants scope. The discovery protocol contract owns decoding.

For OpenRouter's documented status semantics:402 is account/key-credit exhaustion;
503 (no available provider satisfying this model's routing requirements) is model
unavailability;429 is unknown/shared unless documented structured evidence proves
narrower scope. Do not infer model-only from an upstream provider name/code alone.
Unknown scope conservatively excludes same-provider siblings in the advisory
kernel; absence of authenticated account identity never proves an independent key.
Authentication, moderation, malformed input, generic500/502, unknown transport and
partial successful bodies remain holds, not capacity fallbacks in this slice.

Represent confirmed non-2xx capacity refusals as a ProviderUnavailableError
subclass: the documented pre-generation refusal releases unspent reservations.
HTTP200 error bodies, empty results and statusless outcomes retain the existing
indeterminate path. Thread typed scope through ProviderAttemptDiagnostic and
AllProvidersExhaustedError. A dedicated router catch BEFORE the existing rate/
overload handlers skips whole-provider cooldown for model-only scope; account/
unknown retain it. Honor bounded
Retry-After; invalid/missing hints stay unknown, not invented timestamps.

The runner then consumes a finite advisory plan of immutable ModelRefs prepared
from existing order_models, never a grant. Before every candidate it re-enters
the current selected serving-authority path. After a recorded inference-only
capacity failure it may call begin_round(after_failed_inference=True), preserving
completed tool history and all previous launch/accounting rows. No retry of a
started/ambiguous tool, no implicit return to the original prompt, no cycling to
an exhausted account. Legacy pins and explicit empty fallback tails remain held.
Fold unknown to account when constructing the kernel's existing Exhaustion; do
not add a third kernel scope. In this slice429 is always unknown, never guessed
from provider names/codes. Re-run order_models with accumulated exhaustion after
each failure rather than duplicating its identity/filtering rules in the runner.
502 remains a deliberate conservative hold because malformed provider responses
and model unavailability are not distinguished by this slice's typed decoder.
Tests must exercise real composition: model capacity after a known result,
account/unknown exclusion, revocation before replacement, and free-only price
ceilings on every actual POST. Saved/current/public ingress remains the next
connection to this same plan, not a parallel authority or preference store.

Independent review1878,341s, exact8cb8d991: IMPLEMENTATION APPROVE;
CAPACITY_SHAPE ADAPT. The three required changes above (settlement inheritance,
dedicated cooldown catch, unknown-to-account fold) are incorporated before code.
Full result recovered from exact transcriptf5bb6a53-bb74-4cd2-b5d0-25450c5b5e1a
after the known stop-hook recap.13 composition tests independently passed5.05s.

Local implementation verification September10 2026: 52 focused tests pass;
broader 16-file group passes515 Windows/515 actual Docker Linux, zero skips.
See docs/reviews/2026-09-10-model-capacity-proof.md. No provider call, live tool
effect, deploy or picker completion is claimed by these synthetic composition tests.

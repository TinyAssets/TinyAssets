# Mixed-source execution — release-blocking correction

## Required outcome and observed defect

The owner can order native/subscription/local and HTTP choices together. The
runtime must execute that order at safe capacity boundaries, retaining completed
work. Hiding mixed choices is not completion of the requested capability.
At reviewed9374a633, _call_writer chose an execution style once; native exhaustion
never advanced the plan, while the HTTP loop advanced to native with an
incompatible structured request. Coordinator d1d3b39e corrects those dispatch
paths locally, preserving default/current/saved ordering. Complete native
attempted-capacity evidence and live acceptance remain open; see
docs/reviews/2026-09-11-mixed-agent-execution-proof.md.

## One coordinator, two installed execution capabilities

Use one in-process interactive-turn coordinator for any selected candidate with
an AgentModelPlan. A step capability is either engine-managed inference/tools or
native whole-agent execution. Select from installed executor capability, not
model names or service-specific fallback branches. Native providers keep their
CLI subprocesses; no primary-writer API SDK, fabricated native model or fake
HTTP SelectedModel. Selection still means the exact accepted member reference.

The coordinator owns original prompt/system, captured policy, current selection,
accumulated exhaustion, attempted references, one absolute deadline and progress.
Every candidate is freshly authorized through the existing router/assignment
entrypoint. No reused ServedProviderAuthority, no caller-provided prepared model,
no cost/token limit union and no credential copying. Current request/tool fences
remain before inference and effects. User's saved/current policies are not edited.

## Closed retry evidence

A shared pure classifier consumes exact typed per-attempt diagnostics. An all-
skipped capacity gate means nothing launched. An attempted capacity refusal is
retryable only with explicit locally observed side_effect_state=none. Missing,
possible, committed, malformed or contradictory effect evidence cannot be
upgraded to none. Unknown errors, timeout, auth, malformed protocol and uncertain
tool outcomes do not become capacity just because another model exists.

Require diagnostics for the current exact provider, no unaccounted attempted
provider. Preserve model-local vs account scope; missing scope is conservative
account scope and must not rotate keys on an unproven independent account.
Unknown account relationships use existing model-policy exclusion. No sleep on
the interactive worker. Retain retry hints as evidence, not an instruction to
retry an exhausted source immediately. Traversal is finite; never revisit a
candidate or fabricate an eligible replacement outside the captured plan.

Native CLI adapters may attest no effects only from a complete supported stream
or another execution-bound fact. Empty/malformed/unknown protocol is not proof.
Existing streamed tool observations remain sticky: a later tool-finished event
must not erase the fact that work happened. Do not broaden fallback from legacy
unselected writer calls, which keep their existing one-all-skipped retry rule.

Source re-verification September11: installed Codex JSONL explicitly documents
best-effort intermediate tool events; Claude's liveness normalizer intentionally
accepts unknown blocks/subtypes as heartbeats. Neither liveness trace proves
complete effects coverage. Do not manufacture protocol_complete from terminal
presence plus an empty tool list. Existing adapters report incomplete coverage;
attempted capacity holds unless actual execution-bound no-effects proof exists.
All-skipped quota gates can still advance without a native launch. Native success
uses its validated successful terminal plus reaped child, with effects unknown;
it ends the turn and cannot precede another candidate, so complete effects coverage
is not necessary for success. The journal's completed branch must reflect that
distinction without weakening capacity_no_effects. This does not declare native
first-attempt capacity fallback solved; complete observation remains required.

## Durable progress and native delegation

Extend the private journal by explicit versioned native-step records, preserving
version-one HTTP bytes/readers. Native model="" truthfully means provider
default. Do not synthesize a native AgentReply as if it were a tool-free HTTP
inference. A native result is a whole-agent terminal response with its real
provider/optional reported model/accounting, and unknown internal tool detail.

The native step records intent after fresh reservation/claim and before launch,
using the existing claim observer with a clearly separate installed step kind.
Its terminal record distinguishes succeeded, known-no-effects capacity refusal
and indeterminate. Existing SQL tables may carry a version-two candidate/reply
payload; no destructive migration or alteration of old rows. Snapshot validation
must branch on the explicit version/kind and must never infer a retryable native
failure from the absence of HTTP tool rows. Incomplete/unknown native work holds.

Preserve all earlier fully completed engine-managed tool calls/results exactly
when the next step is native. Project the same validated history as portable
data, strip foreign private reasoning, and render a bounded structured history
block beside the unchanged original request. Treat tool content as untrusted
data, never system authority or instructions. Do not summarize/truncate user
content or manufacture missing results. If it cannot fit, fail visibly; no lossy
fallback. Native agent tools still use the current engine grant.

If native capacity refusal proves no effects, that step contributes no tool
history and the next HTTP/native candidate receives the same prior completed
history. Native success ends the turn; native uncertain effects cannot be
restarted on another source. No crash resurrection or background replay is added.
Final answering-model observer uses only the final genuine provider response.

## Verification and sequencing

Review this storage/observer boundary before implementing it. The pure closed
retry classifier may be built independently; it grants no authority and is not
activation of mixed fallback. Then connect one coordinator and versioned journal
with a genuine native/HTTP/native acceptance fixture through real request,
assignment, router and broker seams. Do not replace those seams with mock grants.

Cover automatic native-first exhaustion, explicit HTTP-first, native/native,
HTTP tools -> native continuation, native no-effects -> HTTP preserving earlier
results, uncertain native/tool effects held, account siblings skipped, empty
explicit tail, revoked fallback, no paid widening, once-only final receipt,
deadline/cancellation, legacy no-plan compatibility, v1 snapshot differential
decoding and version-two malformed/mismatched record refusal. Run Windows and
actual Linux, independent exact-head review, fresh CI, protected deployment proof
and rendered ordinary app retest. Neither the classifier nor a hidden UI option
closes this requirement on its own.

## Pre-build review disposition

Independent review ADAPT143s agrees with the coordinator and requires these
concrete boundaries before runtime activation. This section resolves those
requirements; it does not claim the unbuilt integration is approved.

1. Installed step kind is separate from config.agent_request presence. Add a
   private native_agent/engine_inference contract to coordinator/router, validate
   it against the actual resolved executor after source resolution, and reject
   mismatches before launch. Both kinds use agent-turn launch allowance under
   existing per-binding cost/token budgets. Native receives agent_request=None
   and selected_model=None, never a forged HTTP model. Observer authorization
   requires writer/converse, current selected serving authority and matched kind.
2. Version-two native candidate/reply has a distinct terminal union: completed,
   capacity_no_effects, indeterminate. Its failure branch durably stores the
   validated evidence, never just missing HTTP tools. _read/_frontier and prefix
   validation distinguish that union from version-one engine inference. A native
   started step is reset-blocking; uncertain native outcomes use a distinct
   held_native_unknown state that reset_blockers includes, not held_transport.
   Only explicit native capacity_no_effects or existing valid engine refusal
   may precede another step. Old v1 bytes/semantics remain unchanged. Test scoped
   reset while native started/unknown, and after known-no-effects or success.
3. Complete native failure evidence includes exact attempt slot/provider,
   supported complete protocol, reaped child and sticky side_effect_state=none.
   Claude's initial none without terminal proof is insufficient; Codex's empty
   current in-flight set after clearing is insufficient. Propagate the evidence
   through post-stream terminal classification in both installed adapters. Unknown
   events/EOF/truncation/reader errors or possible/committed tool activity hold.
   The router may attach unconditional no-effects to SelectedModelCapacityError
   only for a proven engine-inference refusal, never native delegation. The pure
   classifier requires explicit execution kind plus aligned native proof slots;
   a summary failure_class alone never authorizes traversal.
4. Native claim observer does not call HTTP _begin: it hashes actual native
   rendered input and records the exact authorized source, model="" where
   default, binding and reservation after claim/before launch. Factor historical
   batch validation into a shared projector; do not call a model-requiring HTTP
   encoder with an invented native alias. Skip native history only for a validated
   no-effects terminal; any incomplete or ambiguous prior step holds. Preserve
   exact argument/result text and exclude foreign reasoning.
5. Native terminal records distinguish requested/default model, legacy configured
   response label and optional reported_model. Accounting values copied from the
   executor are explicitly executor_accounting (estimated or unknown), not
   claimed provider-reported billing. Existing budget settlement remains intact;
   do not turn unknown cost/token/model data into zero or a verified fact. Only
   the coordinator's final successful response invokes the answer observer.

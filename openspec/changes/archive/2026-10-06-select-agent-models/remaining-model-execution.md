# Remaining model execution — September14 reviewed decisions

Owner approved one remaining-design Fable5.1 review and one final release review.
The first completed against f8299c49 in576s, VERDICT ADAPT, source reasoning only.
Full artifact: `docs/reviews/2026-09-14-model-selector-shape-fable.md`.
This document is the lead disposition before implementation. It does not approve
the full PR or describe deployed behavior.

## Native selection (R1/R2)

AGREE: separate executor default, requested opaque ID, and reported answering ID.
Introduce a typed native selection, not a fabricated HTTP pricing/wire contract.
The router overwrites request-local native configuration from current authority;
ordinary caller configuration cannot confer selection permission. A selected
default omits the CLI model argument and ignores process-global model overrides.
Codex uses `-m`, Claude uses `--model`; no native implicit fallback flag is added.

Explicit IDs already accepted in ModelAccess may execute as owner-declared IDs.
They are not verified available and the picker must label their basis. This is
incremental propagation, not completion of the owner's all-available-model list.
Discovered IDs require fresh owner-bound enumeration, matching current custody.
Provider-default execution remains valid when enumeration is unavailable.

Register discovery/selection behavior on installed executor objects, not vendor
names or release lists in the policy kernel. Enumeration gets an exact owned
credential snapshot, runs outside admission locks and database transactions,
and cleans it up in finally. Do not use the maintainer home or issue inference
to enumerate. Codex model/list is a documented metadata-only seam; its current
official documentation was fetched September14. Claude's documented initialize
control response contains models, but its direct CLI transport and completeness
still require validation. No SDK primary writer or fabricated enumeration claim.

Native workflow evidence is an explicitly versioned alternative inside the
existing strict selection evidence field; HTTP evidence remains byte-compatible.
Owner-declared evidence contains no observation timestamp: declaration is not
discovery. The router carries native selection separately from HTTP selected-model
facts, avoiding accidental HTTP cost/context calculations for native executors.
Native admission checks exact member, model scope, current custody and lifecycle.
Inspection distinguishes owner declaration from enumeration and actual receipts.

## Workflow tool coordinator (R3/R4/R6/R7)

AGREE: keep the existing served-request tool fence chat-only. A shared progress
loop receives separate chat/workflow authority adapters. Each workflow inference
reserves under the same receipt/claim; each tool rechecks current owner, immutable
work/branch, member and applicable cancellation/lease/activation. Configured tool
identity comes from receipt facts, never caller actor/graph strings. No fake chat
request, parallel grant store, or journal-as-authority path.

Before relaxing existing router/tool refusals, pin them with negative tests.
Add read-only sealed carrier accessors for round provenance; observe each actual
admitted workflow inference. Add versioned journal lineage for work-owned rounds,
retaining legacy chat record decoding and excluding work records from chat-only
projection. A completed tool result is preserved; uncertain external effects
hold and suppress whole-node retry, rather than beginning a fresh replayable turn.

Carrier provenance accessors are now built: reservation ID, work receipt ID and
selected member binding ID/generation/digest. Legacy provider-scoped receipts
retain their binding tuple. Reads do not consume/rearm a carrier or grant fresh
authority. Windows/Linux real-store groups each pass 115 tests, zero skips;
HTTP compiler/router cases verify the selected member rather than the aggregate
manifest root. This is not yet the dispatch observer or workflow adapter.

## Round allowance (R5 — existing authorized ceiling)

### R4 format correction before implementation

DISAGREE_EVIDENCE with reusing round version2 for work: current
storage/agent_turn_journal.py dispatches that version to NativeInput, whose
existing native payload is version2/kind=native_agent. Preserve HTTP version1
and native version2 byte-for-byte. New work payloads use version3 with explicit
kind (engine_inference/native_agent), authority_kind=work_invocation and a
nonempty work_receipt_id. Root input headers use version3 with the same lineage;
chat headers remain version2 (and legacy version1 still reads). SQL containers
remain unchanged. Every inserted/read round must match its root lineage, so work
cannot masquerade as chat or move between work receipts. TurnSnapshot exposes
lineage without converting it into execution authority. Reset continues to
preserve/block uncertain effects for either kind; it must not hide work holds.
No existing row is migrated or rewritten. The workflow adapter will supply the
actual admitted receipt, never a caller's synthetic chat request.

The lineage format is now implemented and tested on Windows and Linux (258
passes in each, zero skips). Evidence:
`docs/reviews/2026-09-14-work-agent-journal-proof.md`. Current-home checks and
all execution guards remain unchanged; this checkpoint grants no work authority.

Shared progress extraction now built locally: AgentTurnCoordinator plus the
served-chat adapter retain existing chat behavior, including original input
records and typed fallback refusal. Ten executable differential cases compare
the old coordinator with the new composition through real stores and router.
Evidence: docs/reviews/2026-09-14-shared-agent-coordinator-proof.md. This neither
implements the workflow adapter nor relaxes the pinned authorization refusals.

AGREE with the finding: current admission counts one inference per node attempt;
multi-round tools need an explicitly finite allowance under the same work cap.
DISAGREE_CONCERN with automatically adding a new workflow `max_agent_rounds=0`
knob as the only repair: this would keep existing workflows unable to use the
capability unless their authors changed them. For the existing immutable opt-in
agent-turn workflow contract, use the already authorized finite work/member
ceiling as its aggregate invocation allowance. The static compiler node/retry
term remains the minimum admission requirement and must fit that ceiling.
Prompt-only work retains exactly its old static term. Legacy run receipts use
their child binding's existing ceiling; manifest receipts use the minimum of
their accepted participating members' ceilings. Neither tokens nor money increase.
All rounds share that one receipt/claim and existing durable reservation counter;
there is no new per-node allowance, counter, store or workflow field. A native
agent that performs one invocation consumes one; engine inference consumes each
round. Exhaustion holds, never creates another receipt or retries an effect.

This uses `shared_self_requested` only to recognize the platform's existing
immutable agent-turn opt-in. It does not create or alter the user's background
project, enable unmarked nodes, change its existing single-prompt restriction or
add a new tool permission. Background receipts already derive a finite ceiling
from the remaining attempt/queue/member limits, so their arithmetic is unchanged.
Router permissions remain closed until the adapter/observer/replay fence is built.
This adaptation implements the review's finite aggregate invariant using existing
owner authority rather than its proposed new zero-default workflow field.

Full coordinator integration exposed a second R5 boundary: a one-node receipt
reserves its entire token/cost share again after spending seven tokens, so the
second reservation exceeds the aggregate by seven even though almost all budget
remains. Before arming an agent round, clip its requested token/cost share to the
remaining aggregate, using the exact existing reservation-charge function (known
actuals, unknown maxima, cancelled-before-launch zero). Keep ordinary calls
unchanged. Reapply model affordability after clipping. Receipt ceilings are no
larger than participating member ceilings; the existing atomic aggregate/member
checks remain authoritative. This neither grants fresh budget nor erases spend.

The allowance adaptation and private foreground between-step authority check are
now built. The check rereads live receipt/claim/member/parent (and legacy child),
current owner and immutable running subject without reserving or rearming. Its
adapter call sites remain pending. Windows/Linux group193/193 passes, zero skips;
provider-neutrality plus new files57 passes. Evidence:
`docs/reviews/2026-09-14-work-agent-admission-proof.md`.

R6 refinement from the actual compiler: `_call_policy_router_with_retry` catches
AllProvidersExhaustedError and starts the entire node again. Even a *known*
completed tool followed by later capacity exhaustion would be replayed by a new
turn. Therefore a work adapter must suppress whole-node retry after any dispatched
tool, not just an unknown effect. Safe no-effect capacity traversal remains inside
the same coordinator/history. A pre-intent failure can retain existing retry only
when no earlier action was dispatched. Cancellation must keep its original signal.

Foreground integration is now built with the separate work adapter and actual
pre-inference observer. HTTP tool/result/second-round execution and native
completion/capacity/uncertainty use work-owned journal roots. Prelaunch failures
release unused reservations, injected caller authority refuses, and effectful
or unknown failures cannot restart the node.292Windows/292Linux tests pass;
see docs/reviews/2026-09-14-foreground-work-agent-proof.md. Background integration
and work-owned within-turn safe fallback traversal remain open. The current
foreground adapter pins the initially resolved source/model for later rounds;
it does not fabricate a served-chat model plan for workflow authority.

Background implementation seams confirmed from current source: `_branch_roles`
already loads and validates the immutable version, but the background session
does not yet call `prepare_shared_self_turn`. Reuse that opt-in/persona path.
Every inference still needs `_authorize_launch`'s current task, activation,
attempt/member and consumer-lease checks; tool checks must additionally reject
`cancel_requested` rather than inheriting that launch helper's broader status
set. Existing `_background_receipt_authority` validates the receipt's attempt
and queue owner but is insufficient alone for activation and consumer identity.
Keep per-inference invocation indices monotonic even after a failed attempt;
the old session increments only after a successful provider call. Existing
background token/cost shares already divide the aggregate across its ceiling.

Background/native integration also exposes an accounting distinction: the old
router settles every typed capacity error at zero usage. A delegated work agent
may already have spent tokens before that error, even with no external effects.
Without observed usage totals, retain an indeterminate reservation and its
maximum charge, consistent with R5. No-effects evidence controls safe retry,
not accounting; permitted traversal still has to fit the remaining budget.
Do not infer zero usage from a capacity exception or an empty engine log.

Background integration and that accounting correction are now implemented.
The work adapter preserves its initial receipt/claim, while separate foreground
and queue sessions recheck their own execution authority. Background metadata
discovery and model/tools execute outside assignment locks. The existing attempt
ceiling, owner/admin, immutable subject, activation and consumer/task leases stay
authoritative at each step. Final accounting group211Windows/211Linux passes,
zero skips; broader initial integration323Windows/335Linux. Exact evidence and
the fixed obsolete persona unit seam are in
docs/reviews/2026-09-14-background-work-agent-proof.md. User acceptance and final
review remain open; no private workflow or user background project was edited.

## Order and completion

Implement native propagation with basis labels and tests; then owner-bound
enumeration. Implement shared progress with unchanged chat behavior and the
workflow adapter only after round allowance is resolved. Final approved review
is reserved for the complete tested candidate. Windows/Linux tests, CI, actual
deploy containment/canary, and ordinary rendered app/workflow proof remain open.

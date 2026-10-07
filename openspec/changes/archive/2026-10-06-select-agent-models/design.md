## Context

September15: automatic-model-bootstrap.md captures the founder-approved empty-
universe sign-in continuation through hosted OpenRouter signup/authorization,
free-only owner consent and generic additional-source request. Fable5.1 round2
ADAPT and lead corrections are recorded before code. This is the existing
unpowered slice, not permission for platform-created provider accounts or spending.

September15 reconnect supplement: `model-preserving-reconnect.md` specifies the
accepted-manifest preservation boundary before runtime edits. Four synthetic
tests reproduce legacy reconnect dropping the manifest/changing its root.

September14 next-slice supplement: `served-model-setup.md` records the Fable5.1
reviewed model-setup facade and owner-consent boundary before implementation.
The native-picker release candidate remains unchanged while its CI completes.

September 9 owner direction in PLAN and the provider-compatibility concern is
authoritative. The 11:39 PDT app reply proposes account-aware fallback but
explicitly says it has not inspected implementation. Existing serving authority
resolves exactly one binding; the open HTTP executor has no engine-tool loop.
Neither fact can be repaired by drawing a picker over today's name list.

## Goals / Non-Goals

September14 remaining execution decisions and Fable ADAPT dispositions are in
`remaining-model-execution.md`; read it before native/workflow tool integration.

**Goals:** actionable model choice, automatic suitable defaults, accepted ordered
fallbacks and honest actual-model receipts using the user's authorized compute.

**Non-goals:** an LLM choosing each next LLM, provider account creation, implicit
paid upgrades, workflow edits, a parallel credential/authority engine, or claiming
universal compatibility with arbitrary unknown executables.

## Decisions

September 11 scope correction: main-agent choice is not the only execution
scope. The owner explicitly requires independent agent/workflow/task choices.
The candidate currently breaks ordinary workflows after manifest admission;
workflow-selection-correction.md records the reproduced failure and the
per-invocation authority/aggregate-budget design gate. Do not remove the legacy
guard or change private pins to make a smoke test pass. This gate concerns
platform implementation only, never the cloud universe's own project workflow.

1. **Separate identity, policy and observation.** Keep connection/grant and
   content-addressed legacy provider definitions intact. Store a versioned
   owner/universe policy with generation, mode (automatic/explicit), selected
   connection/model and ordered accepted fallbacks. Choices reference a connection
   and opaque model identifier, not a process-global CLI instance. A turn captures
   its policy generation; changing preferences affects subsequent inference, not
   an already running call. Actual-attempt receipts are separate, tied to that
   turn. Do not activate previously ignored CLI model fields during migration.

2. **Catalogue and capability belong to the connection boundary.** A normalized
   connection-scoped snapshot includes source, freshness, opaque model id,
   modality/context/tool support, price constraints and availability evidence.
   Provider-specific discovery maps into this contract; the engine does not know
   vendor model names. Discovery uses the existing broker and explicitly granted
   endpoints, never arbitrary URLs from remote metadata or a broader credential.
   A stale cached catalogue is labelled, not portrayed as confirmed live access.
   Current authority is checked again before every attempt.

3. **Selection is deterministic without requiring powered intelligence.** Explicit
   current choice then saved default/order wins. Automatic mode first considers
   eligible connected subscription/local sources; within a connection use its
   default unless the user supplies a model. Rank remaining OpenRouter candidates
   within capabilities, privacy and permitted cost. Proposed ranking evidence is
   a fresh agentic benchmark, then general-intelligence evidence from the same
   comparable source; unknown scores are not invented. Keep an existing suitable
   choice stable on ties. All models remain visible with eligibility explanations.
   Ranking source/schema and stale-data behavior require the shape review below
   before implementation; larger context or newer names are not quality scores.

4. **Authority does not follow a name.** Reuse current owner-scoped work authority
   and assignment admission, extending resolution to an exact accepted candidate
   per attempt. Do not repeatedly mutate the universe's serving binding while
   traversing fallbacks, synthesize actors, widen an allowlist or borrow host
   registrations. A saved preference is not an inference grant. Preserve hard
   pins and explicit empty fallback sequences as fail-closed.

5. **Capacity and progress are scoped.** Track model-local versus connection/account
   exhaustion from typed adapter evidence and retry hints. An account-wide refusal
   skips sibling models sharing that proven account; an unknown account identity
   is not proof of independent capacity. Never cycle indefinitely through keys or
   model names. Persist completed tool results and resume only at a safe inference
   boundary; if a tool may have executed without a receipt, hold instead of replay.
   After exhaustion show waiting/retry information and existing connection requests.

6. **UI is usable unpowered.** A keyboard-accessible button near typed chat shows
   the current attempt or last reported answering provider/model, not the speech
   voice. It opens all available choices, automatic mode, a current-choice action,
   save-default action and ordered fallback controls. Use authenticated app state
   independent of `converse`; never require an LLM to repair its own routing.
   A configured alias with no model receipt is labelled unknown. Existing pins
   remain visible even if absent from a refreshed catalogue, with a reason.

## Source checks (September 9, documentation, not live capability proof)

- OpenRouter's [free router](https://openrouter.ai/docs/guides/routing/routers/free-router)
  randomly chooses compatible free models and returns the selected model. It is
  not a best-first ranking implementation.
- The [user model catalogue](https://openrouter.ai/docs/api/api-reference/models/list-models-filtered-by-user-provider-preferences-privacy-settings-and-guardrails)
  accounts for provider preferences, privacy and guardrails. The
  [benchmark endpoint](https://openrouter.ai/docs/api/api-reference/benchmarks/list-benchmarks)
  exposes source-labelled scores including agentic/general intelligence and
  freshness metadata. Verify actual schema and model-id correspondence before
  trusting the join; documentation samples are not live-account evidence.
- The [auto router](https://openrouter.ai/docs/guides/routing/routers/auto-router)
  uses task-specific spending rankings and cost bands. That is not automatically
  a free-only ordered policy. The
  [limit documentation](https://openrouter.ai/docs/api_reference/limits)
  distinguishes upstream capacity from platform limits and documents retry/reset
  signals. Keep those distinctions in the normalized adapter outcome.

## Risks / Trade-offs

- Missing benchmark coverage → explain unknown rank; preserve a working eligible
  default, allow explicit choice, do not claim a mathematically best model.
- Changed catalogue or withdrawn free tier → recheck eligible price/capabilities
  and enforce free-only dispatch, never silently choose a paid variant.
- Tool-incompatible HTTP path → do not mark full-agent readiness until the shared
  tool loop is implemented and tested. Text-only success is insufficient.
- Concurrent preference/revocation → generation checks plus existing authoritative
  admission; separate actual receipt avoids overwriting the saved preference.

## Migration Plan

Add owner-scoped policy without rewriting old definitions. Existing explicit
serving selections stay explicit; new automatic mode is opt-in for existing
universes and the default for new connections without a saved choice. Separate
receipt truth can ship first. Review the exact storage/API authority design, then
land tested runtime and UI together; no decorative enabled controls. Verify
deployment and normal rendered conversation. Rollback preserves saved policy and
receipts but disables new routing, retaining the prior explicit binding.

## Installed agent envelope boundary (September 11)

Canonical text/tool messages and captured completed history are validated without
an HTTP envelope. Installed AgentWireShape data owns the relative request path,
six top-level field mappings and reply/receipt selectors. The actual executor's
AgentCodec uses that adapter directly; historical function imports delegate only
for compatibility. Authority, source admission, tools and cost checks stay outside
translation. This internal boundary does not publish a new owner-authored wire
schema or promise unknown message formats/CLIs. Reviewed implementation and
frozen compatibility proof: docs/reviews/2026-09-11-agent-envelope-proof.md.

## Open Questions / pre-build gate

### Connection-authored discovery correction (September 11)

`discovery-contract-v1.md` proposes the bounded versioned extension to the
existing capability descriptor. It distinguishes an owner-accepted source
contract from independently verified availability, preserves legacy descriptors,
and reuses current grant, assignment, price and executor checks. Pre-build review
ADAPT122s required removing the digest-as-consent ceremony, closing accounting
through supported units, preserving legacy scalar/substring behavior and defining
conservative capacity scope. These corrections are incorporated; implementation
and exact-head review remain. This does not complete arbitrary CLI support.

### Reviewed pure policy kernel (September 9)

Implement the internal advisory ordering kernel first, with no API, storage,
clock, network, dispatch or credential imports. Independent Claude review ADAPT
and lead disposition are recorded in
`docs/reviews/2026-09-09-model-policy-shape-review.md`. The broad authority review
timed out without a verdict; public/storage/authority changes remain gated.

Inputs separately carry current selection, saved default and accepted fallbacks.
Current overrides saved default as primary, rather than implicitly adding the
saved default as a fallback. Empty accepted fallbacks are preserved. Automatic
mode uses one designated benchmark source, ranked and unranked buckets and stable
input order; the stable existing choice is a tie-breaker only. Model identifiers
are opaque. Freshness is computed upstream and explicitly supplied, not inferred
from transport or wall clock inside the kernel.

Connection snapshots include owner/universe scope, owner-filtered discovery,
executor tool support, trusted account-capacity identity, source kind and
advertised default. Tool-capable models on text-only executors are ineligible.
Automatic candidates require fresh capability/privacy evidence and, when
metered, complete confirmed pricing for every required component. Integer prices
use component-specific units shared with the explicit ceiling; no rounding may
turn a nonzero price into zero. Stale explicit choices are advisory and labelled
for refresh, not authorized by a saved preference. Known incompatibility or cost
violation still excludes them.

Account exhaustion excludes every proven sibling; unknown identity does not
justify switching keys within the same provider scope. Account/model duplicates
are removed after eligibility so an unusable first credential cannot suppress a
usable accepted one. Output carries generation, advisory references, basis and
reasons only; it contains no authority proof and cannot be passed as a grant.

The independently reviewed receipt slice exposed a downstream validation issue:
`AgentInvocationProviderOutcome` currently requires a nonempty successful model
field. For this slice, permit the exact empty string as unknown telemetry while
retaining all other field, digest, provider identity and settlement checks. No
table shape or authority changes; existing nonempty outcomes remain valid. Prove
successful storage/read/replay and once-only settlement with omitted model
metadata. Reject invalid non-string or whitespace-only values as before. This
bounded adaptation is required before the HTTP receipt correction can ship.

Resolve and independently review: exact authority-store transaction/API seam;
HTTP tool-loop dependency and safe continuation; model-catalogue grants for
existing connections; benchmark coverage/matching/freshness; authenticated
account identity and shared cooldown storage. These are engineering work, not
requests for the owner to supply a model ranking or repair workflows.

## Durable answering-receipt follow-up (September 14)

The request-owned display receipt now needs to survive transcript reload. The
reviewed additive storage/projection design and Fable pre-code dispositions are
in [persist-answer-receipts.md](persist-answer-receipts.md). It uses the existing
conversation store, preserves legacy read-only history, and introduces no new
authority or model-selection input. This is separate from PR3844's setup tools.

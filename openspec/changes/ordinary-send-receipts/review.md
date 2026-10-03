# Initial independent design review — ADAPT

Reviewed exact design: `21ddb02f24ced1cdbb8bb2e6290ad5e4fdcfa7cb`.
Main baseline: `8a8ec275`. Held #4308: `cedc4f6dff8849051042de08eafba425141138fe`,
inspected by Git object only. Independent same-family reviewer
`mobile_exact_head_review`; cross-family review remains outstanding.

This is the historical first-round result, not a verdict on the revised design.
The initial implementation gate did not pass. The author agreed with all four P1 findings.
No runtime/schema code was written; this record preserves the failed gate rather
than implying that documenting an invariant implements or proves it.

## P1: Complete durable input custody

AGREE. Cover claim, take_carryover, settle AND open_turn's stale-live cleanup,
as well as binding before enqueue/take exposes new inputs to the model. Before
an input leaves the durable queue or reaches a model, retain immutable text, exact
steer ID, owner/home/agent and root key/receipt binding. Delivery-attempt state
survives crash; uncertain delivery never becomes auto-claimable carryover. Every
cleanup keeps custody until exact durable settlement permits its release.

Hermetic source execution in a temporary data root confirmed:

| Committed boundary, before the next durable step | Stored input remaining |
|---|---|
| hold -> claim; before client converse | no pending row |
| hold -> take_carryover; before provider | no pending row |
| open/enqueue/take -> settle; before history | zero steer rows |
| open/enqueue/take -> new open_turn after stale live; before terminal/history | original row deleted |

These are characterization proofs of existing gaps, not tests of a proposed fix.
Only synthetic text and temporary stores were used. No provider/effect executed.

## P1: Preclaim versus admission winner

AGREE. A claim that reserves the author receipt prevents the later converse from
being the insertion winner. Choose a complete integration-lead protocol: fold
custody into initial keyed admission, OR keep preclaim non-executable and bound to
scope/key/payload/IDs so only the first converse insertion attaches it and starts.
Retries observe custody; orphan custody holds. A prepared-to-started CAS would be
a different reviewed protocol, not an implicit exception to the current design.

## P1: Schema loss/reset cannot erase replay protection

AGREE. Read-side unknown alone is insufficient. Existing journal connection
helpers can create schemas, so admission needs an explicit distinction between
first initialization and unexpected loss. It must fail closed on missing replay
history; reset/compaction must retain tombstones or permanently retire the request
namespace. Deletion/reset serialization must include custody and projection.
Integration-lead ownership and the concrete initialization protocol are unresolved.

## P1: Ordinary identity survives consumer-selection changes

AGREE. Lookup an existing exact ordinary key BEFORE dynamic consumer negotiation.
A completed ordinary send with a lost reply cannot become a new consumer-key send
after the selected conversation changes. Accepted keys remain observation-only;
unknown ordinary admission never permits conversion into a fresh consumer intent.
This ordering corrects design.md's incomplete route-before-reservation wording.

## Fence/dependency boundary

#4308's author-journal fence is necessary, not cross-database atomicity. Steering,
conversation and deletion writers remain listed as unfenced-before-C2 there;
HANDOVER_ENABLED remains false. Do not infer that B1 makes their writes safe under
multiple owners. The parent must assign the custody/reset changes and approve the
fenced baseline/dependency before this lane can implement a coherent patch.
The observational branch remains frozen at `cf5ff2a5`. No further PR creation
attempt is permitted from this lane after its two timeouts.


## Revised submission — pending independent review

The parent subsequently assigned custody and narrowly necessary lifecycle design
to this lane and instructed use of current main, without a #4308 dependency.
The earlier dependency statement above is superseded. No runtime files changed.

- Custody: retains exact rows through all six steering APIs and stale cleanup;
  attempted inputs freeze rather than delete/requeue; history acknowledgement
  alone enables exact cleanup.
- Admission: server-issued PREPARING/PREPARED receipts are non-executable;
  one guarded PREPARED -> STARTED transition admits the live winner.
- Schema/reset: supplied IDs are always lookup-only, even after erasure and
  reinitialization. Current-boot restriction rejects restored old preparations.
  Content erasure needs no persistent content-bearing replay tombstone.
- Routing: any ordinary envelope resolves before dynamic consumer negotiation;
  accepted/missing ordinary IDs cannot be converted into fresh consumer requests.

Executable evidence: `python openspec/changes/ordinary-send-receipts/proofs/protocol.py`
passes 19 unittest cases using three actual separate SQLite files in temporary
roots. Includes concurrent start, claim conflict, preparation/admission/delivery/
projection crash gaps, enqueue/freeze and enqueue/tombstone races, owner/home/agent
isolation, missing schema, explicit reset, old boot, read-only and owner erasure.
Ruff and diff whitespace checks pass; OpenSpec check-change reports ALLOWED.
This is a design model, not production/provider/Android proof. Production path
validation, migration inventory and private former-home directory deletion remain
implementation obligations. Account-deletion changes need independent privacy
review and parent coordination before implementation. Cross-family review remains
unavailable in this environment; independent same-family review is requested.

## Revised independent review — ADAPT

Reviewed exact `e55431cec4917784cef5edf4b365c1557042bf04` independently by
`mobile_exact_head_review` (same family). Reviewer reran all 19 model tests.
Original four P1s are closed at design level, not runtime implementation level.
Deletion/privacy direction is accepted with no additional design finding; actual
lifecycle changes still require parent coordination and production verification.

### New P1: actual steering delivery crosses process boundaries

AGREE. `engine_steering._take` calls `agent_steering.take` in the separately
supervised engine process (`engine_mcp_http._EngineServer`). Each process creates
its own BOOT.boot_id. The model's `running` equality to the serving boot therefore
rejects legitimate engine delivery if translated literally. Copying a receipt's
boot ID into the call does not prove the issuing serving process remains current.

Before implementation, specify either delivery through the admitting process or
an exact current-incarnation observation contract under existing single-writer
assumptions. Keep existing engine serving-owner/scope checks; supplied receipt or
boot IDs must never grant authority. Add a model with distinct serving/engine
identities: valid delivery succeeds, serving retirement refuses stale delivery,
and neither path grants another start. Explicitly include the engine boundary in
the coordinated implementation surface. Do not silently drop the guard or invent
an unreviewed authority writer, route, capability or provider/effect modification.

This is a substantive process/security extension beyond the submitted surface.
Hand off this finding for parent coordination; no runtime changes, new credentials,
live migrations, or additional PR attempts have occurred. Revised implementation
gate remains BLOCKED. The observational repair stays frozen at `cf5ff2a5`.


## Cross-process amendment submission — pending independent review

Parent authorized proposed interface design and requested exact additional files
before implementation. Section 7 now uses existing process_liveness of the stored
original issuer, preserving real engine scope/session/live delegation. No use of
engine BOOT as serving BOOT, new authority/credentials, or handover. Only proposed
additional runtime file is engine_steering.py plus packaged mirror; new focused
integration tests are named there. No runtime file changed.

The original 19 model cases and four new actual-process synthetic cases pass.
The latter imports existing process_liveness and proves legitimate delivery from
a distinct BOOT, post-SIGKILL/closed/missing-proof refusal and no second start.
It does not prove production routing/ACL wiring or instantaneous crash fencing.

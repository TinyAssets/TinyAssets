# Independent design review — ADAPT

Reviewed exact design: `21ddb02f24ced1cdbb8bb2e6290ad5e4fdcfa7cb`.
Main baseline: `8a8ec275`. Held #4308: `cedc4f6dff8849051042de08eafba425141138fe`,
inspected by Git object only. Independent same-family reviewer
`mobile_exact_head_review`; cross-family review remains outstanding.

Implementation gate does not pass. The author agrees with all four P1 findings.
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

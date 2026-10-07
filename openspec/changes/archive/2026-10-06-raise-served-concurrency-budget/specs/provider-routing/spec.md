# provider-routing (delta)

## ADDED Requirements

### Requirement: Served-provider budget admission bounds concurrency, not lifetime spend

The served-provider budget reservation for a converse turn SHALL request a
BOUNDED per-call output allotment, decoupled from the binding's aggregate
in-flight ceiling: when the caller sets no explicit `max_tokens`, the router
SHALL reserve a bounded per-call default (capped to the ceiling), never the
whole ceiling. The aggregate ceiling SHALL bound only UNSETTLED (in-flight)
reserved token/cost spend, so a settled turn releases its reservation. The
ceiling is a concurrency runaway backstop, NOT a cumulative spend limit — real
spend is metered on the user's own deposited subscription upstream, and the
process-wide provider worker pool is the actual provider-execution concurrency
control. The ceiling SHALL be sized so that a single user driving their universe
from multiple surfaces at once, alongside concurrent LangGraph automations, is
not falsely held. Each universe's binding has its own independent budget ledger,
so concurrent users do not contend at the budget layer.

#### Scenario: A converse turn reserves a bounded per-call amount, not the ceiling

- **GIVEN** a ready serving binding whose aggregate ceiling is far larger than
  one turn's output
- **WHEN** a converse turn is routed with no explicit `max_tokens`
- **THEN** the reservation's output allotment is the bounded per-call default,
  not the aggregate ceiling

#### Scenario: A stale-low binding is healed by re-bind, not by an admission override

- **GIVEN** a serving binding whose persisted (digest-covered) ceiling DIFFERS
  from current policy — stale-low (bound before a raise) or stale-high (policy
  since tightened)
- **WHEN** the binding owner re-binds the same provider
- **THEN** the re-bind does NOT replay the stale binding (replay requires EXACT
  policy equality); it advances the generation/digest and persists the current
  ceiling, re-signing the authority — healing up or reflowing down as policy dictates
- **AND** admission NEVER expands a reservation above the binding's own stored,
  digest-covered ceiling (no admission-time floor) — the authority contract holds

#### Scenario: Many concurrent turns across surfaces on one binding

- **GIVEN** a ready serving binding for a universe
- **AND** ten simultaneous served turns are in flight for that binding, each
  reserving a realistic (~20 KB system+prompt) amount of budget
- **WHEN** each turn reserves served-provider budget before launch
- **THEN** all ten reservations are admitted (none is held with
  "Provider authority budget is exhausted")
- **AND** the rolling per-hour invocation cap and the user's metered
  subscription remain the effective runaway/spend backstops

#### Scenario: Runaway invocation guard still fires

- **GIVEN** a serving binding whose rolling-window invocation count has reached
  its per-hour cap
- **WHEN** another served turn attempts to reserve budget
- **THEN** the reservation is held (the invocation runaway guard is unchanged by
  the concurrency-sized token ceiling)

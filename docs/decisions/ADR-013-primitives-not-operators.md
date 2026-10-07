# ADR-013: Ship Primitives the Agent Composes, Not Operators

## Status

Accepted

## Date

2026-08-30 (irreducibility rule 2026-07-25)

## Context

On 2026-08-29/30 four deploys added `$ta.*` body-transform operators to change
one line of a fetched file, because nothing deterministic could run between a
fetch and a write. Each live failure invited one more special case.

## Decision

When a live failure suggests "add an operator or a special case", ask which
primitive is missing that would let the user's agent solve it itself. That
primitive ships; the operator does not.

- A new top-level primitive (a new MCP handle or substrate concept) ships only
  on a recorded irreducibility finding: the behaviour has essentially one
  useful shape. Anything with many plausible shapes is user-buildable and
  belongs to the commons. Otherwise new behaviour lands as actions and
  parameters under the existing canonical handles.
- Effects fire at node time in graph order; a refused or failed write fails the
  node, and later nodes can read earlier responses.
- A sandboxed code node runs deterministic Python with the node's data and
  every ancestor's response, with no credentials and no network.
- The `$ta.*` vocabulary is frozen.
- Test through the app agent as a user would. Never feed it an answer it
  should work out, and never build or edit a user's workflow yourself; enable
  the agent instead. A missing basic capability stays listed until fixed.
- Generator, evaluator and ground truth stay separate channels. Every
  scaffold (counter, threshold, gate, routing rule) is a falsifiable
  hypothesis: prove the simpler approach fails before adding one.
- There is no structural cap on graph size. A big graph is bounded by usage
  (admissions, budget, consent, sandbox limits), never by its shape.

## Consequences

- As-built: `openspec/specs/graph-execution-substrate/` and
  `openspec/specs/live-mcp-connector-surface/` (the canonical handle set,
  asserted by `scripts/mcp_public_canary.py --assert-handles`); change
  `openspec/changes/archive/2026-08-30-sandboxed-code-node/`.
- Next primitive in flight: `openspec/changes/workspace-node/`.

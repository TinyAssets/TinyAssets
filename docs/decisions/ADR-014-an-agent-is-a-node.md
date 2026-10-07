# ADR-014: An Agent Is a Node

## Status

Accepted

## Date

2026-09-27

## Context

Custom agents had their own compiler, provider loop and grant model
(`agent_runtime_*`) beside the graph runtime, duplicating what a chat turn
already does.

## Decision

A prompt node whose `tools_allowed` holds `agent` runs the same turn `converse`
runs: the persona and brain, the shared agent loop, the engine tools pinned to
the run's own command center and owner, and the owner's model preferences, with
the node's `llm_policy` as a per-node override. It runs as one workflow step
until the turn finishes and writes its answer to graph state. The rest of
`tools_allowed` is the owner's grant: the marker alone means everything the
owner's chat has, and listed tool names narrow the node to exactly those. Each
round is metered against the run's existing work receipt; no cap is added. A
custom agent is a stored configuration of an agent node, shared and remixed as
a branch. A run refuses a branch another user authored.

## Consequences

- The `agent_runtime_*` second compiler, provider loop and grant model are
  redundant and are removed in a later lane.
- As-built: `openspec/specs/agent-node/`; change
  `openspec/changes/archive/2026-09-28-agent-node-and-tool-grants/`.

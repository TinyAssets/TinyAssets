## Context

The existing owner event automation and native agent prompt node already provide scheduling and owner/provider admission. A room adapter provides append-only messages, atomic local files and idempotent replies.

## Goals / Non-Goals

Build an editable owner workflow, not a platform feature editor or new agent runtime. No live activation, subscriptions or model spending occur when the skill is shipped.

## Decisions

The builder accepts owner-selected center, source workflow, room identity and adapter source. Claim reads the actual completed source run through existing read_graph authority before accepting its receipt. Under a durable lock it selects explicitly addressed root owner messages, reserves a turn and marks generation uncertain before the native node. A saved result is committed with one stable message ID. A generation never times out into a retry. Interrupted runs are terminal in the existing engine; explicit replay starts at the claim guard.

The graph routes unknown/empty route values to stop; only generate enters one native main-agent node, with read-only tools,120-second node timeout and no node retry. Saved results route directly to commit. Replies request no further responders. Owner revocation closes the ledger and marks in-flight claims revoked; current platform owner/provider admission still applies on each action.

## Risks / Trade-offs

The room adapter is trusted owner code with documented atomicity/idempotence requirements. No claimed authority comes from a participant name or claim token. Normal native-agent context includes owner conversation context; this is not a room-only model-context guarantee. One native turn can include several provider requests. Default activation limits one turn total and per UTC day, but is not a new currency spending cap. Existing provider budget controls apply; activation requires separate approval.

## Verification

Fixture tests cover concurrent claims, actual-result commits, crash boundaries, revocation, quotas, malformed output, source checks and graph routes. No private identifiers or production data enter tests/review.

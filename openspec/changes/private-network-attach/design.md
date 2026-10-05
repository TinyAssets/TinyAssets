## Context

Sources: [Muse connection methods](../../../docs/design-notes/2026-10-04-muse-connection-methods.md) and [orchestration analysis](../../../docs/design-notes/2026-10-04-agent-orchestration-comparative-analysis.md). Their evidence labels/caveats remain authoritative for attribution; this is a proposed TinyAssets contract, not a verified claim of existing functionality.

## Goals / Non-Goals

Private and self-hosted APIs/MCP servers need a generic owner-network route without exposing platform infrastructure or requiring a device app. connect-anything-ladder owns MCP/secret custody; inline-connect-and-approve owns the sole connect card and protected decisions; existing egress broker owns destination enforcement. This changes no public top-level MCP handle. No platform LLM or provider-specific compute/integration path.

## Decisions

Persist network configuration as a typed child of the existing connection incarnation: public overlay configuration, opaque custody reference, permitted host/service predicates and current session generation. Reuse protocol-compatible overlay clients as extensions/connection data (WireGuard/Tailscale-compatible configuration), never provider-specific daemon code. Keep overlay credentials in an isolated broker-side helper, not the agent sandbox. An outbound-only tunnel terminates in that owner's isolated network context; no founder machine or shared host joins an owner's network.

A route is not permission. Initial contact with each private host creates an exact-host protected request; the resulting grant pins network incarnation, host identity/resolved destination, service/port and action classes. Re-resolve and enforce at dispatch and on redirects. DNS rebinding, changed host identity or address outside approved overlay routes needs new validation/approval. Never route platform metadata, control-plane, loopback or another owner's namespace through a private-route exception. Public egress SSRF protections remain in force outside that explicit route.

Disconnect first fences routes, calls and MCP sessions, then closes the tunnel and removes staged custody durably. Reconnect gets a new incarnation and cannot reuse grants. Report offline/unsupported routes truthfully; no public-network or host-process fallback. Existing workspace-node/connect-cross-user-nodes retain compute enrollment: this adds reachability only, not enrollment or compute authority.

## Migration Plan

Extend existing versioned records and preserve prior data/history. Negotiate unsupported versions visibly before dispatch. Roll out after prerequisites pass; on rollback disable new dispatch, retain records and revocation/recovery controls, and never reactivate stale authority or erase user data.

## Risks / Trade-offs

External availability and partial failures can leave work held: expose current state and receipts; do not invent success. New handles can become confused deputies: resolve authenticated bindings at every dispatch and fence stale generations.

## Acceptance criteria

Connect a private MCP endpoint with no service-specific code; reject another owner, metadata routing and DNS rebinding; revoke during a live call. This also supports the Home Assistant reserve test in the orchestration analysis; it does not claim local devices stay online without their owner.

Implementation verification includes affected tests/heavy files, Ruff, strict OpenSpec validation, floor review, deployed-SHA assertion, real-user rendered proof and spec sync. Sandbox/process/network enforcement must pass the Linux oracle; a skip is not acceptance.

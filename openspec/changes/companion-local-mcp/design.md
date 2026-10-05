## Context

Sources: [Muse connection methods](../../../docs/design-notes/2026-10-04-muse-connection-methods.md) and [orchestration analysis](../../../docs/design-notes/2026-10-04-agent-orchestration-comparative-analysis.md). Their evidence labels/caveats remain authoritative for attribution; this is a proposed TinyAssets contract, not a verified claim of existing functionality.

## Goals / Non-Goals

Desktop and phone capabilities need one user-owned local MCP device shape rather than platform-specific integrations in the cloud daemon. connect-anything-ladder supplies attach/discovery; private-network-attach provides optional private reachability; workspace-node owns existing node identity/enrollment; inline-connect-and-approve supplies decisions. No cloud uptime dependency on a companion. No platform LLM or provider-specific compute/integration path.

## Decisions

Reuse device/node identity and connection incarnation rather than another device registry. Pair through protected first-party owner confirmation, mint a device-bound revocable credential held by the companion's OS keystore, and bind the outbound authenticated tunnel to owner/device/incarnation. Cloud tools reach local MCP through the same attachment/ta dispatcher; devices need no inbound public port. Desktop first supports scoped files, commands and screenshot/click where the OS permits. Phone follows with supported contacts, notifications, location or messaging abilities; do not claim iOS/Android background or SMS/health access where the OS denies it.

Per-capability Off / Read / Read+interact controls default Off and are intersected with OS grants and current owner/agent connection policy. An OS permission never creates cloud authority. Local paths, window capture and commands remain scoped to the selected device; a broader tool cannot escape a narrower granted operation. Device OS prompts and sensitive credential entry remain local protected interactions, not agent-authored approval chrome. No raw reusable credentials flow to agent context.

Revocation, logout/unpair and lost-device controls fence tunnel/tool dispatch before cleanup. Re-pair creates new credentials/incarnation; old handles cannot reconnect. A sleeping/offline phone returns unavailable and holds work visibly; it does not fabricate local execution or redirect onto the founder's desktop. Resident cloud work continues independently. Package exports include logical local-capability slots only, never device credentials, local paths/data or active grants.

## Migration Plan

Extend existing versioned records and preserve prior data/history. Negotiate unsupported versions visibly before dispatch. Roll out after prerequisites pass; on rollback disable new dispatch, retain records and revocation/recovery controls, and never reactivate stale authority or erase user data.

## Risks / Trade-offs

External availability and partial failures can leave work held: expose current state and receipts; do not invent success. New handles can become confused deputies: resolve authenticated bindings at every dispatch and fence stale generations.

## Acceptance criteria

Pair two separately owned desktop devices, prove cross-owner refusal and Off/Read/Interact distinctions, then demonstrate one supported phone capability plus an OS-denied one. Prove offline/awake/unpair behavior without routing cloud jobs to either device.

Implementation verification includes affected tests/heavy files, Ruff, strict OpenSpec validation, floor review, deployed-SHA assertion, real-user rendered proof and spec sync. Sandbox/process/network enforcement must pass the Linux oracle; a skip is not acceptance.

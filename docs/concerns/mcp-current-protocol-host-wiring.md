---
severity: P2
title: Remote MCP protected host continuation and streaming integration
filed: '2026-10-09'
summary: The July adapter is ready for a trusted host callback, but the isolation-owned extension spawn site still uses a collector and lacks protected URL consent and durable wake wiring.
---

# Remote MCP host continuation and streaming integration

Slice 3 adds the July protocol adapter and a trusted `RemoteMcp(elicit_url=...)`
callback. Its pending continuation lives only in the original coroutine, bound
to the instance's principal/connection/incarnation and operation. No resume
token or opaque requestState is returned to the model.

`extension_remote.invoke` currently creates a fresh client without that callback
and uses the collecting `EffectorTransport`. Therefore the live extension path
does not yet offer URL elicitation or durable continuation, and cancelling its
coroutine cannot close an already-running upstream collector. Do not claim the
one-tap live experience or restart recovery from the adapter tests.

The isolation/card integration lanes must install the protected host callback,
tie cancellation/account changes and the existing durable wake to the pending
operation, and supply the existing async broker streaming API through governed
effect admission. The host must show the full URL and requesting server before
consent, open an isolated external authentication surface, and never fetch or
expose page contents to the agent. Changed URLs require new consent.

No broker API change is required by the adapter. Isolation-owned broker files,
outbound connection storage and spawn sites were deliberately not edited.
Delete this concern after a deployed-SHA assertion and a naive-user app-agent
connect/cancel/revoke pass prove the integrated path.

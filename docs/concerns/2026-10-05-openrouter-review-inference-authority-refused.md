---
severity: P2
title: tiny's OpenRouter review call is refused with "inference usage authority refused"
filed: '2026-10-05'
summary: the GPT review tiny runs through its OpenRouter connection fails with ProviderAuthorityHeldError; the Codex review works
---

# OpenRouter review refused for inference authority

tiny reported on 2026-10-05 that its GPT review through the founder's OpenRouter
HTTP connection is refused with "inference usage authority refused". That
message is raised as `ProviderAuthorityHeldError` in the broker dispatch path
(`tinyassets/storage/outbound_connections.py` near the `ProviderAuthorityHeldError`
handler; `tinyassets/broker/client.py:51`). The Codex review path works.

## L2 investigation

The generic message loses the original reason. `resolve_inference_usage` in
`tinyassets/storage/agent_request_usage.py` rejects an HTTP POST without a parent
usage reference when the connection has model_use/model_discovery capability or
an owner-bound registered HTTP provider definition. This happens after grant
resolution and before credentials or network dispatch. The ordinary direct
connection/effect path supplies no usage envelope, whereas the model router and
`api_key_http_provider` reserve and forward one. Extending an HTTP grant cannot
repair this accounting refusal; no owner approval may bypass it.

L2 reproduces this path against the real ledger and both private IPC transports,
then proves an accounted call on the same grant succeeds. The change preserves
the guard and carries `InferenceUsageRequired` with fixed recovery instructions
instead of the generic dead end. The on-demand connections handbook directs an
agent to a prompt-template review node through run_graph and, only when model
access is absent, the existing fieldless `bind_model_access` owner ask (or a
model-use connect ask when the connection itself lacks that use).

The historical founder call has not been replayed against production, so this
is a reproduced cause consistent with the report, not proof of that specific
call's private state. Other invalid/closed-reference checks can also produce the
old generic refusal. Keep this concern until the deployed agent completes the
founder's review through the app with its intended approved model. No user
workflow or production grant was changed by this lane.

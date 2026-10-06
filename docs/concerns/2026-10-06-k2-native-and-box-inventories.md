---
severity: P1
title: K2 requires one shared agent contract across all executors
filed: '2026-10-06'
summary: Founder rejected separate provider inventories; native execution and remote ta need a shared contract coordinated with wf-orphan before parity can be asserted.
---

K2 draft PR #4517 must not be merged/deployed as a completed four-tool cutover.
The founder explicitly requires no starter capability loss. Claude's required
cross-family review returned ADAPT and independently confirmed these blockers.

`tinyassets/providers/codex_provider.py` enables cached web search in served
launches. Installed codex-cli 0.160.0 also enables native feature tools; the
engine's `enabled_tools` allowlist restricts only its MCP server. No supported,
verified configuration was found that removes every native tool (especially
apply_patch) while retaining owner-authorized native inference. Do not silently
substitute another provider or disable the owner's native agent to pass a test.
An actual upstream request/tool-inventory capture is required before claiming
exactly four tools or the whole native payload fits the stock-core budget.
The platform-supplied prompt plus real MCP schema tests are useful, but are not
that evidence. Official config reference:
https://developers.openai.com/codex/config-reference .

`tinyassets/agent_loop/served_chat.py` remains an opt-in remote box path with the
backend engine inventory plus history/activity reads. Its box-exec protocol has
no authenticated ta broker in the remote box. Filtering that list now would
strand capabilities. Build/prove the owner/center/turn-bound bridge before
projecting this adapter to four tools, including cancellation and lost replies.
Do not silently fall back to a different execution surface.

Native apply_patch also bypasses the new cooperative seed boundary; the seed
API preserves observed divergence but cannot promise atomic exclusion from an
uncooperative native writer. This blocks the all-adapter activation claim.

Remaining acceptance: real native inventories/envelopes, remote-box bridge,
paired model-family memory trials, deployed SHA, and a real owner app pass.
Public tinyassets.io/mcp handles are outside this lane and unchanged.

## Revised direction and mandatory handoff

Founder direction 2026-10-06 supersedes the separate-inventory framing above:
one agent definition supplies tools, instructions and capabilities to every
adapter and to the main agent and other agents alike. Per-provider inventories
are diagnostic evidence of divergence, never acceptable product definitions.

The native and remote-bridge findings have recurred. Under AGENTS.md loop rule 7,
the continuation handed them to Claude through `peer-agents`, rather than
patching individual production providers. The 103-second read-only review
returned ADAPT, confirmed the prior fixes and both outstanding blockers, and
identified overlap with wf-orphan's executor work. Reconcile the shared executor
contract with that lane before K2 changes it; do not disable native inference or
substitute a provider to manufacture parity.

The local diagnostic `scripts/native_cli_payload.py` captures credential-free
requests to a loopback sink (nothing forwarded upstream), or enumerates all
registered HTTP dialect encoders. It is not the production native launch:
its MCP server uses stdio instead of the production authenticated HTTP route.
Codex's missing MCP definitions in this probe therefore do NOT establish missing
production MCP tools. Extra native tools in the captured request do establish
that this local launch is not a four-tool agent. Tool supersets, missing MCP
tools and stock-budget overflows return a nonzero status. Every native row
explicitly says `production_parity_proven: false`.

No all-provider parity guard or remote-box cross-owner proof was completed by
this continuation. Existing ta jail isolation tests are not a remote-box proof.
No paired live trials or executable live-trial harness was completed. These
requirements remain open, and #4517 must remain draft.

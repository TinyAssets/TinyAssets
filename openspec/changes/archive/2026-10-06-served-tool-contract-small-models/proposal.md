## Why

On 2026-09-30 a free account on OpenRouter `:free` models (about fifty requests a day for the whole account) spent 21 model calls on one message, "make a morning note workflow". Most of them went on our own tool contract. `write_graph` only accepted `payload_json` as a string, so the model hand-escaped a `prompt_template` and got it wrong six times. Each failure came back as a refusal marked `isError: false`, which reads as success. A list of nodes with no edges was refused as "not reachable", and a running run had to be polled once per model request.

## What Changes

- The served engine `write_graph payload_json` and `run_graph inputs_json` accept the JSON value itself (an object or an array) as well as its text. One level of double encoding is unwrapped. Handlers still parse one text form, so there is still one representation and one validator.
- Every served engine refusal is returned with `isError: true`, and its text is unchanged. A refusal is an object with a truthy `error`/`errors` and no `status`, or a `status` of `rejected`/`refused`/`error`. A successful read of a failed run is not a refusal.
- A string that fails to parse also says to pass the object instead.
- If branch create gets several nodes with no edges or conditional edges at all, and no entry point other than the first node, the nodes are chained in the order listed. A notice says so. If the author supplies any wiring, it is validated exactly as written.
- Served `read_graph target=run` waits up to 10 s for a queued or running run to settle before it answers.
- The daily-quota classifier no longer treats a message that names a shorter window ("per minute ... daily quota remaining") as a daily refusal. Before this fix such a message cooled the whole source and dropped a working sibling model.
- The public connector (`https://tinyassets.io/mcp`) is unchanged: its handles, argument types and descriptions stay as they are.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `live-mcp-connector-surface`: this change updates the served engine argument types, the refusal error flag, the chaining default, and the bounded run read.

## Impact

`tinyassets/engine_mcp_server.py` (two argument types, one middleware, one run-read helper, handbook text) `tinyassets/api/branches.py` (the chaining default in staging), and `tinyassets/providers/daily_quota.py` with its shapes file (the shorter-window guard). The daily-cap stop that was also requested already landed in #4137. This change adds tests for it and the shorter-window guard. No new handle, no new target, and no change to authority.

Owner: Claude Code. Branch: tool-friction. Delivery: one PR; after merge, a live free-account "make me a morning note" round count.

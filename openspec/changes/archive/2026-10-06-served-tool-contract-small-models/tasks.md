## 1. Implement

- [x] 1.1 Accept the JSON value or its text for served `write_graph payload_json` / `run_graph inputs_json`; unwrap one level of double encoding.
- [x] 1.2 Flag every served refusal `isError: true` in one middleware outside the result ceiling.
- [x] 1.3 Chain an unwired node list in listed order, with a notice; validate any author wiring as written.
- [x] 1.4 Bounded wait on served `read_graph target=run` while a run is queued/running.

## 2. Verify and deliver

- [x] 2.1 Replay the live payloads through a real MCP client session; test the daily cap through the real router with OpenRouter's 429 shape. Mutation-check each guard.
- [x] 2.2 Sync the spec, then open one PR without auto-merge.
- [ ] 2.3 After merge, assert the deployed SHA, re-run "make me a morning note" on the free account and count the rounds, then archive.

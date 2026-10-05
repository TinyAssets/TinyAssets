---
severity: note
title: Local Windows MCP tool-description budget exceeds the ratchet
filed: '2026-10-05'
summary: test_engine_tool_description_budget_does_not_grow measures 33,373 chars vs 30,100 on Windows only; the Linux CI shard passes
---

# Local Windows MCP description budget exceeds the existing ratchet

Observed while verifying PR #4489 on 2026-10-05: tests/test_converse_turn_cost.py::test_engine_tool_description_budget_does_not_grow measures 33,373 characters against the 30,100 limit (write_graph: 13,935). Reproduces in an isolated pytest process with origin/main universe_tools source; engine_mcp_server.py is unchanged. The Linux CI shard passed this test (its sole failure was the separately fixed folder harness budget). Investigate dependency/runtime differences; no budget increase, skip, or weakening made in the chat lane.

# Tasks: remove-non-usage-limits

## 1. Account-shaped limits (PR 1)
- [ ] 1.1 Any user may create any number of universes; keep only the identity floor (`universe_server.py`, `api/prompts.py`).
- [ ] 1.2 Conversation history is never deleted (`conversation_store.py`, `storage/conversation_run_admissions.py`).
- [ ] 1.3 Storage-shaped caps go — project memory 1 MB, daemon wiki caps + eviction, app-UI-library 4 MiB (`memory/project.py`, `daemon_memory.py`, `custom_agents.py`).
- [ ] 1.4 Structural caps go — `MAX_PENDING`, `MAX_LINEAGE_DEPTH` + CHECK, `MAX_NOT_BEFORE`, consumer-binding 100 cutoff + `LIST_LIMIT` saturation, `MAX_SKILLS` silent drop, `MAX_DEFINITION_BYTES`/`MAX_OPERATIONS_PER_BATCH`.
- [ ] 1.5 Receiver per-sender rate becomes owner-set, default none, no ceiling (`storage/receiver_links.py`, `api/deliveries.py`, `storage/deliveries.py`).

## 2. Run and host bounds (PR 2)
- [ ] 2.1 Per-run and per-node counters go; per-call payload bounds stay (`effectors/__init__.py`, `node_sandbox.py`).
- [ ] 2.2 No recursion ceiling and no validated range; author retry policy honoured (`runs.py`, `graph_compiler.py`).
- [ ] 2.3 Wall clocks that capped a turn go; per-call transport timeouts audited and kept (`universe_intelligence.py`, `automations.py`).
- [ ] 2.4 Host safety waits — `provider_admission`, universe-tool jail slots, nested invoke, workspace job lock.
- [ ] 2.5 Inbound keeps per-token anti-flood only; per-universe rate/in-flight go and runs queue (`webhook_inbound.py`). Voice per-user windows go.
- [ ] 2.6 Serving-binding hourly/token/cost windows go; dark `_HTTP_ACTION_CAP` and market `_MAX_FANOUT` deleted after confirming no live caller.

  HTTP portion completed by PR #4476: new HTTP grants are uncapped, legacy
  `http_requests` caps are cleared on ledger open, and listings return `null`.
  Preservation, concurrent-open, and initialized-open locking regressions are
  covered; as-built contract is in `http-connections-and-outbound-authority`.
  Serving-binding windows and market fanout remain in this task.

## 3. Text, docs, specs (PR 3)
- [ ] 3.1 `get_status` `resource_usage` publishes storage and seats only (`api/resource_usage.py`).
- [ ] 3.2 Served handbook, connector docs and compiler guidance describe two dimensions (`engine_mcp_server.py`, `universe_server.py`, `graph_compiler.py`).
- [ ] 3.3 Website plan table, onboarding and legal text (`WebSite/site-react/app/{fine-print,start,legal}`), per `website-editing`.
- [ ] 3.4 Spec deltas + `environment-variables.md` default fix + drop the `TINYASSETS_USAGE_ENFORCEMENT` host-action row.

## 4. Prove
- [ ] 4.1 Limit-gone tests replace limit tests; new waiting behaviour covered; mutation-check each removed gate.
- [ ] 4.2 `gpt-6-astra` refute per PR: did removing X open a cross-user or host-safety hole.

## Context

This is step 1 of the 2026-10-11 decision
(`2026-10-11-harness-architecture-audit.md (archived in git)`). It is
foundation work: one boundary that later steps build on (Direction 5). The
existing parts are reused, not rebuilt:

- `ProviderRouter.call` already reserves each attempt
  (`router.py:1343-1484`) and enforces ADR-006 on entry and dispatch
  (`providers/owner_binding.py`).
- `ModelRef(connection_id, model_id)` (`providers/model_policy.py:19`) is the
  model reference.
- `served_model_plan.prepare_owned_model_plan` already enumerates the owner's
  own verified sources and orders them for Automatic.
- `ExecutionReceipt` (`providers/execution_receipt.py`) already separates
  `requested_model`, `configured_model` and reported `model`.
- `TurnRequestBudget` with `UsageStore` and `broker/usage.py` already
  implements reserve, dispatched and settle with outcomes `not_sent`,
  `succeeded`, `failed` and `unknown`, plus parent links.

What is wrong is how these parts are wired: six entry points, a one-provider
authority, callers that set up their own accounting, and a fabricated
response.

## Goals / Non-Goals

**Goals:** one call for every model request; the owner's connected set as the
authority; explicit choice honoured; exactly-once settlement linked to the
parent; a receipt that proves which model answered; a net-negative diff.

**Non-goals:** turning native CLIs into external-agent packages (step 4); one
box per owner (step 2); agents as files and `ta agent run` (step 3); streaming
settlement (`broker-streaming-contract`). Settlement is from buffered
responses until that contract ships.

## Decisions

### D1. The contract

```python
# tinyassets/providers/model_call.py
@dataclass(frozen=True)
class ModelCall:
    model: ModelRef | None          # None = owner default (strongest connected)
    fallbacks: tuple[ModelRef, ...] # explicit; empty = no substitute
    context: ModelContext           # system, messages/history, tools, tool_choice,
                                    # reasoning effort, deadline
    purpose: str                    # reply | tool_review | review | helper | learning
    origin: str                     # chat | workflow | child | extension | ta | review

@dataclass(frozen=True)
class Parent:
    owner: str
    command_center: str
    usage_id: str | None            # join this allocation; None = open a root
    subject: tuple[str, str] | None # ("turn"|"run", id) link, as today

async def call(request: ModelCall, parent: Parent) -> ModelResult  # stream + receipt
def call_sync(request: ModelCall, parent: Parent) -> ModelResult  # the thread-hop wrapper
```

`ModelResult` carries the response (text and tool calls, as
`ProviderResponse` does today) and a `Receipt` (D5). Every caller in the
deletion list moves to it: the chat coordinator, the workflow prompt and
agent nodes, foreground and background work agents, cloud continuation, the
assigned queue, platform helpers that used `call_provider` (25 call sites),
the action reviewer, extensions and `ta`. `ProviderRouter.call` becomes
private to the module.

### D2. Authority is the connected set

`connected_sources(owner, command_center)` returns the sources that are the
owner's own and live:

- HTTP connections with an active grant and a `model_use` capability;
- verified `api_key_http` open provider definitions owned by the owner;
- subscription sources with adopted custody.

This reuses `_own_verified_candidates`, `_http_models` and `_native_models`
from `served_model_plan`. `require_owner_bound_dispatch` changes from
"the provider the binding names" to "a member of `connected_sources`, with
owner credentials" (ADR-006 is unchanged: no host or platform credential ever
serves). The serving binding row stays as the owner's durable default
preference, so there is no schema change. Its exactly-one-provider checks, and
the `default` versus `manifest` scopes in foreground, background and cloud
continuation admission, collapse into this one membership check. Callers carry
no provider names.

### D3. Resolution

The first of these that applies wins:

1. `request.model`, with `request.fallbacks`;
2. the agent's `settings.yaml` `model` (`SettingsSnapshot.model_choice()`,
   which gains its first runtime caller);
3. for a workflow node, `llm_policy.preferred` as the model and
   `llm_policy.fallback_chain` as the fallbacks;
4. the owner's durable preference, otherwise Automatic (the strongest
   connected source, existing `prepare_owned_model_plan` order).

An explicit model that is not in the connected set, or is refused, fails
visibly, naming the model and the reason. The call tries a fallback only if
the caller declared it. Automatic may try the next source in its own order;
that is what Automatic means. A stored `llm_policy.difficulty_override` is
removed by migration M1, and the validator refuses it afterwards.

### D4. Allocation and settlement

- `call` opens a root allocation when `parent.usage_id` is None, or joins the
  given one. The allocation is always durable in the broker's usage store,
  through the existing `broker/usage.py` operations. The in-memory
  `TurnRequestBudget` branch, used when `_store is None`, is deleted.
- Each attempt is reserved before transport, marked `dispatched` before the
  first byte, and settled exactly once: `not_sent`, `succeeded`, `failed` or
  `unknown`. A retry inside `call` is a new attempt on the same allocation. A
  child session or nested call passes the parent's `usage_id`, so its
  attempts count against the parent's policy and a stop of the parent reaches
  the child (`InferenceUsageStopped`).
- HTTP sources still send through the broker with the private usage reference.
  That reference becomes internal to `call` and the broker; no caller can mint
  or see it.
- Native CLI attempts use the same reserve, dispatched and settle cycle. Their
  usage provenance is `cli_reported` or `unknown`.

### D5. The receipt

The receipt extends `ExecutionReceipt` with these fields:

| Field | Meaning |
|---|---|
| `requested_model` | What the caller or the resolution named, or `automatic` |
| `resolved_model` | The `ModelRef` the call dispatched, with source kind and family from the source's catalogue entry |
| `reported_model` | What the provider's response said; empty means `model_status: unknown` |
| `fallback_used` | The index into the declared fallbacks, or none |
| `execution` | `model_response` or `native_agent` (a CLI's whole loop, until step 4) |
| `usage` | Tokens and cost with `provenance`: `provider_reported`, `cli_reported`, `estimated` or `unknown`. Unknown is never zero |
| `outcome` | `succeeded`, `failed`, `not_sent` or `unknown`, as settled |
| `usage_id`, `attempt`, `parent_usage_id` | Links to the allocation |

`requested_model` is never evidence of what answered. `family` comes from the
resolved source's catalogue entry, never from titles or answer text. The
`WriterExecutionReceipt` observer, which collects the first response as a side
effect, is deleted because `call` returns the receipt. Run, node and turn
records store the receipt as they store `execution` today.

### D6. `ta` and raw HTTP

- `ta` gains a `model` capability: `{"model": "<connection>/<id>"?,
  "fallbacks": [...], "messages": [...], "tools": [...]}`. It returns the text
  and the receipt, and runs `call` with the invoking turn's or run's
  allocation as parent. Extensions use the same capability.
- The broker keeps classifying a POST to a model-source connection from
  trusted connection authority. It refuses the POST with one fixed message:
  "This connection is a model source. Call it through the `model` capability
  (`ta call model …`) or the agent's `model` setting, which meters it; raw
  HTTP to a model API is not metered." `InferenceUsageRequired` keeps its
  class, with this text. The 19-line prompt band-aid at
  `engine_mcp_server.py:1745-1763` becomes this one rule.

### D7. Action review and fail-loudly

The engine's action reviewer stops using MCP sampling (`ta_capabilities.py`
`_turn_reviewer`) and calls `call(purpose="review")` with the owner's review
model, which is explicit when set and otherwise the default. The mock string
at `graph_compiler.py:1741` and `call_provider`'s `_force_mock` path are
deleted. A node with no runnable model raises a `CompilerError` that names
what is missing.

## Base inventory (task 2.1)

Measured on `fcc071f1240` (2026-10-11) with `git grep` and Python AST spans;
the checked-out tree was clean and identical to that base. The 75 matching
`git grep` lines include imports, comments, definitions and call sites, so they
are not 75 model calls. There are **7 direct `call_provider` calls** and
additional callable handoffs in `api/runs.py`, `automations.py`,
`consumer_runtime.py`, `delivery_runtime.py`, `evaluation/editorial.py`,
`ingestion/extractors.py`, `run_input_direct.py` and `runs.py`. The graph prompt
path calls `call_with_policy_sync` through its callable and has three concrete
session implementations (foreground, background, cloud). `TurnRequestBudget`
has **7 constructors**: 5 caller/router roots (`agent_turn_coordinator`,
`background_served_provider`, `foreground_run_provider`,
`universe_intelligence`, `providers/router`) and 2 broker/storage
reconstitutions (`broker/usage.py`, `storage/agent_request_usage.py`).

The read-only production copy in Docker volume `regress3-copy-data` has 265
current `branch_definitions` rows. Parsing `node_defs_json` and
`default_llm_policy_json` found **0 nodes and 0 branch defaults** with
`difficulty_override` (0 branch IDs). Historical run/version snapshots can
contain the key and are not current editable workflow definitions. M1 must
still inspect the deployment's fresh backup before cutover.

| Existing deletion target | Measured AST span on base |
|---|---:|
| `UniverseBoundProviderCall`, `bind_universe_provider_call` | 64 + 13 = 77 |
| `_build_fallback_router` | 51 |
| `_call_router_with_retry` | 75 |
| `call_provider` | 95 |
| `ProviderRouter.call_with_policy(_sync)` | 35 + 83 = 118 |
| `graph_compiler._call_policy_router_with_retry` | 60 |
| Foreground policy helpers and `call_with_policy_sync` | 12 + 13 + 18 = 43 |
| Background/cloud `call_with_policy_sync` | 13 + 25 = 38 |
| `WriterExecutionReceipt` | 47 |
| `current_request_budget`, `request_budget_scope` | 2 + 14 = 16 |

These are inclusive definition spans, not net diff numbers. The broader
admission, allocation, mock and policy branches below do not form complete
definitions; their deletion size is measured from each PR's actual numstat.

## Deletions (same release)

| Delete | Where | Measured span or remaining estimate |
|---|---|---|
| `call_with_policy`, `call_with_policy_sync` | `providers/router.py:1879-1999` | -118 |
| `FALLBACK_CHAINS` and its two consumers | `router.py:243`, `api/status.py:1767`, `provider_serving_binding.py:392` | -30 |
| `call_provider`, `_call_router_with_retry`, `UniverseBoundProviderCall`, `bind_universe_provider_call`, `_build_fallback_router` (registers Claude, Codex and Ollama by name), `_force_mock` | `providers/call.py` | 298 measured; `_force_mock` branch separate |
| `_call_policy_router_with_retry`, the mock branch, the duplicate policy branch | `graph_compiler.py:545-604, 1735-1800` | 60 measured; branches separate |
| `_declared_policy_providers`, `_declared_policy_pins`, the default/manifest split in `_admit_manifest` and default admission, `call_with_policy_sync`, caller reservation in `_call_captured_prompt` and `_call_once` | `foreground_run_provider.py` | 43 measured; branches separate |
| The same policy, scope and allocation code | `background_served_provider.py:748-954` | -200 |
| Policy authority re-check | `cloud_automation_continuation.py` | -80 |
| The `_call_work_agent` allocation and policy plumbing | `workflow_agent.py:321-423` | -60 |
| Caller-built allocations and `request_budget_scope` / `current_request_budget` | `agent_turn_coordinator.py:464`, `universe_intelligence.py:699, 1522`, `router.py:801`, `request_budget.py:347-365` | -80 |
| The in-memory allocation branch | `request_budget.py` `TurnRequestBudget` | -120 |
| The exactly-one-provider serving checks | `provider_serving_binding.py` | -150 |
| `WriterExecutionReceipt` | `providers/execution_receipt.py:149-195` | 47 measured |
| The MCP-sampling reviewer | `ta_capabilities.py:410-445`, `agent_review.py` bound callback | -50 |
| The prompt band-aid; the old refusal text | `engine_mcp_server.py:1745-1763`, `agent_request_usage.py:126-136` | -25 |
| **Deleted** | | **Net measured per PR** |
| `model_call.py` (contract, resolution, allocation, receipt) | new | +320 |
| `connected_sources` and the membership check | `served_model_plan.py`, `owner_binding.py` | +80 |
| The receipt fields | `execution_receipt.py` | +40 |
| The `ta` `model` capability | `ta_capabilities.py` | +60 |
| M1 migration | `deploy/` | +40 |
| **Net** | | **Measured per PR** |

The baseline has 7 direct `call_provider` call expressions plus callable
handoffs, not 25 direct calls. Entries without a measured span remain planning
estimates until their exact branch deletion is in a PR diff. The gate is
`git diff --numstat origin/main -- tinyassets deploy ':!**/tests/**'`, with
total added minus total deleted below zero for each PR.

## Migration

M1 (with writers stopped, after a full backup): rewrite every stored workflow
node whose `llm_policy` has `difficulty_override` so that the key is removed,
and record the count and branch IDs in the deploy log. Nothing else changes in
storage: serving binding rows and usage store rows keep their shape.
In-flight runs admitted under a `manifest` receipt finish under the new
membership check. It is a superset of the old exact-provider check, so no
admitted call becomes forbidden.

## Risks / Trade-offs

- **Wider authority.** An agent can now use any source the owner connected,
  not only the bound one. That is the Direction (4): the owner connected it,
  and budgets and ADR-006 still apply per call. Cross-family review checks
  that membership is per owner and per command center and is revalidated on
  each attempt.
- **Native CLI receipts are weaker.** `execution: native_agent` with
  `cli_reported` or `unknown` usage is honest about it. Step 4 resolves it.
- **Buffered settlement.** Until `broker-streaming-contract` ships, HTTP
  usage is read from the completed response, so a stream cut mid-way settles
  as `unknown`.
- **A large touch surface in one PR.** Tasks 2-6 can ship separately, each
  net-negative, because `call` can first wrap `ProviderRouter.call` and
  callers then move to it one group at a time. No parallel engine exists at
  any point: each moved group's old entry point is deleted in the same PR.

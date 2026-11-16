# Bounded turn, model and snapshot seam check

2026-09-19; read-only Windows source inspection on planning base `7b406025`.
Commands: `rg -n` named symbols and `docview.py lines` cited helpers.
Source evidence only, not execution or deployment proof.

## Durable progress is not canonical turn admission

`conversation_store.py:79` stores `(session_id, turn_no, speaker, content, ts,
ext_id, execution_json, failure_json)` in the universe conversation database.
`record_turn:227` can deduplicate an external message ID, but canonical
`record_exchange:349` / `record_failure:369` use `_record_pair:383`, inserting
empty `ext_id` after execution with best-effort persistence. `universe_server.py`
`converse:2312` has no durable request key and records results at 2486/2512.
`execution_receipt.py:23` accepts exactly provider/model/model_status, not run ID.

`storage/agent_turn_journal.py:375` creates a new UUID-scoped provider turn in
the provider authority database. Input version 3 includes `authority_kind` and
`work_receipt_id`; `workflow_agent.py:60` uses that path. Its work receipt leads
to a run root (`foreground_run_provider.py:483`), but not to canonical founder
message admission or an exactly-once terminal conversation row.

Crucial: `runs.py:1004,2602,3359` `run_lineage.parent_run_id` means the previous
terminal comparison run, sometimes branch-wide. It is NOT causal execution
ancestry and MUST NOT become a recursive-turn or cancellation authority check.

Smallest missing relation for review: owner/universe/session-scoped client
request key + request digest, canonical turn ID, selected binding ID/revision
and component fingerprint, reserved root run ID, admission/terminal state and
single terminal result reference. Reuse an existing database, not a new service;
reviewer must choose its authoritative home and transaction seam. Do not
repurpose after-the-fact execution JSON or comparison lineage. Reserve once
before effects; restart/reconnect observes held/terminal state without replay.
Cross-database writes need a defined crash boundary, not best-effort deduplication.
This is a storage/API shape decision, not runtime implementation authorization.

## A root snapshot does not freeze descendants

`branch_versions.py:209-226,234` hashes/stores one Branch (topology, node
definitions, schema, skills), without resolving transitive executable closure.
`graph_compiler.py:2702` resolves `invoke_branch_spec.branch_def_id` against the
live child. `:2861` uses an explicit child version, but that snapshot may again
invoke a live child. `versions_invoking` (`branch_versions.py:433`) protects
reverse dependencies; it is not pinning. Current visibility checks still apply.
Child launch sites `graph_compiler.py:2790,2989` do not receive canonical turn
or cancellation association; blocking child policy can explicitly retry effects.

Smallest proposed v1 choice: reject nested invocation and unresolved executable
references before install/admission; accept only a self-contained published
graph whose executable nodes are covered by its hash. Multi-node strategy is
supported; nested harnesses are not. Alternative: recursively pinned version
closure plus causal child admission/cancellation, a larger reviewed slice.
Never freeze mutable definitions silently or resolve using creator authority.
Explicit tool effects which create/run workflows retain ordinary authority;
they are not automatically frozen dependencies. Reviewer must distinguish those
effects from executable references required to start this consumer.

## Current-turn model choice is not reusable run authority

`served_model_plan.py:418` authenticates a converse carrier, captures current or
saved preferences via `prepare_owned_model_plan`, and sets `agent_model_plan`
and model selection. `api/runs.py:59` instead creates a foreground run session
with universe config, not the conversation plan. `foreground_run_provider.py`
`:845-856` explicitly rejects substituted request/invocation/served-provider/
model-plan contexts. Its `:729-778` selects using node policy and current owned
discovery (`work_model_selection.prepare_work_model_snapshot`).
`workflow_agent.py:43-64` likewise rejects served plans.

Missing bridge is trusted receiver preference DATA, never a reused capability:
capture choice/fallback ordering at canonical admission, derive each invocation
through current work authority, exclusions and budgets. Graph suggestions may
only narrow compatible choices, not override user choice or add fallback/spend
consent; conflicts refuse visibly. Record the actual provider/model producing
final text; label multiple contributors honestly, not as the selected default.
Reviewer must confirm a narrow bridge preserves both authority boundaries;
otherwise model parity blocks the adapter. Never retry the whole failed turn.

## Concrete review decision

Choose self-contained graphs versus transitive closure; name the canonical turn
reservation home/commit boundary; approve/adapt the model preference-data bridge
and current-owner install consent. Layout deployment proves none of these.
Arbitrary executable UI/full-setup compatibility remains outside this adapter.

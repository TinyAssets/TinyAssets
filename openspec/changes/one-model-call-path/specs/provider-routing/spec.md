## ADDED Requirements

### Requirement: Every model request uses one call
Every model request SHALL go through `call(model_ref, context, parent)` in
`tinyassets/providers/model_call.py`, whose only executor is
`ProviderRouter.call`. This covers chat turns, workflow prompt and agent
nodes, foreground and background work agents, cloud continuation, the assigned
queue, platform helpers, action review, child sessions, extensions and `ta`.
No other module SHALL invoke a provider's `complete` or the router directly.
Callers SHALL NOT branch on a provider or vendor name.

#### Scenario: a workflow prompt and a chat turn share the path
- **WHEN** a chat turn and a workflow prompt node each request a model
- **THEN** both reach the provider through `model_call.call`
- **AND** `call_provider`, `call_with_policy` and `call_with_policy_sync` do not
  exist in the codebase

#### Scenario: a child call carries its parent
- **WHEN** an extension or `ta` issues a model call during a turn
- **THEN** the call names that turn's allocation as its parent

### Requirement: The owner's authority is the set of connected sources
A call SHALL be admitted only for a source in the owner's connected set for
that command center: HTTP connections with an active grant and a model
capability, verified owner-owned open provider definitions, and subscription
sources with adopted custody. Membership SHALL be checked again for every
attempt. No platform, host or other owner's credential SHALL ever serve a call.
The serving binding SHALL act as the owner's default preference, not as a
limit to one provider.

#### Scenario: a second connected source is usable
- **GIVEN** an owner who connected a Claude subscription and an OpenRouter API key
- **WHEN** a call names a model on the OpenRouter connection
- **THEN** it is admitted and dispatched on that connection

#### Scenario: another owner's source is refused
- **WHEN** owner A's call names a connection owned by owner B
- **THEN** it is refused before anything is sent

#### Scenario: a grant revoked between attempts
- **WHEN** a source's grant is revoked after the first attempt
- **THEN** the next attempt on that source is refused, and no broader
  credential is substituted

### Requirement: An explicit model choice is honoured with declared fallbacks only
The call SHALL resolve its model from the first of these that applies: the
call's `model_ref`; the agent's settings `model`; the node's
`llm_policy.preferred`; the owner's default preference; and otherwise the
strongest connected source (Automatic). An explicit choice SHALL NOT be
replaced by another model unless the caller declared that model as a
fallback. An explicit choice that is unavailable SHALL fail visibly, naming
the model and the reason. `llm_policy.difficulty_override` SHALL be refused.

#### Scenario: a node pins a non-default connected model
- **GIVEN** an owner whose default is Claude, and a node with
  `llm_policy.preferred` naming a connected GPT model
- **WHEN** the node runs
- **THEN** the call dispatches to the GPT model, and its receipt records it as
  resolved

#### Scenario: an unavailable pin does not silently fall back
- **WHEN** a pinned model is refused by its source and no fallbacks are declared
- **THEN** the call fails, naming the pinned model
- **AND** no other model is called

### Requirement: Every attempt is reserved on its parent allocation and settled exactly once
The call service SHALL open a root allocation, or join the parent's, in the
broker's durable usage store. It SHALL reserve each attempt before transport,
mark it dispatched before the first byte is sent, and settle it exactly once
as `not_sent`, `succeeded`, `failed` or `unknown`. Retries and child calls
SHALL be attempts on, or children linked to, the parent allocation. Stopping
the parent SHALL stop its children. Callers SHALL NOT construct allocations,
reserve or settle. No allocation SHALL exist only in memory.

#### Scenario: a retry is not charged twice
- **WHEN** a call's first attempt fails transiently and a second succeeds
- **THEN** the allocation holds two attempts, each settled once
- **AND** the parent's totals count each exactly once

#### Scenario: a lost reply settles unknown
- **WHEN** the connection drops after dispatch and before the response completes
- **THEN** the attempt settles as `unknown`, never as `not_sent` or zero usage

#### Scenario: a parent stop reaches a child call
- **WHEN** a turn is stopped while a child model call it started is in flight
- **THEN** the child's attempt ends and settles on the turn's allocation

### Requirement: Every call returns a receipt that proves which model answered
Every call SHALL return a receipt with `requested_model`, `resolved_model`
(with source kind and family from the source's catalogue) and
`reported_model`, kept as separate fields. It SHALL also carry
`fallback_used`, `execution` (`model_response` or `native_agent`), `usage`
with `provenance` (`provider_reported`, `cli_reported`, `estimated` or
`unknown`), `outcome`, and the allocation and attempt identifiers. The model
or family SHALL NOT be inferred from a workflow title, an agent name or
response text. An unreported model SHALL be recorded as `unknown`, and
unknown usage SHALL NOT be recorded as zero.

#### Scenario: a mislabeled step cannot pass as cross-family
- **WHEN** a step titled "Codex review" resolves to a Claude model
- **THEN** its receipt's `resolved_model` family is Claude

#### Scenario: a source that reports no model
- **WHEN** a provider response carries no model name
- **THEN** `reported_model` is empty and `model_status` is `unknown`, while
  `requested_model` and `resolved_model` are still recorded

### Requirement: A model call never fabricates a response
A call that cannot run SHALL raise an error naming what is missing. No code
path SHALL return placeholder or mock text as a model response.

#### Scenario: a prompt node with no runnable model
- **WHEN** a workflow prompt node has no admitted model
- **THEN** the node fails with a `CompilerError` naming the missing model
  source
- **AND** no `[Mock response …]` text is produced

## REMOVED Requirements

### Requirement: Every role chain terminates at the local model
**Reason**: Fixed role chains ending at a host-local model contradict ADR-006,
and `FALLBACK_CHAINS` is deleted. Sources are the owner's connected set.
**Migration**: An owner who wants a local model connects it as their own
source. Resolution follows "An explicit model choice is honoured with declared
fallbacks only".

### Requirement: Per-node policy routing honors llm_policy overrides
**Reason**: `call_with_policy` is deleted. Before this change it discarded the
policy (`del policy, difficulty`), so this requirement no longer described
the code.
**Migration**: `llm_policy.preferred` and `llm_policy.fallback_chain` resolve
through "An explicit model choice is honoured with declared fallbacks only".
M1 removes `difficulty_override`.

### Requirement: Provider calls use one explicit immutable contract
**Reason**: Superseded by "Every model request uses one call" and "Every call
returns a receipt that proves which model answered". The policy-routing
telemetry tuple goes with `call_with_policy`.
**Migration**: `UniverseContext`, `ModelConfig` and `ProviderResponse` remain
the router's internal types beneath `model_call.call`.

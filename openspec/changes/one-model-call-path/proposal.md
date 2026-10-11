## Why

The founder's app agent could not add a cross-family review step. Its raw
HTTP POST to OpenRouter was refused for missing accounting, and its "Codex
review" step ran on the default Claude model. The cause is structural, not a
missing patch (`2026-10-11-harness-architecture-audit.md (archived in git)`):

- **Six model-call paths**: native CLI turns, the HTTP agent turn,
  `call_with_policy` for workflow prompts, `call_provider` for platform
  helpers, MCP sampling for action review, and a raw `ta` connection POST
  that is refused.
- **One-provider authority.** The owner's authority names exactly one provider,
  so `router.call_with_policy` begins with `del policy, difficulty`
  (`tinyassets/providers/router.py:1902`). A node's model choice survives
  only in the foreground "manifest" scope.
- **Accounting set up by callers.** Six callers build their own parent
  allocation, and HTTP metering works only through the envelope that one
  adapter (`api_key_http_provider`) attaches.
- **A mock response.** A prompt node with no provider returns
  `"[Mock response for <node>]"` (`tinyassets/graph_compiler.py:1741`),
  against Fact 8.

Founder decision, 2026-10-11: build the correct shape, and do not patch a
known bad one. This change is step 1 of six.

## What Changes

- **One call.** `call(model_ref, context, parent)` in
  `tinyassets/providers/model_call.py` is the only way to call a model. It
  serves chat turns, workflow prompt and agent steps, child sessions,
  extensions and `ta` (a new `model` capability). `ProviderRouter.call` is its
  only executor.
- **The owner's authority is the set of their connected model sources**
  (connections with a live grant and a model capability, verified open
  provider definitions, and adopted subscription custody). It is no longer
  one bound provider. Callers contain no provider-specific branches.
- **The caller's explicit model choice is honoured.** The order is the call's
  `model_ref`, then the agent's `settings.yaml` `model`, then the node's
  `llm_policy.preferred`. Only a declared fallback list may substitute another
  model. With no explicit choice, the call uses the owner's strongest connected
  source, as Automatic does today.
- **Broker metering for every call.** The call service opens or joins the
  parent allocation in the broker's usage store, reserves each attempt before
  sending, and settles it exactly once. Retries and child calls link to the
  parent. Callers never reserve or settle.
- **Every call returns a receipt.** It records the requested, resolved and
  provider-reported model separately, usage with its provenance, and an
  outcome that may be `unknown`.
- **Raw HTTP to a known model API is refused.** The message names the `model`
  capability instead of steering the agent to a prompt node.
- **The mock-response fallbacks are deleted.** A call that cannot run fails
  loudly.
- **Deletions ship in this change** (design § Deletions): `call_with_policy`
  and its sync form, `call_provider`, `UniverseBoundProviderCall`,
  `_build_fallback_router`, the graph policy retry, `FALLBACK_CHAINS`, the
  default/manifest scope split, caller-side allocation, the in-memory
  allocation branch, the MCP-sampling reviewer, the prompt band-aid and both
  mocks. The estimated net is about **-1.1k executable lines**. A slice whose
  measured net is not negative does not merge.

**BREAKING** (internal callers only): `call_provider`, `call_with_policy` and
`call_with_policy_sync` are removed. A workflow node's `llm_policy` keeps its
stored shape, but `difficulty_override` is no longer accepted; one migration
removes it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `provider-routing`: one call contract, owner source-set authority, explicit
  choice with declared fallbacks only, exactly-once settlement, receipts, and
  no fabricated responses. Removes the role chains to the local model,
  `call_with_policy` policy routing, and the old contract requirement.
- `platform-check-scope`: the raw-HTTP model refusal names the `model`
  capability.

## Impact

- **Authority and money (floor, ADR-015).** Authority widens from one bound
  provider to the owner's connected set, and remains owner-only (ADR-006).
  Settlement moves from callers into the call service. This needs one
  cross-family review round.
- **Storage.** No new tables. Allocations use the existing broker usage store
  (`.broker/outbound.db`). One data migration removes `difficulty_override`
  from stored `llm_policy` documents, run with writers stopped and after a
  backup.
- **Public surface.** `ta` gains a `model` capability. MCP handles are
  unchanged.
- **Superseded work.** PR #4614's effector-accounting bridge
  (`connection_inference.py`) and its Codex-home launch special case are not
  landed. Its lost-message fix lands on its own.
- **Not in scope.** Native CLIs stay callable through the same call, marked
  as native-agent executions in their receipts. Turning them into
  external-agent packages is step 4.

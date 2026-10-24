Checking how the router turns the `ta` request carrier into authority, to see whether fallbacks really get fresh authority.

Still reviewing — now reading the new test file and running it plus the shape checker.

Checking whether workflow_agent and the chat coordinator dispatch through model_call or around it.

Running the relevant test files now.

Checking whether workflow-run policy fallbacks now hit the armed carrier and change the error type the run's candidate loop expects.

The PR holds against the bypass and cross-owner attacks, but it adds a fallback regression on workflow runs, so I can't agree with it yet.

**What holds up**
- **No dispatch bypasses the broker or the receipt.** The paths that reach `get_provider_router()` outside `model_call` (`foreground_run_provider.py:897,1006`, `default_model_evidence.py:58`, `workflow_agent.py:317`) only cool sources, read cooldowns or classify failures. Workflow and foreground dispatch goes through `model_call.call`/`text_sync`.
- **No platform LLM call runs without an owner.** reflexion, raptor, agentic_search and extract_learning all go through `text_sync`. With no `universe_context` it uses the bound owner call if one is set and otherwise raises `ProviderAuthorityHeldError`.
- **No caller can pick another owner's source.** On the `ta` path each fallback is checked against the admitted sources (`ta_capabilities.py:458`), and every attempt is re-authorized with the requester's identity (`router.py:697-713`). All attempts share one `TurnRequestBudget`, so spending can't widen.
- **The router allowlist rewrite (`router.py:1008`) does the same thing as before** for both the served and armed paths.
- **No tests were deleted.**

**Findings**

1. **High: `tinyassets/providers/model_call.py:208-241` with `foreground_run_provider.py:957-959`.** Workflow nodes that declare `llm_policy.fallback_chain` now take a second fallback path, and it breaks the run's own candidate fallback.
   - `_call_once` passes `{**policy, "preferred": selected}`, so the node's `fallback_chain` survives. `text_sync` (via `_policy_models`) turns it into `ModelCall.fallbacks`, which `_attempts` now actually tries. Before this PR, `call` ignored fallbacks.
   - The second attempt reuses the same armed carrier, which is bound to `selected`. `router.py:821-838` rejects the different model with `ProviderAuthorityHeldError`. On the way it settles a carrier the first attempt may already have settled, which can produce the "could not be settled" error instead.
   - So the caller gets `ProviderAuthorityHeldError` where it used to get `AllProvidersExhaustedError`. The foreground loop's `except AllProvidersExhaustedError` (`foreground_run_provider.py:991`) is then skipped: no refusal or capacity boundary, no cooldown, no step to the next admitted work candidate. The run fails as held instead of falling back.
   - The new tests use a fake router that honours `model_selection`, so they never hit the carrier check.
   - Fix: don't run `_attempts` when an invocation carrier or a pre-bound served authority is present, or remove the policy `fallback_chain` from the policy passed at `_call_once`.

2. **Medium: `model_call.py:221,237`.** The fallback runs on *any* `AllProvidersExhaustedError`. The chat and work paths only fall back when every attempt shows `side_effect_state == "none"` and a known failure class (`agent_capacity_boundary.py:80-85`), and they cool the abandoned source. Here, an attempt that was sent and then timed out (side effects unknown, possibly billed) is resent to the next source, and nothing records the cooldown. That is a second, weaker fallback policy. Use `capacity_boundary`/`refusal_boundary` to decide whether to continue.

3. **Medium (doc accuracy): `docs/architecture.md` S1 row.** "Acquire fresh authority per attempt" is only true for the `provider_request` path (`ta`). With an armed carrier or a pre-bound served authority, every attempt uses the same authority, and `model_selection` is either rejected (carrier) or ignored (served: `chain = [model_authority.provider]`, `selected_model` comes from the authority). Marking S1 "converged" on that wording overstates it until finding 1 is fixed.

4. **Low: `scripts/check_shapes.py:161-175`.** The S13 ceiling went from 14 to 0 by registering the old-path files, including the very large `universe_intelligence.py`, as homes. The forbid patterns catch `.complete(`, `.call_sync(`, router or provider imports, and `call_provider`. They don't catch the async form `from tinyassets.providers import call as bridge; bridge.get_provider_router().call(...)`, which is how `workflow_agent.py:317` reaches the router. Add `get_provider_router` and `\.call\(`-on-router (or `ProviderRouter`) to the forbid list.

5. **Low: `tinyassets/graph_compiler.py:349-366`.** `_get_shared_router()` builds a second `ProviderRouter()` and nothing calls it. It's dead code to delete (Direction 6), not a live second path.

**Tests:** I couldn't confirm anything by running tests on this Windows worktree. The shape checker passes ("shape registry clean"), but `test_model_call_tail`, `test_platform_has_no_llm` and `test_owner_broker_egress` fail here with "owner broker channel is unavailable" and "the credential broker is not running". That looks like the environment, not the PR, but they need `scripts/linux_oracle.py` or CI before anyone counts them as passing.

VERDICT: DISAGREE_EVIDENCE

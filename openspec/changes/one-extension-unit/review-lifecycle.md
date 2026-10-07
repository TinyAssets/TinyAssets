Still reviewing — checking how the outside origin travels with launch grants and queued runs.

Checking the design's outside-client section, then writing up findings.

I'm recommending ADAPT for #4519. The overall design holds up, but there are five fixes needed before merge. Two of them let an outside client end up with the owner's full authority (findings 2 and 3). This was one solo pass: I read the four new modules, the working diff and the paths around them, and ran no tests. I left out the three known test issues you listed.

## Required fixes

**1. A slow outside effect can block revocation and the kill switch, for every owner.** (Confirmed by reading the code.)
- **Where:** `tinyassets/outside_authority.py:179-188`. `effect_admission` opens a write lock on the one shared `.outside-client-authority.sqlite3` file and keeps it open for the whole network call (`effectors/authenticated_external_call.py:1294-1307`).
- **What happens while an effect is running, for any owner:**
  - Revoke and kill-switch calls wait 10 s, then fail with a database-locked error. The handler in `onboarding/outside_clients.py` doesn't catch that error, so the owner gets a 500 and nothing is revoked.
  - Token checks fail the same way at `observe()`, called from `workos_provider.py`. That error isn't caught there either, so login breaks for every outside client.
  - A second outside effect anywhere fails with the raw error.
- **Effect:** a client can stall its own revocation just by keeping an effect in flight, and one owner's slow effect breaks other owners' clients.
- **Fix:** do the check in a short transaction and track in-flight effects with a lease, not a held lock. Treat every database error, including in `observe`, as a refusal.

**2. The outside origin is silently dropped when the engine route has no grant key.** (Confirmed by reading the code.)
- **Where:** `launch_grant` (`served_tools.py:208`) returns an empty grant when there's no key, and `route_with_session` then sends no grant at all. `engine_mcp_http.py:174` accepts routes with an empty `grant_key`.
- **Effect:** the engine sees `launch_tools() is None`, so `outside_origin()` returns None, and both `OutsideClientScope` and the `_bind_founder_identity` check do nothing. The outside client's turn runs every engine tool with no restriction.
- **Fix:** if the current identity has an outside origin, refuse to launch rather than launching without a grant.

**3. Saved work created by an outside client keeps running after revocation, with full owner authority.** (Likely; I didn't trace the scheduler code.)
- **Where:** origin only travels through `copy_context()`, which covers immediate `run_graph`. There's no origin handling anywhere in `automations.py`, `runs.py` or `graph_compiler.py`.
- **Path:** a client granted `write_graph` creates an automation or schedule. When it fires later, there's no identity, so `effect_admission` lets everything through and agent nodes (`graph_compiler.py:2152`) get grants with no outside origin.
- **Effect:** this goes beyond the client's scopes and survives both revocation and the kill switch, which contradicts the design's "no positive authorization cache."
- **Fix:** store the client, family and generation on saved work and re-check them when it runs, or refuse automation and schedule creation for outside clients until that's built.

**4. A hook failing after a completed turn turns it into a failed turn.** (Confirmed by reading the code.)
- **Where:** `agent_turn_coordinator.py:462-466` runs the `turn_end` hook after `_run()` has finished and its effects have happened. If the hook raises, the turn is reported as failed, and a retry repeats the effects.
- **Related:** `extension_hooks.py:260` and `:284` raise whenever an active hook exists but the turn has no `bash` grant. So one installed hook makes every engine tool call and every turn fail for research turns and outside-client turns. That's not what an "observational" hook should do.
- **Fix:** record hook failures in the turn's evidence instead of raising after completion, and skip hooks (with a note) when `bash` isn't granted.

**5. The hook middleware runs every tool with run-level permissions.** (Likely.)
- **Where:** `engine_mcp_server.py:264-273`. `ExtensionHookEvents` calls `_bind_founder_identity(_RUN_CAPABILITIES)` around `call_next`, not just around the two `engine_event` calls.
- **Effect:** any tool that relies on the ambient identity rather than setting its own now runs with run permissions, even in research sessions and for read-only tools. This happens even when no hooks exist.
- **Fix:** set that identity only around `engine_event`.

## Needs a test before merge
- **Hooks can trigger themselves endlessly.** (Likely.) A hook started by `engine_event` runs bash with `ta`, which calls back through `call_platform` and `server.mcp.call_tool` on the engine loop. That callback doesn't carry the thread's context, so `_RUNNING` is False. If `call_tool` goes through the middleware, `before_tool` fires again and recurses without limit, each level a 30 s bash. Add a test where a hook calls a platform tool through `ta`, or carry the context into `call_platform`.
- **Per-client scopes can be sidestepped within the owner's own universes.** (Likely.) `check_mcp_request` (`outside_authority.py:207-212`) works out the universe only from `universe_id`/`graph_id`, falling back to the founder's home, and the agent from `agent_id`/`agent_binding_id`, falling back to `main`. Calls that name their target another way, such as `run_id`, `branch_def_id`, `branch_id`, `automation_id`, `agent_definition_id` or `source_id`, are checked against the home universe and the main agent, whichever universe or agent they actually touch. This stays within one owner, so it's not a cross-user breach, but it breaks the "exact universe/agent/capability" promise. Resolve those IDs to their real universe and agent, or refuse calls that name a resource without an explicit universe.

## Optional notes
- `effect_admission` only checks that the grant is still live, not which capability it covers. Any client granted `run_graph` can therefore fire any of the owner's connections. That may be intended; if so, write it into the design.
- Any bash command starting with `ta extension:event --json ` skips the before/after hooks and can fake events. That's fine while hooks are only observational, but an owner shouldn't use them as a policy gate.
- An owner who is an admin, not the owner, of someone else's universe can grant an outside client into it (`onboarding/outside_clients.py:428`). That stays within their own authority, but it may deserve a sentence in the design.
- Outside-client and first-party chats share one memory session per user and agent (`universe_server.py:3125`). History and carried-over steering written during an outside turn can therefore feed the owner's next turn, which has full access. It's the same user, so not a floor issue.
- Checked and fine:
  - The UI fence and projection: a stale revision or generation, or a hand-edited row, is hidden.
  - The family and `auth_time` revocation fence.
  - Generation pinning in launch grants, covered by the grant's signature.
  - The `/mcp` path restriction and the refusal of batches and other methods.
  - Blocking outside clients from changing the extension lifecycle.

VERDICT: ADAPT

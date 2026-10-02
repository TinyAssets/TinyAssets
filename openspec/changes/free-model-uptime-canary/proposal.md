## Why

The founder, 2026-10-02: "make sure the freemodel user is always prioritized to maintain uptime throughout the testing time." Play closed-test participants connect a FREE OpenRouter model and build; endpoint liveness alone cannot show whether their served model path still works.

## What Changes

- Run one real served `converse` turn hourly (every three hours after two consecutive failures, until a pass) from the droplet through `https://tinyassets.io/mcp`, in a dedicated private canary account/home with its own connected free-model credential.
- Automatically create a host-confined canary bearer in a separate root-only env file on first deploy. Refuse every action outside exact own-home conversation/status shapes before dispatch; the bearer cannot connect credentials or perform writes through tools.
- Resolve the enrollment human through the identified founder home's `universe_owner.owner_of`, while the reserved account owns its admin/founder-home/usage and credential/grant records. Offer an explicit-button, one-time owner-only browser link using the existing OpenRouter OAuth PKCE acquisition flow, with a narrowly scoped server-side bind to the canary home. Founder setup is clicks only.
- The fixed prompt (`Reply with the single word: ok`) and absent tool authority make a healthy canary turn one request with no tool rounds. Reuse #4303's merged bad-reply recovery (`tinyassets/agent_turn_coordinator.py:861`, `MAX_BAD_REPLY_RETRIES = 2`): retry the same model once, then one other accepted model, at most 1 + 2 = 3 requests. Skip learning extraction and deferred learning for every canary-principal turn. Sustained-failure backoff gives a conservative planning worst case of (2 initial failures + up to 8 three-hour probes) x 3 = about 30 requests/day. Agents have no per-turn or per-message cap; they run as long as the task needs, bounded only by the real remaining budget pooled across all of the command center's sources.
- Enforce own-home confinement at the shared permission boundary before public-read shortcuts and across persisted/background execution; define deletion cleanup and separate authorizer auditing for the cross-account bind.
- Record result, structured failure code, actual request count, latency and answering model in host state and journal; alarm on two consecutive failures through a checked write to the local watchdog alarm log; external GitHub issue delivery is optional and permission-dependent. A stolen owner session is outside the PKCE threat model and could bind an attacker's source only while the canary has no connection; confirm binding in the owner's request rail.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `uptime-and-alarms`: add a dedicated account-owned free-model canary, its constrained authority, click-only enrollment, accounting and alarm lifecycle. Existing model-independent uptime probes remain model-independent.

## Impact

Implementation will touch auth middleware/principal classification, served-turn admission and receipts, onboarding PKCE/bootstrap integration, host env provisioning, host uptime installer/unit manifests, focused tests and `docs/host-actions.md`. These are planned changes only; this drafting lane writes solely inside this change directory.

Owner: Codex drafting for the lead. Branch: `feat/free-model-canary`. One intent and one eventual PR: detect failures of the closed test's free-model served path without founder secret work. This draft is committed locally, not pushed or deployed; implementation, live proof and spec sync remain unchecked tasks.

## Out of Scope

No paid model, shared platform LLM, founder model subscription, desktop infrastructure, GitHub-hosted model runner, new GitHub credential, secret paste or founder shell work. No automatic daemon restart for provider failures. No scheduling priority changes for users, synthetic build benchmark, guaranteed third-party uptime, or changes to the existing droplet-down canary. One short turn covers the serving path, not every build/tool workflow.

## Why

The founder, 2026-10-02: "make sure the freemodel user is always prioritized to maintain uptime throughout the testing time." Play closed-test participants connect a FREE OpenRouter model and build; endpoint liveness alone cannot show whether their served model path still works.

## What Changes

- Run one real served `converse` turn hourly (every three hours after two consecutive failures, until a pass) from the droplet through `https://tinyassets.io/mcp`, in a dedicated private canary account/home with its own connected free-model credential.
- Automatically create a host-confined canary bearer in a separate root-only env file on first deploy. Refuse every action outside exact own-home conversation/status shapes before dispatch; the bearer cannot connect credentials or perform writes through tools.
- Resolve the enrollment human through the identified founder home's `universe_owner.owner_of`, while the reserved account owns its admin/founder-home/usage and credential/grant records. Offer an explicit-button, one-time owner-only browser link using the existing OpenRouter OAuth PKCE acquisition flow, with a narrowly scoped server-side bind to the canary home. Founder setup is clicks only.
- Reuse the real free-tier per-message budget from `fix/request-count-per-turn`: at most two rounds plus one alternative after failure (three requests), normally one request. Reuse its capped-source learning skip. Sustained-failure backoff bounds the planning worst case to about 30 requests/day.
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

## Why

A universe cannot run until it has a real LLM to run on, and today there is **no
way for a user to give their universe one**. Proven live 2026-08-10 via the
TinyAssets connector reading the real graph: the Slack Demo App agent
(`agent_binding_01kz0k6mwe61a0ph60a2hzp01x`) points at a **placeholder**
`provider_ref: "opaque:test-provider-ref-v1"`; the only real binding is an
*automation-scoped* GitHub-drain grant (`pwb_fbddd0e8…`,
`allowed_operations:["repository_spec_delivery"]`, role `writer`, cap 64 — not
chat/completion); and there is **no `connections` entry for Anthropic or OpenAI at
all**. So every conversational turn fail-closes with "Connect your provider before
running this universe" (keystone #2399, which is correct). The base durable-memory
fix records turns fine, but the universe can never *reply* — it has nothing to run
on.

Founder directive (2026-08-10), which this change makes true: **the platform NEVER
provides an LLM to any universe — not a borrow, not a metered trial, ever.** The
ONLY way a universe gets an LLM is the user connecting THEIR OWN subscription. This
is **generic for every user — there is no special path for the platform's own
dogfood universe** (u-tiny/Demo App is just a user of the same flow). A user brings
their universe two things, both their own: their **LLM provider** (subscription)
and the **GitHub project** that universe is meant to work with.

## What Changes

Build the **"birth of a universe" bring-your-own-LLM connect flow** as the SOLE
route by which any universe acquires an LLM:

1. **One-click provider-OAuth per subscription** — "Connect Claude" federates to
   Anthropic's consent, "Connect ChatGPT/Codex" to OpenAI's; the returned
   credential is **requester-owned** and stored in that universe's own vault. (API
   key paste is the alternate for API-key providers.) WorkOS remains identity/login
   only; it does not grant LLM entitlement.
2. **Mint a general-purpose chat/completion `provider_binding`** from the connected
   credential, scoped for agent/host *serving* (today only an automation-shaped
   `bind_provider` exists — cadence/repo/spec, unusable for conversational turns).
3. **Wire the universe's agent binding** to that real binding (replace the
   placeholder / mis-pointed ref), and expose a host write-path for the serving
   flag so a connected universe actually serves.
4. **Point the universe at its GitHub project** — the generic "the repo this
   universe works with" binding, available to any user, not a special path.
5. **Prune every non-intended LLM route** — host writers, the writer-"fleet"
   plumbing, old "fantasy writers", ambient host-credential fallback
   (`CLAUDE_CODE_OAUTH_TOKEN`/`CODEX_HOME` borrow in `providers/base.py`), and the
   platform-supplies-an-LLM branches — so the connect flow above is the ONLY way an
   LLM is ever reached.
6. **Guard test**: assert no code path can reach an LLM without a
   universe-authorized, requester-owned provider (fail-closed everywhere, no
   ambient borrow).

Slice order: **slice 1** mint + wire a requester-owned chat/completion binding from
a connected subscription (restores conversational serving, proves the model,
generic); **slice 2** the one-click provider-OAuth connect UX + vault capture;
**slice 3** the prune + the guard test. Security-substrate; Codex-built,
dual-family reviewed before any live rollout.

## Capabilities

### New Capabilities
- `byo-llm-provider-connect`: how a user connects their own LLM subscription to a
  universe (provider-OAuth → requester-owned credential in the universe vault →
  minted chat/completion provider binding → wired agent/serving), as the sole LLM
  route.

### Modified Capabilities
- `provider-routing`: the router's authorized-provider resolution becomes the ONLY
  path to an LLM — every host-writer / ambient-host-credential / platform-supplied
  fallback is removed; a request with no universe-authorized provider fails closed
  with "connect your provider", enforced by a guard test.

## Impact

- `tinyassets/providers/router.py` (sole-path resolution + fail-closed guard),
  `tinyassets/providers/base.py` (remove host-cred/ambient fallback + `codex_home`
  host borrow), `tinyassets/credential_vault.py` (requester-owned connect/deposit +
  chat/completion binding mint), `tinyassets/api/universe.py` (agent_binding /
  connections / engine-source), the connector `write_graph` surface (a
  chat/completion serving binding operation + host serving-flag write-path).
- Prunes host-writer / writer-fleet / `fantasy_daemon` LLM-provisioning code.
- Live surfaces: u-tiny/Demo App conversational serving; the Slack recall+reply
  proof completes only once slice 1 lands. No platform-supplied-LLM path survives.

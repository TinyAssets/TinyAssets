## Why

`https://tinyassets.io/mcp` only serves chatbot clients with connector UIs
(Claude.ai, ChatGPT) that can run the connector OAuth flow. A programmatic agent,
script or integration with no MCP client cannot connect at all.

## What Changes

- **Founder request, 2026-10-10:** a REST API that mirrors what the MCP server
  already exposes, authenticated by long-lived, revocable per-user API keys. Same
  underlying logic, different transport, simpler auth. MCP stays as it is for the
  chatbot-connector audience; REST is the universal path for anything that can
  make an HTTP call.
- New public endpoint `https://tinyassets.io/api` (Worker route + proxy).
  `AGENTS.md` fact 11 now names both public endpoints.
- Routes in this first release (each one runs exactly one MCP handle's function):
  - reads (`read_graph`): list command centers, read one, its status (state),
    its runs (activity) and one run, its connections;
  - actions: start a branch run and cancel it (`run_graph`), send the agent a
    message and get its reply (`converse`).
- API keys: minted, listed and revoked only from the protected owner view
  (`POST /app/api-keys`, owner session, same rule as `/app/outside-clients`).
  The secret is shown once and stored as a SHA-256 digest.
- Each key is an outside client (`tinyassets/outside_authority.py`) with exact
  `{universe, agent, capability}` grants, so every existing outside confinement
  applies; there is no second authority path.

## Out of scope (first release)

- Webhooks: the agent side has no inbound endpoint yet; polling
  `GET .../runs/{run_id}` covers it.
- OAuth client-credentials for third-party developers.
- An app UI for keys (the owner route exists; the screen is a follow-up).
- Advertising `/api` on the website `/developers` page: after it is verified
  live.

## Impact

New: `tinyassets/api_keys.py`, `tinyassets/rest_api.py`,
`tinyassets/onboarding/api_keys.py`. Changed: auth middleware (the `/api`
door), route mounting, Worker route. New storage: `.api-keys.sqlite3` under the
data dir (create-if-missing, no migration). No money, no new provider spend
beyond what the same handle already spends.

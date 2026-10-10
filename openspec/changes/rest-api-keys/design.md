## Context

The public handles are plain Python functions in `universe_server.py`; `_structured_return` supplies the connector projection. `outside_authority.py` already carries authority through runs, saved work, resource checks and effect admission. `owner_sessions.require` verifies the separate interactive owner cookie and exact origin. These are the reuse boundaries.

## Goals / Non-Goals

Deliver programmatic reads, messages and runs with long-lived revocable keys and an owner UI. No generic write endpoint, key-based approval, OAuth changes, webhooks or replacement business logic.

## Decisions

1. Thin REST routes call `read_graph`, `get_status`, `converse`, `run_graph` and the same structured-result projection. No `write_graph` operation is in v1 scope. Return connector data in `data`, with explicit untrusted-content markers around the entire payload; preserve nested connector fences. Strict input models prohibit arbitrary handle names, targets, identity and approval parameters.
2. Store keys in an additive table in the existing platform-owned `.outside-client-authority.sqlite3` (already outside command centers and covered by platform backup). Generate `ta_key_` plus 256 random bits, store SHA-256 only, and return plaintext only from successful creation. Persist opaque ID, owner, name, fixed center grants, agents, independent levels, timestamps, revocation and shared per-minute counters. No positive auth cache; acknowledge revoke after commit; invalid keys return 401. Missing store/row fails closed. Names/scopes can be edited only in the owner view; edits invalidate saved work through a generation increment.
3. Each grant names a current owned command center and explicit agent IDs or `*`. `read`, `message`, `control`, `costly` are independent. Shared center metadata/state/activity/connections and branch runs require `*`, since those existing handlers span agents. Conversation reads and messages support exact agent scopes. No cross-owner grants, even with a collaboration ACL. Collection reads enumerate granted centers and invoke the same per-center reader; they never serialize foreign or out-of-scope metadata.
4. Messaging requires `message`; running arbitrary branches requires `control` and `costly`, conservatively, because effects cannot be inferred from branch names. Execution keeps the key's outside origin and generation; downstream authority checks re-read the key. Reads need `read`, mutations `control`, unknown/external effects additionally `costly`; a message-only key cannot launder message access into tool control. Sensitive consent still requires inline-connect-and-approve's protected owner session. No API route accepts approval or grant edits.
5. REST authentication is separate from OAuth and accepts keys only at `/api/v1/`. Protected key management uses an exact same-origin cookie-authenticated `/app/api-keys` endpoint; the app links to a first-party Connected apps / API keys view. Neither app OAuth nor API-key bearers create owner proof.
6. Public endpoints: GET `/api/v1/openapi.json`, `/command-centers`, `/command-centers/{id}`, plus `/state`, `/activity`, `/connections`, `/runs`, `/runs/{run_id}`, `/agents/{agent_id}/conversation`; POST `/agents/{agent_id}/messages` and `/runs` beneath a center. Activity is the existing status snapshot including its activity tail. Responses report the same running/held/error state as MCP; no fabricated completion or idempotency claim.
7. One durable limit of 60 authenticated REST calls/minute/key, including refused scope calls, returns 429 and Retry-After. Atomic SQLite updates share the limit across workers; no secret-bearing logs, URLs, localStorage or cached responses.

## Risks / Trade-offs

- Shared read filtering cannot safely identify every agent contributor: require explicit `*` rather than label shared data as main-agent data.
- Revocation cannot undo already-admitted external effects: retain existing outside effect admission semantics and refuse next calls/queued dispatch.
- Arbitrary branch cost is unknowable at ingress: require both control and costly, while preserving existing spending/approval checks.
- A copied key acts within its scope until revoked: show once, persist only digest, support named replacement and immediate revoke.

## Migration Plan

Additive tables only; no existing owner data rewrite or isolation change. Deploy backend before the Worker REST route. Rollback removes REST exposure while retaining keys/revocations; older MCP remains unchanged and never accepts keys. Validate Linux suites, parity, Worker tests and public MCP canary. This task opens a non-draft PR; live deployment acceptance remains explicitly pending until the deployed SHA and naive-user pass are proven.

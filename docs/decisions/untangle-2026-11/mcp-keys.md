# mcp-keys as built (2026-11-21)

Founder call 6 (`founder-answers.md`): Muse connects both ways. It uses a key on `/mcp`, as ADR-028 says, through the `/api` door's own key check, so one handler serves keys and OAuth alike. It also uses OAuth, which the directory listings need anyway. `apps.json` and `wake-kinds.json` are made to agree. The research is `packages_connectors_research.md` §3 (the Muse door) and slice M1.

1. **One key check for both doors a key opens.**
   - `api-keys.ts` `presentedKey(env, request)` reads the `Authorization: Bearer` header alone (`kit.ts` `bearer`), parses, reads the directory entry and compares hashes. It replaces `verifyKey` and the `/api` door's own `bearer` read.
   - `/api` and `/mcp` both call it. Neither reads a query or a cookie: `serve` drops the `Cookie` header before either sees the request.
   - `control.ts` `outsideAccount` is the outside gate (the kill switch) with its record on the binding. Before this, `/api` and `/mcp` each spelled it out.
2. **`/mcp` serves a key through the same handler as an OAuth token.**
   - `doors.ts` `mcpDoor`: the `Origin` refusal first, as before. Then a key that verifies goes to `outsideMcp`, and so does the binding `bindMcp` makes from an OAuth token. `outsideMcp` is the outside gate and then `serveMcp`.
   - A key is held to its own grant. The object answers its profile (`toolProfile`) from the key's grant row, as on `/api`. The key's binding is `bindApiKey`'s: source `api_key`, origin `apiKeyOrigin(id)`, and the mint time as its sign-in. So the key acts as the owner's connected agent within the scopes the owner gave it, and the object's refusals are the same on both doors (`outside client revoked`, `403`).
   - **Any other bearer goes to the OAuth resource, a key that does not verify included.** So a wrong, revoked or deleted key gets the same `401` as an invalid token, `WWW-Authenticate` naming `resource_metadata` with `error="invalid_token"`. An OAuth-capable client still finds the metadata. A key's directory read comes first, as on `/api`.
   - OAuth tokens take the path they took before, unchanged.
3. **Revoked, deleted and expired.**
   - A revoke deletes the directory entry, so the next call is `401` at the edge. While the entry is still visible somewhere, the object refuses the revoked grant (`403`).
   - A key has no expiry: it is live until revoked or deleted. `api-keys.ts` says so.
4. **Rate and quota are the same as `/api`'s.** Neither door counts or caps a caller (Fact 15). Both are bounded by the tier's simultaneous runs, the owner's settings and vendors' limits, which the object holds per call.
5. **An API-key agent may register for a delivery its client subscribes to (`mcp_event`).**
   - `ledger.ts` `register` refused it because "an API key has no such door". A key now reaches the MCP door, and `events/subscribe` there finds the subscriber by its origin, whatever the source. So the refusal had no reason left, and it is removed.
   - An API-key agent is still registered from the owner's browser with the key (`own_card`), never by its own card.
6. **Muse's two ways in, in one place.**
   - `apps.json` `muse` lists both. One: make a key in API keys, then give it to Muse as an `Authorization: Bearer` header in its secure credential prompt. Two: Muse Code runs `muse mcp login`, which uses DCR. Its surfaces name Muse and Muse Code, and its `source` adds Muse Code's page.
   - `registration` stays `dcr` until the live test records Muse Code's `client_name` and redirects.
   - `wake-kinds.json`'s `muse_recurring_task` no longer restates how Muse connects ("the owner's ADR-028 key in Muse's Secrets tab"). It points to `apps.json`'s two ways in.
7. ADR-028's Status records that this is built, and that the per-agent rate limit it names is not (Fact 15).
8. **The `Bearer` scheme is read in any case** (review): `kit.ts` `bearer`, the one read of every door's bearer, takes `bearer` and `BEARER` with one or more spaces (RFC 7235 §2.1, RFC 6750 §2.1), as the OAuth provider does. A client that sends `bearer <key>` is served on `/api` and `/mcp`; the owner surface reads its token the same way.

**Left:**
- The owner's connected-apps list (`grants.ts` `apps`, `agents_list`'s `apps`) counts only `mcp` clients by vendor. A key names no vendor, so a Muse connected by a key shows under its agent, not as "Meta Muse connected". A vendor for a key would have to come from the owner at mint. No research covers that yet.

**Owed:**
- [ ] live: at the live stage's first Muse connect, record Muse Code's `client_name` and OAuth `redirect_uris` in `apps.json` `muse` (`match.names`, `match.redirects`), and test a key on `/mcp` from the Muse app's custom connector (research slice M2).

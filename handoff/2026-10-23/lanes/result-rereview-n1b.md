Diff and yardstick read; now pulling spec.md line numbers to anchor the findings.

I reviewed only `git diff 1fe715ca0dd..HEAD`, measured against T1–T10. The shared-app change opens three cross-owner holes, all in Teams and WhatsApp, so the verdict is DISAGREE_EVIDENCE.

## New findings

**HIGH 1. Any owner's box can change verification data for everyone using a shared app** (`design.md:28`, `spec.md:89`, `spec.md:93`)
- Only the platform signing secret gets a platform writer (`design.md:28`). The selector, the pinned public keys and their refresh still come through "the owner's bound relay" and the in-box timed wake.
- For a shared app those keys belong to the app, not to one owner. If they're stored once per app, one owner's box can pin its own key. It can then sign Teams tokens for any tenant and push forged events into other owners' boxes, which breaks T5. It can also change the selector, which re-routes every owner. That breaks T9's one-writer rule.
- If the spec means a separate key copy per installation, it doesn't say so. The router would then have to read the unverified identity to pick which keys to check.
- Fix: shared-app profiles (selector, issuer/audience, keys, secret reference) get one writer, the platform installation authority. The spec must also say who refreshes shared keys with no host online, because shared code may not fetch keys and per-box refresh can't own app-wide keys. That conflict is unresolved.

**HIGH 2. Teams can't meet the spec's own rule that the identity must be authenticated** (`design.md:33`, `design.md:40`, `spec.md:4`, `spec.md:18`)
- As far as I know, the Bot Framework token carries issuer, audience and `serviceUrl` but no tenant claim, and it doesn't sign the body. So "equality to the verified identity claim" has nothing to compare against, and `channelData.tenant.id` stays unsigned.
- Keying on tenant is also too broad. Another person in the same tenant can add the app from the Teams store without going through TinyAssets OAuth. Their private chats with the bot would then route to the first installer's box.
- The token can be reused for about an hour and isn't tied to a tenant. `design.md:42` only says "credential material" is stripped; it must name `Authorization` explicitly.
- Outbound has a similar problem (T6). The Teams bot token is valid across every tenant, and the unchanged rule (`design.md:22`, `:62`) injects "the bound service's credential". That lets A's box message B's users as TinyAssets.
- Fix: take Teams out of shared-app mode in N1 until a fixture proves an authenticated identity per installation, plus installation-scoped outbound credentials.

**HIGH 3. A batched WhatsApp delivery can leak one owner's messages to another** (`design.md:34`, `design.md:40`, `spec.md:19`)
- Meta sends deliveries as an `entry[]` array, and its docs say changes from different objects of the same type can be batched into one delivery.
- A selector like `$.entry[0].id` is allowed, because it's a single scalar. When `entry[1]` belongs to another owner's account, those bytes are forwarded verbatim to the first owner's box.
- The door can't see this as "ambiguous", because noticing the second entry means reading more than one field.
- Fix: as I understand Meta's API, it supports a callback-URL override per WhatsApp business account or phone number. Using that gives each owner their own callback URL with no selector at all. Otherwise, forbid index steps unless a fixture proves the provider never batches across accounts.

**MEDIUM 4. An uninstall can leave a stale mapping, and installs can be hijacked** (`design.md:38`, `spec.md:13-14`)
- Only an owner's own disconnect removes the mapping. A workspace admin can uninstall in Slack instead (`app_uninstalled` / `tokens_revoked`), and only the box can read that event. If A's box is suspended or erased, the door returns 404 and the record stays. "First owner keeps it" then locks the workspace out of TinyAssets permanently.
- The spec doesn't say where the identity comes from. It must come from the provider's server-side token exchange or token inspection (for example `oauth.v2.access`, or Meta's `debug_token`). It must never come from callback query parameters or client-side embedded-signup data. Otherwise an owner can pre-claim another company's account ID and lock it out.
- OAuth `state` must be bound to the owner who started the flow and checked in the same session. Without that, an attacker can send a victim admin their own install link and map the victim's workspace to the attacker's box.
- A refused second install must not revoke tokens at the provider. Slack can re-issue the same workspace bot token on reinstall.
- Fix: when an install conflicts, the installation authority checks the existing credential with the provider. If it has been revoked, it retires the old record and notifies A. Erasure removes the owner's records.

**MEDIUM 5. App-level setup challenges have no box to answer them** (`design.md:34`, `design.md:40`)
- Slack's `url_verification` request has no `team_id`, and Meta's webhook verification GET has no account ID. "Missing identity refuses routing" plus "challenge handling stays in the mapped box" means neither shared app's callback URL can ever be verified.
- Fix: state a one-time platform setup step that sits outside the door.

**MEDIUM 6. One shared endpoint makes every owner share each other's failures** (`design.md:46`, `spec.md:32`, `spec.md:36`)
- Every 404 for an unmapped or unadmitted identity, and every maintenance 503, lands on one app-wide endpoint. As I understand Slack's rules, it disables an app's event subscriptions when almost all deliveries fail for about an hour.
- A long maintenance window, or a pile of dead installs, would cut Slack off for every owner. Re-enabling it is a manual host action, which conflicts with Direction 8.
- Fix: disconnect also uninstalls at the provider. Keep maintenance windows below provider thresholds, and decide whether an unmapped shared-app identity should be acknowledged as a no-op.

**MEDIUM 7. `$.team_id` doesn't always identify the Slack installation** (`design.md:32`)
- In Enterprise Grid org-wide installs the installation is identified by `enterprise_id`, and `team_id` may be missing.
- In Slack Connect, the authorized installations are listed in a truncated `authorizations[]` list. One event can belong to two owners, which makes "one workspace, two owners" possible through different fields.
- Interactivity and slash-command requests are form-encoded, not JSON.
- Fix: the selected field must be the provider's installation identity, and the profile declares the body encoding. Unsupported shapes, including events for several installations, are refused.

**LOW 8. The selector grammar is undefined** (`design.md:40`, `spec.md:19`)
- Pin down: object keys only (see HIGH 3 on index steps), the identity must be a JSON string compared byte-for-byte, strict parsing that rejects duplicate keys at every step of the path, and depth/size caps.
- Parse only after the HMAC check. HMAC-signed bodies keep this from being an injection surface unless the provider signs attacker-shaped JSON, so the remaining risk is parser differences and resource use.

**LOW 9. Replay across owners after a workspace changes hands** (`design.md:42`, `design.md:48`, `design.md:54`)
- The "binding/revision" forwarded in shared-app mode must be the installation record's, so per-box dedupe and "old revisions never resurrect" work for each installation.
- Proof headers (`X-Slack-Signature`, `X-Hub-Signature-256`, `Authorization`) must be named as stripped. Otherwise a box keeps signed requests it could replay to the next owner within the freshness window.

**LOW 10. Uninstall notification retry needs a shared retry queue** (`design.md:38`, `spec.md:14`)
- Retrying the notification needs durable shared retry state, which contradicts `design.md:7` ("no … retry queue in shared services").
- Fix: let the box reconcile its installations against the routing snapshot when it wakes.

**LOW 11. Who may call `INBOUND_VERIFY` isn't restricted** (`design.md:19`)
- If `_connection_op` also serves box connections (T10's `BOX_PUBLISH`), restrict `INBOUND_VERIFY` to the router.
- Resolve the profile in the broker from the binding ID, never from a profile the caller supplies. It's only a verify oracle, not a signing one, so this stays low.

**Not reachable as specified:** the shared signing secret itself. It stays in the broker and is only used for verification. The real exposure is the app-wide outbound credential in HIGH 2.

## Earlier findings
- **MEDIUM, Teams key rotation while asleep:** resolved for per-binding profiles. There's a timed refresh wake, a retryable 503 `verification_keys_unavailable`, and `test_key_rotation_while_asleep` (`design.md:28`, `:80`; `spec.md:89`, `:91-94`). It comes back in another form for shared apps (HIGH 1). Nit: JWKS keys usually have no expiry, so "before pinned keys expire" needs a stated refresh interval.
- **LOW, router-side verifier placement:** resolved (`design.md:19`, `:24`; `spec.md:40`).
- **Nit, HMAC encoding and prefix:** resolved (`design.md:28`; `spec.md:96-98`).
- **Nit, owners creating their own apps:** superseded by the founder decision (`design.md:38`).

The provider facts above — Bot Framework token claims, Meta batching and callback overrides, Slack's disable threshold — come from my knowledge of those APIs, not from anything in the repo. Check each one with a fixture before acting on it.

VERDICT: DISAGREE_EVIDENCE

**Verdict:** HIGH 1, 2 and 3 are fixed. A cross-owner availability gap from MEDIUM 6 is still open, so the verdict is DISAGREE_EVIDENCE.

## (1) HIGH 1–3

- **HIGH 1 (any box could change a shared app's verification data): resolved.** Only the platform installation authority writes shared-app profiles, and boxes can't publish or refresh them. The shared JWT and key-refresh machinery is deleted. See `design.md:37`, `spec.md:34`, `spec.md:52` and `spec.md:62`.
- **HIGH 2 (Teams can't authenticate the identity): resolved.**
  - Teams is out of N1: `design.md:31`, `spec.md:10`.
  - `Authorization` is now named as stripped: `design.md:59`, `spec.md:67`.
  - Outbound credentials are scoped to the installation, and no app-wide token is exposed: `design.md:49`, `design.md:77`, `spec.md:52`, `spec.md:97`.
- **HIGH 3 (batched WhatsApp delivery): resolved as a gate.** Each owner gets their own callback URL through the per-account override, and nothing is routed by body field. WhatsApp only stays in N1 if a fixture proves both the override and that batches never mix accounts. There is no selector or array-index fallback. See `design.md:28`, `design.md:33`, `spec.md:9`.

## (2) Remaining cross-owner paths

I found no way for a box to write shared-app profile data. Only the router may call `INBOUND_VERIFY`, and the broker resolves the profile from the binding (`design.md:15`).

**A. MEDIUM (follows from the spec's own text): one owner's working but failing box can switch Slack off for every owner.**
- A box only returns 2xx after its own commit (`design.md:67`). Maintenance, a cold-start timeout or a full box returns 503 or 429 (`design.md:66`, `design.md:75`).
- `spec.md:31` forbids a 2xx no-op that would hide the loss of an admitted event.
- The receiver code is in the owner's packages (`design.md:17`), so an owner can make it fail or hang on every event, by accident or on purpose. They can also generate as many events as they like in workspaces they control.
- The spec cites Slack's rule that subscriptions are disabled after more than 95% failed deliveries in 60 minutes (`design.md:69`). Under that rule, one owner can disable the shared app for everyone. Turning it back on is a manual host action, which conflicts with Direction 8.
- The MEDIUM 6 fix covers dead installations and maintenance, but not this case.
- Fix: when one installation's failures pass a set threshold, the router moves it to the 200 no-op row. Only that owner loses events, and their box reconciles when it wakes. `spec.md:31` would need amending to allow this.
- Needs a fixture: whether the threshold is app-wide, and whether a cold start fits inside Slack's roughly 3-second delivery timeout.

**B. Needs a fixture: a box could point its own WhatsApp account's callback at another owner's binding.**
- The box can call the provider through the proxy with its own account token (`design.md:77`). With that token it could set its account's callback override to B's `/hooks/inbound/{binding}`.
- The router routes by URL only (`design.md:28`), and nothing requires a box to check the account ID inside the event.
- The only defence is Meta's verification GET, answered by the platform setup responder (`design.md:55`), and the binding segment being hard to guess.
- Fixture: Meta does verify override URLs, and the responder rejects any verify token that isn't the platform's.
- Cheap guard: the box ignores any entry whose account or phone ID isn't its own installation's.

**C. Needs a fixture: overlapping Slack org-wide and workspace installs.**
- Records are keyed `(app, kind, id)` (`design.md:49`). So owner B's org-wide install of enterprise E and owner A's workspace install of a workspace inside E don't count as a conflict.
- Because Slack truncates `authorizations` (`design.md:45`), an event from that workspace could route to either owner.
- Fixture: whether Slack allows both installs at once. If it does, the installation authority must treat an install that covers another as a conflict.

**D. Needs a fixture: connected email push.**
- `design.md:30` requires a per-binding callback, but not a per-binding event source.
- If Gmail-style push uses one shared Pub/Sub topic, every subscription gets every owner's notifications (email address and history ID).
- The source must be per binding.

**E. LOW: box-requested retirement isn't scoped.**
- `design.md:53` and `spec.md:43-44` let a box ask for an installation to be retired, but the authority isn't told to accept requests only for the caller's own installations.
- The authority should also confirm the revocation with the provider itself.
- The box's "routing snapshot view" should be filtered to that owner's records. Otherwise A's box could retire B's mapping.

## (3) MEDIUM 4–7

- **MEDIUM 4 (stale mappings and install hijacking): resolved.**
  - Identity comes from the provider's server-side exchange, and OAuth state is single-use and checked in the same session: `design.md:49`, `spec.md:34`.
  - On a competing install the existing credential is checked: a live one keeps its owner, an unclear result defers, a revoked one is retired. A refused install never revokes tokens. See `design.md:51`, `spec.md:36-39`.
  - Erasure removes the owner's records, the box reconciles on wake, and there's no notification retry queue: `design.md:53`, `spec.md:41-44`.
- **MEDIUM 5 (setup challenges with no box): resolved.** Slack's and Meta's setup challenges are a one-time platform step outside the door: `design.md:55`, `spec.md:46-49`.
- **MEDIUM 6 (shared failure budget): partly resolved.** Dead installations get a 200 no-op that takes precedence over maintenance, maintenance is bounded, and disconnect uninstalls at the provider: `design.md:53`, `design.md:64`, `design.md:69`, `spec.md:23-25`, `spec.md:31`. The live failing installation in finding A is still open.
- **MEDIUM 7 (which field identifies the Slack installation): resolved.**
  - The identity is chosen by `is_enterprise_install`: `enterprise_id` for org-wide installs, `team_id` otherwise.
  - Slack Connect, multiple authorizations and form-encoded requests are 200 no-ops, and a singleton list isn't treated as proof of uniqueness.
  - See `design.md:41-45`, `spec.md:15-21`. Which event families count as single-installation is still left to fixtures.

VERDICT: DISAGREE_EVIDENCE

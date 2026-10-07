## Why

The founder's directive (2026-08, verbatim): *"github, twitter, slack, all channels
we have used and future will use should all use this channel set up. they should all
be user built channels that once made other users can use faster or custom change to
their own needs or add others. how ever we currently do slack, github and twitter is
old architecture spaghetti code."* And: *"every user is a founder of their own
universe... my universe just only has the credentials that the founder gave it and it
cant run something it doesnt have"* — isolation is INHERENT in per-universe credentials,
not an allowlist/tier.

The as-built outbound edge contradicts that shape. Each channel is a hand-written
effector module with its own credential resolver, its own hard-coded endpoint, and its
own auth header format, and a `if sink == ... elif sink == ...` chain routes between
them. Concretely, verified against `origin/main` (sha `4b4895d4`):

- **Dispatch is a hard-coded ladder, and there are two of them.**
  `tinyassets/effectors/github_pr.py:2294` (`run_effects_for_branch`) is an
  `if sink == EXTERNAL_WRITE_SINK_GITHUB_PR / elif "windows_desktop" / elif
  "wiki_write_back" / elif "twitter_post" / else unknown_sink` chain, and
  `tinyassets/effectors/__init__.py:83` wraps it with a *second* dispatcher that special-cases
  `github_merge`. Adding a channel means editing platform Python in two places.
- **Credential resolution is N bespoke resolvers, one per service.**
  `credential_vault.py` carries `resolve_github_token` (`:1176`), `resolve_slack_token`
  (`:1201`), `resolve_slack_app_token` (`:1232`), `resolve_llm_api_key` (`:1417`) — each
  with a different record shape and lookup key. There is no general typed-bundle
  connection resolver.
- **Twitter bypasses the vault entirely.** `effectors/twitter_post.py:190`
  (`_resolve_credentials`) reads `TWITTER_API_KEY` / `TWITTER_<HANDLE>_*` from **host
  process env**. That is a direct violation of per-universe isolation: a Twitter post is
  authorized by whatever token happens to be in the daemon's environment, not by a
  credential the universe's founder deposited. `slack_transport.py:58` already does it
  right (vault-first, never fall through to env); Twitter is the counter-example the
  collapse must fix.
- **Every channel POSTs to a hard-coded endpoint with a bespoke auth header.**
  `SLACK_POST_MESSAGE_URL` (`slack_transport.py:38`, Bearer), `_TWEETS_URL`
  (`twitter_post.py:45`, OAuth 1.0a HMAC hand-rolled at `:337`), `https://api.github.com`
  (`outbound_connections.py:650`, Bearer). None of them shares an egress/SSRF boundary,
  because none of them takes a caller-supplied URL — the safety is an accident of the
  URLs being frozen platform constants.

What is *already* general and must be reused, not rebuilt:

- **Consent is already channel-agnostic.** `api/source_channel.py` (host directive
  2026-08-18) implements `approve_source_channel` where "GitHub is one channel type, not
  a special case"; `_approve_sink` (`:297`) routes every sink through the shared
  `effector_consents` store keyed by `(sink, destination)`. The consent grant is not
  spaghetti — the dispatch, credential resolution, and HTTP are.
- **The receipt / journal-before-fire lifecycle is already shared.**
  `effectors/outbound_boundary.py` (`execute_replay_safe_effect`,
  `execute_capped_action`, `hold_unreconciled_pending`) and
  `storage/external_write_receipts.py` are consumed by every effector today.
- **A credential-blind connection ledger already exists — but only for GitHub.**
  `storage/outbound_connections.py` has `ConnectionLedger` /`ScopedConnectionProxy` /
  `CredentialBlindBroker`, a spawned-subprocess proxy where "the credential never crosses
  the boundary." Its `_TrustedNetworkDriver` (`:677`) only knows `github` and
  `test-fixture.*`; Slack and Twitter do not use the ledger at all.

## What Changes

Collapse the per-channel spaghetti into **one general effector + one general resolver +
one egress boundary**, with the existing channels re-expressed as instances:

- Add a general `authenticated_external_call` effector: a single adapter that takes a
  **named-connection reference**, an HTTP method, an endpoint (bound from a template), and
  headers/body, and returns a structured redacted result. It reuses the existing soul-
  authority gate, the shared `effector_consents` gate, and the shared
  `outbound_boundary` receipt lifecycle unchanged.
- Add a **named-connection model**: a connection is `{connection_id, connection_type,
  auth_scheme, vault-resolved secret ref, allowed_endpoints}`. `github` / `slack` /
  `twitter` / `http` are connection *types* (auth-scheme + endpoint-template presets),
  not hard-coded platform gates. Users create / list / revoke connections; a created
  connection is a remixable artifact other users can copy or customize.
- Add a general **typed multi-secret** connection resolver and a general `connection`
  credential record, so the four per-service resolvers collapse toward one lookup. The
  resolver returns a typed bundle (Slack's non-interchangeable bot vs app tokens;
  Twitter's four OAuth 1.0a values) — never a vague single "secret" that would invite
  whole-record exposure. Secrets are resolved ONLY inside the broker child against an
  authority-checked grant, and never enter graph state, packets, evidence, or any
  `credential_ref`-bearing projection returned to a caller.
- Add a **strict SSRF transport + per-connection endpoint allowlist** as the egress boundary
  for the general effector, enforced inside the existing spawned credential-blind broker
  child (`storage/outbound_connections.py`), never in adapter/graph code. This boundary does
  not exist today (it was implicit in frozen endpoint constants); a general caller-supplied
  endpoint requires it. The allowlist is necessary but not sufficient — the transport itself
  must canonicalize the URL, reject non-`is_global` addresses, pin the validated public
  address against DNS-rebinding/TOCTOU while preserving TLS hostname verification, disable
  redirects and ambient proxies, and bound response size/headers/time (see design.md D3).
- **Migrate the live channels onto the primitive, incrementally, behind a per-channel
  atomic selection** — `slack_transport` first (Slice 3's background follow-up needs it),
  then `github_pull_request`, then `twitter_post`. Each channel first BACKFILLS its
  connection into the ledger, then the universe atomically selects legacy-effector-or-broker
  (never both live at once, never a fall-through to another credential source). A channel's
  old module is deleted only after a **semantic equivalence matrix** (design.md D6) proves
  the general path equivalent — not "byte parity", which cannot capture gate order, receipt
  compatibility, or exception behavior.

Per-universe isolation is enforced by construction: the connection resolver returns nothing
when the universe's vault holds no such connection, so the universe simply cannot make the
call. There is no allowlist, tier, or roster — "it can't run something it doesn't have."

### Ownership boundaries

- **Depends on `outbound-boundary-layer`; does not re-own it.** Correction to an earlier
  draft: that change is **not unimplemented** — its connection-resource ledger, spawned
  credential-blind proxy, and replay-safety tasks are marked complete and are **real landed
  code** in `storage/outbound_connections.py` (`ConnectionLedger` :820, `ScopedConnectionProxy`
  :286, `CredentialBlindBroker` :307, spawned worker :159); only its spec deltas are as-yet
  unsynced. It OWNS grants-as-resource, the spawned proxy/broker, machine-readable action-cap
  enforcement, system-derived idempotency, and the derived-identity `github_pull_request`
  receipts (its MODIFIED delta). `credential-vault` OWNS credential custody and resolution.
  This change **consumes both** and owns the narrower, additive layer: the general HTTP
  network driver inside the broker, the strict SSRF transport + endpoint allowlist, the
  channel-type descriptor + request mapping, and the three channel migrations. It does NOT
  redefine grants, caps, or idempotency identity, and it leaves the two requirements
  `outbound-boundary-layer` modifies untouched.
- **Sequencing:** the general adapter's execution seam IS the existing broker — this change
  extends the broker's network driver from GitHub-only to general HTTP and adds a
  broker-side typed-secret resolver + response declassification; there is no separate
  in-process ("Stage A") secret-resolution path, because in-process resolution is not
  structurally credential-blind (introspection/logging/traceback/`__context__` all leak —
  Slack already documents an `Authorization`-in-`__context__` leak at `slack_transport.py:89`).
  No channel migrates until the broker is extended and used.
- **Consumes, does not redefine:** `credential-vault` (adds one general typed-bundle resolver
  + record type; custody model unchanged), `identity-auth-and-access-control` (authenticated
  actor + ownership axes), `external-effect-receipts` (the shared receipt lifecycle),
  `graph-execution-substrate` (packet shape + completion-path dispatch).
- **Sync ordering:** the final `external-effect-adapters` sync must combine this change's
  general-adapter requirements with `outbound-boundary-layer`'s derived-identity receipt
  requirements in one coordinated sync, so neither an as-built limitation nor a half-general
  adapter is left stranded beside the other.

## Capabilities

### Modified Capabilities

- `external-effect-adapters`: the shipped per-sink adapters become instances of one
  general `authenticated_external_call` adapter dispatched from a channel-type registry;
  the named-connection model, the authority-checked resolution seam (no `connection_id`-only
  lookup), the `credential_ref`-suppressing projection, and the strict-SSRF-transport +
  endpoint allowlist are added; `twitter_post` moves from host-env credentials to a
  vault-resolved per-universe `twitter` connection via an atomic per-universe cutover.
- `credential-vault`: adds a general `connection` credential record and a general
  **typed multi-secret** connection resolver returning a per-type bundle (Slack bot vs app
  token kept distinct; Twitter's four OAuth values) resolved only inside the broker child;
  the per-service resolvers remain as thin compatibility wrappers until migration completes.
- `app-outbound-adapter`: the injected Slack `Transport` becomes an instance of the
  general primitive — it resolves a named `slack` connection and dispatches an
  `authenticated_external_call`, rather than owning its own token resolution and POST.

## Impact

Design-gate only; nothing here is shipped behavior. On implementation it affects the
effect-dispatch path (`effectors/__init__.py`, `effectors/github_pr.run_effects_for_branch`),
the vault resolver surface (`credential_vault.py`), the connection registry
(`storage/outbound_connections.py`), the Slack app-reply transport
(`app_outbound_adapter.py` + `slack_transport.py`), and the new egress-boundary module. It
must not be synced until its requirements are implemented, its §14 concurrency/load proof
passes, the opposite-provider (Codex) review returns approve/adapt, and — because it touches
the live Slack/GitHub user surface — a rendered chatbot `ui-test` plus post-fix clean-use
evidence exist. No advertised MCP handle changes; connection management rides existing
`write_graph target=source_channel`-style owner surfaces.

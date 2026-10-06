# Tasks — provision-http-connection-channel (Slice 1: connector connect_http)

## 1. Handler
- [x] 1.1 New `tinyassets/api/http_connection.py::connect_http` — owner admin-ACL
      gate (mirror connect_llm); parse+validate payload (destination, auth_scheme
      == bearer [Slice 1], secret, allowed_endpoints >=1). Channel-agnostic: no
      service is named in code.
- [x] 1.2 Vault deposit via `write_credential_vault` (list-wrapped, http record,
      service==destination==vault key); `credential_ref=vault://http/<dest>`;
      connect_llm-style error mapping; fail-closed.
- [x] 1.3 Idempotent `create_connection(connection_type="http", …, allowed_endpoints)`
      + `grant_connection` with deterministic ids + full-policy conflict-check
      (every immutable field incl. the endpoint allow-list, before the vault write);
      redacted projection return (`status:provisioned`) with a `next` consent hint.
      Never echo the secret.
- [x] 1.4 Wire `operation=="connect_http"` into `universe_server.py` write_graph
      target=connection dispatch; mirror into the packaging runtime.

## 2. Tests
- [x] 2.1 Happy path: owner provisions → vault record written under the key, http
      connection created with the endpoint allow-list, grant bound to the universe,
      projection redacted (no secret/credential_ref).
- [x] 2.2 Auth: anonymous + non-admin (write-collaborator) both get uniform
      not_found; nothing created.
- [x] 2.3 Fail-closed: empty allow-list, `oauth1a`, SSRF endpoint, and cross-owner
      transfer each rejected with nothing mutated.
- [x] 2.4 Idempotency: second call returns the same ids + rotates the secret; a
      changed endpoint allow-list (or ownership/type) returns connection_conflict
      with nothing rotated; inert-self-heal completes after a mid-provision grant
      fault. Full focused suite green (15) + ruff + mirror parity.

## 3. Review + rollout
- [x] 3.1a Codex SHAPE review of the design BEFORE build.
- [ ] 3.1b Codex exact-head review of the hardened diff returns approve/adapt
      (authority/credential-sensitive). Fold-in review in flight.
- [ ] 3.2 Merge + deploy; confirm prod `release_state.git_sha` contains it +
      `--assert-handles` canary still green (no connector handle regression).
      (Initial handler landed #2483 a889d8dc; this is the hardening PR.)
- [ ] 3.3 Host-action: enable `TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED` via
      apply-daemon-env so outbound effects fire live.
- [ ] 3.4 Live proof (as a user, through the desktop app): provision an http
      connection to a real user-chosen external API, build a node with the
      `authenticated_external_call` effect, grant effector consent, run, and
      confirm a real outbound post — no service-specific code involved.

## Follow-ups (prose)
Slice 2: expose connect_http + write_graph target=branch + consent/mint on the
SERVED surface (engine_mcp_server.py) so the universe builds channels itself.
oauth1a/multi-secret bundle; typed http credential validation; a dedicated
policy-update op — the ONLY planned way to change an existing connection's endpoint
allow-list. Slice 1 has NO revoke-then-reprovision path (`revoke_connection` only
stamps `revoked_at`, which then trips the `revoked_at` conflict), so until the
update op lands a policy change requires a new destination.

Deferred hardening (Codex cross-family review, single-founder MVP — defer
concurrency edges post-live per founder law, but tracked):
- **Provisioning TOCTOU across the vault + ledger.** `connect_http` conflict-checks,
  then writes the vault (per-universe admission lock), then inserts the ledger row —
  three steps not under one lock. Two concurrent same-owner provisions for the same
  destination with DIFFERENT policies can interleave so the ledger keeps policy A
  while the vault holds secret B (the second insert fails after the secret rotated).
  Fix = serialize provisioning per (universe, destination) across BOTH the vault and
  the ledger mutation, with a deterministic concurrency regression (§14 proof).
  Same-owner only (not a cross-owner authority breach); low reach while the feature
  flag is off and there is a single founder.
- **Legacy unowned http credential recovery.** Records deposited before http
  ownership was tracked have no owner row; Slice 1 now fails them CLOSED (any owned
  overwrite is refused). A dedicated owner-proven recovery/migration flow is owed so
  a legitimate original owner can re-establish ownership without a silent rewrite.

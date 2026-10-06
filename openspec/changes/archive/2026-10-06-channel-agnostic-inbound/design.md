# Design — channel-agnostic inbound

## Grounding (current code, 2026-08-19 estimate)

- **No inbound webhook route exists.** `create_streamable_http_app()`
  (`universe_server.py:2825`, routes at `:2876`) mounts only OAuth discovery + the canonical
  `/mcp` app. The only other HTTP ingress is Slack-specific (`app_ingress_http.py`, own port
  8002, single-shared-HMAC — the opposite of "any channel can POST"). → Floor 1 is new.
- **The run path is ready.** `run_graph` → `api/runs.py::_action_run_branch` (`:562`, resolves
  `branch_def_id → BranchDefinition`, author-gates via `run_branch`, mints actor
  `universe:<uid>`) → `execute_branch_async` (`runs.py:3187`, returns `queued` in ms). An
  inbound trigger reuses this verbatim.
- **Per-universe vault is ready.** `credential_vault.py` — `resolve_<service>_token(universe_dir,
  connection_id)`. A branch's nodes read only their own universe's vaulted creds. So a webhook
  run acts only via the owning universe's own connections — isolation falls out of this.
- **Floor 3 bus exists but is DORMANT** (`scheduler.py`: `branch_subscriptions`, `emit_event`,
  `_event_loop`, idempotency) — `get_or_create_scheduler` has no production caller, and
  `VALID_EVENT_TYPES` is a closed allowlist. Phase 2 activates it.
- **Twitter is the anti-pattern**: `effectors/twitter_post.py` is a hard-coded outbound sink on
  env-var creds, not node-shaped, not vault-integrated, not inbound. Rebuild as node-shaped +
  vault-backed later; do not extend it.

## Floor 1 — universal inbound webhook (this change's shippable slice)

**Model:** a per-branch, unguessable **hook token** binds one URL to one (branch, universe). A
`POST /mcp/hooks/<token>` resolves the binding and enqueues a run of that branch, as that universe,
with the body as input. This is the direct "layer 1" of the design (per-branch URL → branch);
the event-bus fan-out is layer 3 (Source nodes), a separate trigger type — NOT this slice.

**Components:**
1. **Hook-token store** (`storage/webhook_hooks.py`): a table `webhook_hooks(token PRIMARY KEY,
   universe_id, branch_def_id, created_at, revoked_at)`. `mint(universe_id, branch_def_id)` →
   `secrets.token_urlsafe(32)`; `resolve(token)` → `(universe_id, branch_def_id)` or None (active
   only); `revoke(token)`; `list_for_universe(universe_id)`. Content is only identifiers; the
   token is the secret. Store under the universe's data dir so it is per-universe.
2. **The receiver** (`webhook_inbound.py::handle_hook`, mounted as `Route("/mcp/hooks/{token}", …,
   methods=["POST"])` in `create_streamable_http_app`): read the body (size-capped), resolve the
   token → 404 (indistinct) if unknown/revoked/malformed, else enqueue via the SAME gated run
   path `_action_run_branch`-equivalent with `actor=universe:<uid>`, `inputs={"webhook": {body,
   headers-subset}}`. Per-token rate-limit (reuse the engine run-admit limiter shape). Returns
   202 `{queued: true, run_id}` on success. NEVER trusts anything in the request for identity —
   the token alone decides branch+universe.
3. **The mint/revoke operation** (user surface): a runtime op / MCP-reachable action
   `mint_webhook(branch_def_id)` / `revoke_webhook(token)` / `list_webhooks`, authorized to the
   caller's OWN universe only (a token can only be minted for a branch the caller's universe
   owns — author check via the same ownership the run path uses). Returns the full
   `https://<domain>/mcp/hooks/<token>` URL.

**Security invariants:**
- The token is the only authority; unguessable; per (branch, universe). No header/body field can
  redirect the run. Unknown/revoked/malformed → 404 with no enumeration signal.
- The run executes as the owning universe, author-gated exactly like `run_graph` (a branch the
  universe did not author is refused). So the public route cannot run arbitrary code — only a
  branch the universe already authored + explicitly minted a hook for.
- Body size cap; per-token rate limit; the body is passed as input verbatim (user uploads
  authoritative) but never interpreted as identity.
- **Reachable via the existing tunnel (2026-08-20 update):** the receiver is mounted at
  `/mcp/hooks/<token>` (under `/mcp`, like the onboarding app at `/mcp/app`), so the Cloudflare
  tunnel — which already serves `/mcp/*` — reaches it with NO tunnel/infra change. Its auth
  carve-out exempts exactly `/mcp/hooks/<one-segment-token>` from the MCP bearer challenge (and
  skips any foreign bearer) only when inbound is enabled; the unguessable per-branch token +
  author-gated handler is the boundary. The code lands safe/dark; going live is a single
  `TINYASSETS_INBOUND_ENABLED` flip (env-file via apply-daemon-env), followed by the canary.

## Floors 2 & 3 — forward design (built next, not now)

- **Floor 3 activation:** start `get_or_create_scheduler(base, run_fn=execute_branch_async)` at
  daemon boot (behind the same provider-authority safeguards — effect-only branches need none;
  LLM branches need served authority, the keystone); admit a namespaced `source:<id>` event_type
  past `VALID_EVENT_TYPES`. Then Floor 1 can OPTIONALLY `emit_event` instead of running directly,
  gaining subscription fan-out + idempotency for free.
- **Floor 2 source nodes:** a new kept-live node kind whose channel logic is opaque, supervised
  outside per-run execution (polls or holds a connection), calling `emit_event` on observation.
  Publish/reuse inherited from the existing node/branch versioning. Only needed for non-webhook
  channels (polling/WebSocket) — e.g. a Twitter/X stream.

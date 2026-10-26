# Tasks: no anonymous principal (owned continuation)

Owner: codex (founder-approved takeover 2026-09-03). Preserve the existing
Claude implementation history and all three completed Codex review records;
there is no fourth review round. Continuation work stays in this authority lane
and lands as small, independently tested commits.

- [x] 1. `auth/provider.py`: delete `ANONYMOUS`; `DevAuthProvider` resolves the
  named `UNIVERSE_SERVER_DEV_USER` and `create_provider()` fails loud without
  it in dev mode; `oauth_sessions.user_id` loses its `'anonymous'` default
  (migration fails loud naming any row that carries it).
- [x] 2. `auth/middleware.py`: ContextVar default `None`; `auth_middleware`
  returns `None` for a missing or invalid token in every mode;
  `current_identity()` raises; `current_identity_or_none()` for the presence
  checks (ASGI challenge path, status's `bearer_present`, the app's
  `_app_identity_required`, which compares against `None` instead of the
  deleted sentinel); the ASGI middleware answers 401 on every non-exempt
  path without a valid bearer; the anonymous write classifier, body cap and
  tool registry are deleted.
- [x] 3. Exempt paths as one predicate with a test per row, reusing the
  existing exact-one-segment hook predicate and the traversal-safe connect
  predicate (no wildcard prefixes): `/.well-known/*` and
  `/mcp/.well-known/*`; `/mcp/app` shell and `/mcp/app/token`;
  `/mcp/connect/*`; `/hooks/inbound/<id>`; `/mcp/app/billing/webhook`;
  `/mcp/pulse` (superseded by continuation task 12).
- [x] 4. Identity sources outside auth: `api/permissions.py` (no `"anonymous"`
  return), `api/engine_helpers.py` and every `UNIVERSE_SERVER_USER` fallback
  (deleted), `identity.py` git-author slug (from the bound identity, raises
  with none), `engine_mcp_server._bind_founder_identity` (empty actor is
  `_binding_error`), `api/status.py` (fingerprint from the subject only).
- [x] 5. `runs.actor`: `create_run` and every caller require `actor`; the SQL
  default goes; startup logs at ERROR and lists any `'anonymous'` row (never
  re-dispatched, never fatal); `canonical_dispatch` never treats a missing
  actor as a principal. `oauth_sessions` rows with an anonymous user are
  deleted by the migration.
- [x] 6. Hooks run as their owner: `webhook_hooks` gains `owner_principal_id`,
  stored by BOTH `_action_mint_webhook` and `_action_create_source` from the
  caller and returned by `resolve`; the direct delivery path in `handle_hook`
  passes it as `principal_id` to `enqueue_universe_branch_run`;
  `SchedulerEvent.owner_principal_id` is stamped in `_emit_source_event` and
  flows `_dispatch_event` -> `_inbound_event_run_fn` ->
  `enqueue_universe_branch_run` -> `_bind_run_provider_call` without a second
  lookup; a legacy hook with no owner refuses to deliver, logging its prefix
  (fail closed, uniform 404 to the caller).
- [x] 7. `GET /mcp/pulse`: `git_sha`, `image_tag`, `deployed_at`,
  `uptime_seconds`; no principal, no universe data; served before the auth
  middleware (superseded by continuation task 12: canary-authenticated).
- [x] 8. The `canary` service principal: the bearer resolves to identity
  `canary`; a handle allowlist in `AuthContextMiddleware.__call__` (after the
  bearer resolves, before `await self.app`) validates every item of a single
  or batch JSON-RPC body: `initialize`, `notifications/initialized`,
  `tools/list`, `tools/call get_status` with no arguments, `read_graph
  target=status`, and the wiki canary's exact `write_page` / `read_page`
  shapes; anything else is refused before dispatch.
- [x] 9. Scripts: `_canary_common.py` sends the bearer; `mcp_public_canary.py`
  asserts the 401 challenge on an unauthenticated `initialize` and uses the
  bearer for `--assert-handles`; `deployed_sha.py` reads `/mcp/pulse` and
  keeps its `image_tag` corroboration; the other seven scripts take the
  bearer; all exit 2 naming the variable when unset. The 14 workflows pass
  the secret (`p0-outage-triage.yml`, `restart-daemon.yml` included).
- [x] 10. Website: `live.ts`, `playground.ts`, `site-react/lib/live.ts` read
  `/mcp/pulse`; the playground shows the 401 challenge as step one.
- [x] 11. Tests: every test that asserted an anonymous read asserts the 401 /
  `PermissionError`; a test per exempt row; the hook owner reaches dispatch;
  the canary is refused on every handle outside its allowlist; none
  quarantined. `tests/test_no_anonymous_principal.py` is the change's own
  file. Codex design round 3 landed; code review rounds (<=3) next, verdicts
  in the PR.
- [x] 12. Continuation + live acceptance:
  - reconcile the later-head `get_status.daemon` test shape;
  - add OAuth-only `securitySchemes` to every canonical tool and a bounded
    `_meta["mcp/www_authenticate"]` runtime challenge that never dispatches;
  - prove the corrected matrix: direct OAuth, ChatGPT and Claude resolve the
    same account principal/universe; retire cached pre-OAuth registrations,
    which must prompt linking before returning any tool data;
  - protect `/mcp/pulse` with the canary bearer and remove unsigned website
    callers and the cutover fallback;
  - delete remaining anonymous sinks/defaults and make
    `grep anonymous tinyassets/` empty;
  - keep main specs synced to the shipped server contract; because that sync
    landed before acceptance, archive with `--skip-specs` only after the bearer
    canary, deployed-sha gate, and rendered connector acceptance are green on
    the deployed commit. Verified 2026-09-08: deploy run `34271996544` passed
    the authenticated canary and deployed-SHA containment for `4a1877f0`;
    direct OAuth, rendered ChatGPT and rendered Claude returned the same
    principal fingerprint and home universe; the obsolete no-auth ChatGPT
    registration was disconnected.

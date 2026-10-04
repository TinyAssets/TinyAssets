# Configuration — environment variables

> **Canonical env-var reference.** Moved out of `AGENTS.md` on 2026-06-25 under
> [ADR-002](../decisions/ADR-002-static-vs-dynamic-context-budget.md): this is
> pointer-loaded *reference* content, not always-loaded *behavioral* norms, so it
> should not sit in the every-turn static context. `AGENTS.md` keeps a short
> pointer + the load-bearing invariants; the full catalog lives here.

The daemon reads configuration from env vars. Defaults are CWD-independent so
containerized deploys don't drift based on where the process was launched from.

## Data + paths

| Var | Purpose | Default |
|-----|---------|---------|
| `TINYASSETS_DATA_DIR` | Canonical root for all on-disk state (SQLite checkpoint, LanceDB indexes, per-universe output dirs). Absolute path. | Platform default — Windows: `%APPDATA%\TinyAssets`; Linux/macOS/container: `~/.workflow`. |
| `TINYASSETS_UNIVERSE` | Per-universe override — specific universe dir for the stdio MCP shim (`workflow.mcp_server`). | `$TINYASSETS_DATA_DIR/default-universe`. |
| `UNIVERSE_SERVER_DEFAULT_UNIVERSE` | Which universe ID is active when none explicit. | First subdir of `$TINYASSETS_DATA_DIR`. |
| `TINYASSETS_REPO_ROOT` | Path to the local git checkout for `workflow.producers.goal_pool` + git-backed catalog writes. When unset, resolved via `Path(__file__).resolve().parent.parent`. | Derived from module path. |
| `TINYASSETS_WIKI_PATH` | Canonical root for the cross-project knowledge wiki the `wiki` tool reads/writes. Resolved via `workflow.storage.wiki_path()`; inherits `data_dir()` platform handling when unset. | `$TINYASSETS_DATA_DIR/wiki` (platform default). |

## Account storage quota

One pool per account, shared by all of its universes (`tinyassets.storage_accounting`,
`openspec/changes/account-storage-quota`).

| Var | Purpose | Default |
|-----|---------|---------|
| `TINYASSETS_FREE_STORAGE_GIB` | Storage quota, in GiB, for an account on the free tier. Positive number; an unusable value is announced and the default applies. | `2` |
| `TINYASSETS_PAID_STORAGE_GIB` | The same, for the paid tier. | `20` |

## Run-file custody capacity

| Var | Purpose | Default |
|-----|---------|---------|
| `TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES` | Positive integer operational ceiling for exact retained plus pending run-file custody allocations across this store. Independent of account tier, entitlement and pricing. Invalid values refuse rather than silently fall back. Normal global rollout must configure this before exposing file intake; no per-user setup patch. The internal storage foundation alone does not enable a public file action. | Unset: custody intake refuses `file_custody_not_configured`. |
| `TINYASSETS_RUN_FILE_HEADROOM_BYTES` | Nonnegative integer technical free-space floor on the verified destination filesystem, checked inside allocation admission in addition to outstanding pending bytes. Not an entitlement or price. Invalid/out-of-range values refuse. | `67108864` (64 MiB). |

## Patch-request intake

A patch request (bug, missing capability, idea) is how a user's universe tells
TinyAssets about a gap. It travels as an ordinary cross-user delivery to an
intake **another user owns** — the founder's universe built the current one
through the app like anybody else — so the platform has to be *told* which
intake to offer new universes rather than knowing one by name. No secret is
involved anywhere: the address is a `receiver_id`, and the authority is the
owner's approval of a seeded consent request in their "Waiting on you" rail
(`tinyassets/patch_intake.py`).

| Var | Purpose | Default |
|-----|---------|---------|
| `TINYASSETS_PATCH_INTAKE_RECEIVER_ID` | The `receiver_id` of the intake new universes are offered. 8–128 chars of `[A-Za-z0-9._:-]`; a present-but-invalid value is logged at ERROR on every read and offers nothing — no request seeded, no grant possible, and no delivery gated (an unoffered intake is an ordinary receiver governed by its owner's exposure, so refusing every delivery over a typo here would only break unrelated cross-user work). The intake's owner still decides exposure (`open_to_all` / `allowed_senders`); approving the seeded request records one `patch_intake` effector consent naming exactly this id and nothing else. | Unset: no consent request is seeded, `read_graph target="pending_requests"` carries no `patch_intake` block, and the served guidance tells a universe this deployment offers no intake. |
| `TINYASSETS_PATCH_INTAKE_LABEL` | What the platform calls that intake in the request the user reads. Display text only — 1–48 printable characters on one line; it confers nothing and names no universe. | `TinyAssets`. |

**Set these in `/etc/tinyassets/env`, not in `deploy/compose.yml`'s `environment:`
block.** The receiver id names a node in an ordinary user's universe (the founder's
today), so it changes whenever that node is re-exposed and must be settable without a
code deploy. Compose's `environment:` **wins over** `env_file:`, so declaring it there
as `${TINYASSETS_PATCH_INTAKE_RECEIVER_ID:-}` would override the env file with empty
whenever the host shell does not export it — silently stopping the offer for every new
user. Getting the id is a founder step: `docs/host-actions.md`.

## Auth + identity

| Var | Purpose | Default |
|-----|---------|---------|
| `UNIVERSE_SERVER_USER` | **No longer confers identity.** It used to name the actor when no request identity was bound; an environment variable must never confer authority over a universe, and there is no anonymous principal to fall back to (founder, 2026-09-02). The actor is the authenticated request subject or the write refuses. | Unset; reading it is a bug. |
| `UNIVERSE_SERVER_HOST_USER` | Host-identity username used when a request is claimed by the box running the daemon (as opposed to an individual operator). | `host`. |
| `UNIVERSE_SERVER_AUTH` | Auth mode. `"true"` / `"1"` enables OAuth-gated MCP; `workos` is production. Unset selects dev mode, where every bearer resolves to the ONE operator named by `UNIVERSE_SERVER_DEV_USER`. No mode admits a request without a bearer. | `false` (dev). |
| `UNIVERSE_SERVER_DEV_USER` | **Required in dev mode**: the single local operator every bearer resolves to. `create_provider()` raises at startup without it, because a fixed default name is an anonymous principal wearing a badge. A request with no bearer is still refused. The stdio transport (Claude plugin, `--transport stdio`) binds this operator, falling back to the OS account name. | Unset — dev mode refuses to start. |
| `TINYASSETS_WIKI_CANARY_TOKEN` | Bearer for the `canary` service principal: the ONLY principal the uptime probes and the container healthcheck use, since no request without a bearer is served. 32+ UTF-8 bytes, matched in constant time. It may probe exactly `initialize`, `notifications/initialized`, `tools/list`, `tools/call get_status` (no arguments), `read_graph target=status`, and the reserved wiki draft; anything else under it is 403 before dispatch. The same value must be the GitHub Actions secret of the same name (`deploy-prod.yml` syncs it into `/etc/tinyassets/env` and refuses to deploy without it). Generate: `python -c "import secrets; print(secrets.token_hex(32))"`. | Unset — every probe exits 2 naming this variable, and the container healthcheck fails. |
| `UNIVERSE_SERVER_PORT` | Port used by `workflow.auth.wellknown` when emitting OAuth metadata URLs. | `8001`. |
| `TINYASSETS_GIT_AUTHOR` | Verbatim override for git commit author (e.g. `"TinyAssets User <user@users.noreply.tinyassets.local>"`). Highest precedence; otherwise the author is derived from the explicit actor argument or the bound identity, and a commit with neither raises rather than being authored by nobody. | Unset (synthetic from the authenticated subject). |
| `TINYASSETS_AUTH_VIABILITY_PROBE` | Codex refresh-viability ladder in `subscription_auth_health` (presence → `last_refresh` freshness fast path → TTL-cached live `codex exec` probe). Catches present-but-dead tokens that pass presence + `codex login status` yet 401 at call time (2026-06-25 queue-poison class; live-proven 2026-07-14). Falsy = `"0"`/`"false"`/`"off"`/`"no"` reverts to presence-only. | `on`. |
| `TINYASSETS_CODEX_AUTH_FRESH_S` | Freshness window (seconds) for `auth.json` `last_refresh` (fallback: file mtime) under which codex auth reads viable without any probe subprocess. Finite positive only. | `86400` (24h). |
| `TINYASSETS_AUTH_PROBE_TTL_S` | Cache TTL (seconds) for live-probe verdicts per `CODEX_HOME` — the supervisor gates every loop tick; the probe must not run per tick. Finite positive only. | `1800`. |
| `TINYASSETS_AUTH_PROBE_TIMEOUT_S` | Live-probe subprocess timeout (seconds); timeout reads inconclusive → "ok" (only a positive dead signature quarantines). Finite positive only. | `120`. |
| `TINYASSETS_IDENTITY_FINGERPRINT_KEY` | Dedicated high-entropy HMAC key for self-only `get_status` / `read_graph target=status` principal fingerprints. Minimum 32 UTF-8 bytes; never reuse OAuth, provider, maintainer, roster, or bearer material. Missing, short, or invalid values leave the status surface available but set `principal_fingerprint` to `null` with an explicit `identity_evidence.status=unavailable` marker. Canonical local-vault key: `scripts/secrets_keys.txt`; production supplies it through `/etc/tinyassets/env`. | Unset; identity evidence is explicitly unavailable while operational status remains readable. |
| `TINYASSETS_IDENTITY_FINGERPRINT_VERSION` | Safe version tag prefixed to deployment-scoped identity fingerprints. Allowed characters: letters, digits, `.`, `_`, `-`. Change when rotating the key so evidence cannot silently cross rotations. Invalid values leave identity evidence explicitly unavailable without weakening or failing the status surface. | `v1`. |
| `TINYASSETS_SESSION_SEAL_KEY` | AES-GCM key sealing the onboarding app's server-side AuthKit refresh-token store (`tinyassets/onboarding/session_store.py`, `$TINYASSETS_DATA_DIR/.runtime/app_refresh_sessions/`), and the HMAC key for the record filenames. **Strictly** 32 bytes as 43-char base64url (one optional `=`) or 64-char hex — standard base64 (`+`/`/`) is rejected, because a lenient decoder turned junk like a `$`-containing value into a key nobody chose. Generate: `python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`. Read and popped from `os.environ` at **import time** of the store module (`arm()`, also called as the first statement of `universe_server.main()`), so no provider subprocess inherits it — never re-export it into a child env. **Set but malformed = `RuntimeError` at startup**, deliberately: an ephemeral fallback would look healthy while logging every user out on each restart. Rotating it invalidates every live session (users re-login once). Vault-first: production supplies it through `/etc/tinyassets/env`; never a committed plaintext file. | Unset — an ephemeral `secrets.token_bytes(32)` per process, with one logged warning: sessions do not survive a daemon restart. |
| `WORKOS_API_KEY` | WorkOS management key (`sk_…`). Used by account deletion to delete the user record (`tinyassets/account_deletion.py`); unset → deletion reports `identity: not_configured` and the host finishes it by hand. |

## Feature flags

Each flag reads as a string; truthy = `"on"`, `"1"`, `"true"`, `"yes"` (case-insensitive). Defaults chosen so out-of-the-box behavior matches current tier-1 contract.

| Var | Purpose | Default |
|-----|---------|---------|
| `TINYASSETS_DISPATCHER_ENABLED` | Master switch for the dispatcher. Off = every request runs inline; on = dispatch goes through the claim/bid surface. | `on`. |
| `TINYASSETS_PAID_MARKET` | Enables the paid-market bid/claim surface. `TINYASSETS_DISPATCHER_ENABLED` must also be on. Phase-G flag. | `off`. |
| `TINYASSETS_GOAL_POOL` | Enables the goal-pool producer in `workflow.producers.goal_pool` — cross-branch goal aggregation. | `off`. |
| `TINYASSETS_PRODUCER_INTERFACE` | Enables the producer-interface surface — multi-producer concurrency for branches. | `on`. |
| `TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED` | Master transport gate for credential-blind outbound HTTP through user-owned connection grants, including compatible realtime Voice bridges. This gate never grants provider authority, selects a provider, or authorizes platform-funded usage; each call still requires its exact owner, universe, grant, method, endpoint, credential, and capability checks. | `off`. |
| `TINYASSETS_CREDENTIAL_BROKER` | `process` starts the credential broker process (S6, change `broker-streaming-contract`) under the daemon and routes every http connection's `proxy.request` through its socket instead of a spawned worker per proxy. Selected but not running is a loud refusal, never a fall back. Until the per-role uid split (daemon / engine children / broker) the daemon refuses to start with it selected. Temporary rollout switch: removed once the broker is the only path. | unset (per-proxy worker). |
| `TINYASSETS_TIERED_SCOPE` | Enables the tiered-memory-scope retrieval router (`workflow.retrieval.router`). Memory scope is tier-gated (node/branch/goal/user/universe). | `off` (Stage 1 monitoring; flip to `on` at Stage 2c per task #19). |
| `GATES_ENABLED` | Enables outcome-gate claims (Phase 6). When off, `gates` tool returns placeholder. | `off`. |
| `TINYASSETS_SUPERVISOR_LIVENESS_TTL_S` | How long `get_status` may reuse a supervisor-liveness snapshot. Computing it reads ~59 per-worker liveness files and was 58% of a status request after the storage walk was cached. The default is sized against the watchdog threshold it feeds (`stuck_pending_max_age_s < 60`), not by feel. `0` disables. | `5` (seconds). |
| `TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS` | How many provider SUBPROCESSES may run at once — the binding capacity constraint on real turns. **Do not raise this from an estimate.** Two attempts to size it analytically were both wrong (once too conservative at ~77 MB/process extrapolated from 4 processes; once too aggressive, from mistaking an average for a marginal slope and budgeting against total RAM instead of available). The measured marginal cost is ~39 MB per process at the `--version` FLOOR, and a real turn also carries a prompt, streaming state and an engine-MCP child process. Raise it when `refused` climbs while `peak_concurrent` sits at the limit in `get_status.provider_admission` — evidence from production, not arithmetic. | `6`. |
| `TINYASSETS_PROVIDER_NESTED_RESERVE` | Provider slots outer (user-facing) turns may NOT take, held for nested work. A served turn holds a slot for its whole subprocess, and that subprocess can call `run_graph`, whose nodes need slots of their own — without a reserve, outer turns starve the children they created and both fail. Nested calls are recognised by the typed `provider_invocation` carrier. Clamped so outer turns always keep at least one slot. | `1`. |
| `TINYASSETS_PROVIDER_ADMISSION_WAIT_S` | How long a turn waits for a provider slot before being refused with an honest, retryable message (Hard Rule 8). | `20` (seconds). |
| `TINYASSETS_STORAGE_SNAPSHOT_TTL_S` | How long `inspect_storage_utilization` may reuse a snapshot. The walk recursively sums every subsystem directory (~3,300 `stat` syscalls, 24-49 ms measured on the live box 2026-08-28) and its cost is O(files on disk), so it must not run per request. `0` disables the memo for an operator who needs the number right now. | `60` (seconds). |
| `TINYASSETS_STORAGE_BACKEND` | Catalog storage backend selection. Values: empty (default), `"git"`, `"sqlite"`. | Empty (auto-select per backend factory). |
| `TINYASSETS_RUN_MAX_CONCURRENT` | Integer cap on concurrent in-flight branch runs. | Unset = unlimited. |
| `TINYASSETS_IDLE_CYCLE_SINGLE_FLIGHT` | Dedupe the no-claim idle heartbeat cycle across fleet workers (`tinyassets/idle_cycle.py`): the winner holds a run lock for the cycle's lifetime (long cycles exclude others; released on process death), and a worker skips when a DIFFERENT worker's stamp is fresh; own stamps never block. Falsy = `"0"`/`"false"`/`"off"`/`"no"`. | `on`. |
| `TINYASSETS_IDLE_CYCLE_FOREIGN_FRESH_S` | Freshness window (seconds) for the idle-cycle stamp; finite positive numbers only (anything else falls back to default). Keep below the supervisor idle respawn period (~322s at backoff ceiling) and above worker phase offset; also the max heartbeat gap after a stamp-holder death. | `240`. |
| `TINYASSETS_PROACTIVE_CADENCE` | JSON object overriding fields of the engagement-decayed proactive cadence (`tinyassets/control_plane/cadence.py` `CadencePolicy`: `engaged_period_s`, `cooling_after_s`, `cooling_period_s`, `dormant_after_s`, `dormant_period_s`, `idle_s`, `active_start`, `active_end`). Setting the three periods equal turns decay off. A malformed value raises; an owner's per-command-center override wins over it. Decay values await founder confirmation. | Unset: 4 h engaged, daily after 7 days, weekly after 30, 30 min idle, 08:00–22:00 owner clock. |

## LLM + provider routing

| Var | Purpose | Default |
|-----|---------|---------|
| `OLLAMA_HOST` | Local Ollama endpoint URL. Presence is the "local-LLM-bound" signal `get_status` reports. | Unset. |
| `ANTHROPIC_BASE_URL` | Alternate Anthropic endpoint (e.g. self-hosted relay). Presence also flips `llm_endpoint_bound` to truthy. | Unset. |
| `TINYASSETS_PIN_WRITER` | Pin a specific writer provider by name (e.g. `"claude-code"`, `"codex"`). Overrides the provider router's fallback chain. | Unset. |
| `TINYASSETS_CODEX_MODEL` | Explicit operator-global CLI model override across universes using this adapter. Not a connection-local user selection. Keep unset in production so the connected CLI chooses its native model; a rejected explicit override is not silently replaced. | Unset/blank: no model flag; outcome label `provider-default` because JSONL does not expose the resolved name. |
| Model credentials (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `XAI_API_KEY`, `TINYASSETS_CODEX_AUTH_JSON_B64`, `TINYASSETS_CLAUDE_CREDENTIALS_JSON_B64`, `TINYASSETS_ALLOW_API_KEY_PROVIDERS`) | **Retired 2026-09-24 -- the platform has no LLM (AGENTS.md Hard Rule 15).** None is set on the platform; `deploy/docker-entrypoint.sh` strips each one unconditionally, and `deploy/retire_platform_llm_logins.sh` removes them from the host after a green deploy. A universe's provider child gets its own `CODEX_HOME` / `CLAUDE_CONFIG_DIR` from that universe's credentials. `tests/test_no_platform_llm_credentials.py` guards the absence. | Never set. |
| `TINYASSETS_CLOUD_DAEMON_SUBSCRIPTION_ONLY` | Deprecated no-op placeholder retained in `deploy/compose.yml` and `deploy/tinyassets-env.template`. No code path reads this flag. | Unset (no-op). |
| `TINYASSETS_HUGGINGFACE_OAUTH_CLIENT_ID` | Public OAuth client id of a Hugging Face app registered for the connect screen's "Sign in with Hugging Face" source (redirect URI `<public origin>/app/model-callback/connect`, no secret). Unset: the client id is this deployment's Client ID Metadata Document, `<public origin>/app/oauth/client-metadata.json` (`tinyassets/onboarding/source_connect.py`). | Unset. |
| `FANTASY_DAEMON_LLM_TYPES` | Comma-separated list of LLM types the fantasy daemon prefers (e.g. `"claude,codex"`). Filters provider selection. | Unset. |
| `TINYASSETS_ENGINE_RESULT_CEILING_BYTES` | Ceiling on a single engine tool result returned to a served agent (`tinyassets/engine_result_bounds.py`). Over it, the agent gets an explicit `truncated: true` envelope with the original size and how to narrow — never a silent clip. Clamped to 4096..262144. | Unset: derived from the window below, else 24576. |
| `TINYASSETS_ENGINE_MODEL_CONTEXT_TOKENS` | The served turn's selected-model context window, written into the engine MCP server env by `claude_provider._engine_mcp_flags` so the ceiling above scales to the model that has to fit the result. Absent on the persistent HTTP engine transport, which outlives any one turn's model choice. | Unset: the 24576 default applies. |

## Observability + uptime

| Var | Purpose | Default |
|-----|---------|---------|
| `DISK_AUTOPRUNE_PCT` | Pressure trigger for bounded, registry-verified daemon image retention; not broad Docker pruning. In `count` mode it grades the pass (relieved/unmet); in `threshold` mode it gates removal. | `85`; must satisfy `0 < low < high < 100`. |
| `DISK_AUTOPRUNE_LOW_PCT` | Relieved-pressure watermark; in `threshold` mode also stops removal. | `75`. |
| `TINYASSETS_DAEMON_IMAGE_RETENTION_MODE` | `count`: every pass removes daemon images outside the keep set (running image, container refs, configured/receipt rollback refs, newer images, two newest older images) regardless of pressure. `threshold`: legacy pressure-gated removal. Other values refuse. | `count`. |
| `TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY` | Retention-only activation: exact `1` plus CLI `--apply` permits bounded image removal. Exact `0` or absent remains read-only; malformed values refuse. Does not change alarms/rotation. | `0` (off until installed dry-run acceptance). |
| `TINYASSETS_IMAGE_RETENTION_STORAGE_PATH` | Operator-verified image-content filesystem for containerd-backed Docker. Missing mapping refuses cleanup; never a deletion target. Classic overlay2 derives DockerRootDir. | Unset. |
| `DISK_WATCH_PATH` | Explicit disk-alarm measurement override only; does not override retention's verified image-store mapping. | Inspected image-store filesystem. |
| `TINYASSETS_MCP_CANARY_URL` | Public MCP URL the uptime canary probes. | `https://tinyassets.io/mcp` (canonical apex; `mcp.tinyassets.io` is an Access-gated internal tunnel origin, not user-facing — host directive 2026-04-20). |
| `TAB_WATCHDOG_INTERVAL_S` | Interval (seconds) for the tray tab-watchdog's polling. `scripts/tab_watchdog.py`. | `60`. |
| `TINYASSETS_CLAUDE_CHAT_SCREENSHOTS` | User-sim skill flag — capture a screenshot on every `claude_chat.py` response settle. Cost: ~200 KB per response. | Unset (off). |
| `TINYASSETS_DEV_HYGIENE_FLOOR_GB` | Dev-box free-space floor for `scripts/dev_hygiene.py` automation: the SessionStart hook escalates below it, and the scheduled full pass also gates its removals on it. An unparseable value falls back to the default rather than failing a session start. Dev-box only; production disk pressure is `DISK_AUTOPRUNE_PCT`. | `40`. |
| `TINYASSETS_DEV_HYGIENE_DISABLE` | Truthy value turns the SessionStart hygiene hook into a no-op. For debugging the hook itself; the scheduled task is separate (`install_dev_hygiene_task.ps1 -Remove`). | Unset (hook runs). |
| `TINYASSETS_OUTBOUND_PROXY_STARTUP_TIMEOUT_S` | Seconds the outbound credential broker waits for its spawned child's ready handshake before failing the call. The wait occupies a run-executor thread and that pool has only four workers, so a few hung startups stall all top-level graph progress for this long — keep it tight. A startup failure now names its cause, so read that before raising this. | `15` (~100x the measured ~0.13s child import). Capped at `120`; an unparseable, non-finite, or ≤0 value is announced on stderr and falls back to the default rather than failing egress. |

**Canonical resolver:** `workflow.storage.data_dir()` is the single
source of truth for `TINYASSETS_DATA_DIR` resolution. Do not re-implement
the precedence logic elsewhere — call the resolver.

**Container deploys:** set `TINYASSETS_DATA_DIR=/data` + bind-mount the
host path to `/data`. See `deploy/README.md` for the full pattern.

## Billing (Stripe)

All three live on the daemon in `/etc/tinyassets/env` (root:tinyassets, 0640). None is
baked into `deploy/compose.yml`: compose `environment` values ship in this repo, and
these are secrets.

| Variable | What it is |
|---|---|
| `STRIPE_SECRET_KEY` | `sk_live_…` in production, `sk_test_…` in sandbox. Authorizes real charges. |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…`, **per-endpoint and per-mode**. Going live means a new endpoint and therefore a new secret. |
| `TINYASSETS_BILLING_ENTITLEMENT_KEY` | Signs the HMAC claim in each subscription's Stripe metadata. **Ours, not Stripe's.** |

Billing is inert unless the first two are set (`billing_enabled()`), so a deployment
without them serves the app with the Upgrade control disabled rather than failing.

**Why the entitlement key is separate from the webhook secret.** The claim proves a
subscription is one *we* created for a given universe. Signing it with Stripe's webhook
secret tied that authority to a key Stripe tells you to rotate — and that you must
rotate the moment it leaks. Rotating it would invalidate the claim on every subscription
already sold, and those subscriptions could then never move a tier again: a later
cancellation would fail authorization and be ignored, leaving someone entitled who had
cancelled. Claims are versioned; `v1` (webhook secret) is still verified so existing
subscriptions keep working, `v2` uses this key, and nothing new is issued as `v1` once
it is set.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Rotating `TINYASSETS_BILLING_ENTITLEMENT_KEY` invalidates every v2 subscription.** Only
rotate it with a re-signing migration, or by adding a `v3` that verifies `v2` alongside.

**Mode safety.** The webhook refuses any event whose `livemode` disagrees with the
configured key, so a leftover test secret on a live deployment fails loudly instead of
letting free test-mode subscriptions grant the paid tier.

Readiness: `python scripts/stripe_go_live.py --check`.

## Owner notifications (push)

A request reaches its owner's registered devices as a notification. Each channel is independent: with neither set, nothing is dispatched and the dispatch reports `no_transport` — a request is durable and readable in the rail on its own, so push is additive to it and never fails the ask. See `tinyassets/notify/`.

| Variable | Meaning | Default |
|---|---|---|
| `TINYASSETS_FCM_SERVICE_ACCOUNT_JSON` | **Secret.** The Firebase service-account document, as JSON, for Android push over FCM HTTP v1. Exchanged for a short-lived access token by signing a JWT with the account's own key; the send endpoint is derived from the document's own `project_id`, never from a caller. Malformed → logged once at ERROR and Android push stays off, rather than raising into whatever raised the request. Creating the Firebase project is a founder action (`docs/host-actions.md`). | unset (Android push off). |
| `ANDROID_GOOGLE_SERVICES_JSON_B64` | **Build-time secret** (Android release build, not the server). Base64 of Firebase's `google-services.json`, materialised into the generated Android project by `mobile/scripts/materialize_google_services.py`; never committed. Absent → the build still succeeds with push **disabled** and logs it. Present but unusable (not base64/JSON, or for another package) → the build fails. `ANDROID_GOOGLE_SERVICES_JSON_FILE` (a path) is the equivalent for a container build; `/keys/google-services.json` is tried when neither is set. | unset (push disabled in the app). |
| `TINYASSETS_WEBPUSH_VAPID_PRIVATE_KEY` | **Secret.** PEM P-256 private key for browser/desktop web push (RFC 8292). **Self-issued** — `python scripts/webpush_keys.py --subject mailto:…` mints it and there is no third party to ask, which is why this channel can be proven live before FCM exists. Rotating it invalidates every existing browser subscription. | unset (web push off). |
| `TINYASSETS_WEBPUSH_VAPID_SUBJECT` | `mailto:` address or `https://` URL a push service uses to contact the sender. Anything else leaves web push unconfigured. | unset. |

`TINYASSETS_WEBPUSH_VAPID_PUBLIC_KEY` is printed by the same script for the **client** to pass to `pushManager.subscribe({applicationServerKey})`. The server never reads it — it is derived from the private key, and storing it twice is how two copies of one fact drift.

## Local secrets — vault-first

Local operator secrets (Cloudflare tokens, DigitalOcean token, Hetzner creds, OpenAI key) load from a password manager, not a plaintext file. Vendor is chosen via `TINYASSETS_SECRETS_VENDOR` — `1password` (default), `bitwarden`, or `plaintext` (migration-period opt-out, to be retired after cutover).

Bootstrap on a fresh machine:

```bash
# 1. install vendor CLI (see docs/design-notes/2026-04-22-secrets-vault-integration.md)
# 2. sign in:
eval $(op signin)                       # 1Password
# or: bw login && export BW_SESSION=$(bw unlock --raw)   # Bitwarden
# 3. load into current shell:
set -a; source scripts/load_secrets.sh; set +a
```

One-shot migration from the legacy `$HOME/workflow-secrets.env`:

```bash
python scripts/migrate_secrets_to_vault.py --vendor 1password --dry-run
python scripts/migrate_secrets_to_vault.py --vendor 1password
# verify, then shred ~/workflow-secrets.env
```

Canonical list of keys: `scripts/secrets_keys.txt` (edit there, not in shell profiles). Full rationale + vendor comparison + bootstrap runbook: `docs/design-notes/2026-04-22-secrets-vault-integration.md`. GitHub Actions secrets are out of scope — they stay in repo settings.

# Broker egress access inventory

Baseline: `21096788fb04a3587b900ed1883ed5183d92be20`. Line numbers below refer to
that unchanged runtime. **Routing status: specified, NOT IMPLEMENTED.** D11
records the lead decision; delivery.md records the physical-parent blocker.

Search scope: production `tinyassets/`, operator/probe `scripts/`, deploy backup,
all ConnectionLedger constructors, raw SQL callers, injected ledger consumers,
and the proxy runtime path. Constructors execute schema/backfill work, so a
caller that only asks for a read is not a read-only filesystem consumer today.
The generated plugin mirror has corresponding copies under
`packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/`; regenerate
it with runtime edits rather than treating copies as independent implementations.
Unit-test temporary ledgers are synthetic fixtures, not production authority.

## Ledger construction entry points

Each daemon route must preserve the current request's trusted principal and
scope. Engine-MCP handlers move to the daemon per D9; engine code never imports
a filesystem-backed ledger as a compatibility fallback. A route cannot expose
`_connect`, a SQL string, a database path, or arbitrary Python methods over IPC.

| File:line | Enclosing operation | Required route |
|---|---|---|
| `tinyassets/api/cloud_connections.py:110` | `_ledger` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/compute_connection.py:99` | `_validate_http_grant` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/connection_uses.py:179` | `apply_connection_uses` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/connection_uses.py:300` | `configure_connection` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/connection_uses.py:128` | `model_use_refusal` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/http_connection.py:794` | `_connect_http` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/http_connection.py:1086` | `_remove_http` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/http_connection.py:1203` | `_rotation_target` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/http_connection.py:1772` | `_extend_preview` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/model_access_requests.py:122` | `_connection_incarnations` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/package_requests.py:107` | `_connections_you_have` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/pending_requests.py:2365` | `_grant_workspace_consent` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/pending_requests.py:1464` | `_owned_connection_git_host` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/pending_requests.py:1267` | `request_from_user` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/provider_capability.py:104` | `configure_provider_capability` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/api/provider_capability.py:181` | `_configure_model_discovery` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/bound_requests.py:228` | `_authority` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/broker/process.py:63` | `ledger_for` | broker-local ledger implementation |
| `tinyassets/effectors/authenticated_external_call.py:929` | `_read_connection_context` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/effectors/authenticated_external_call.py:963` | `_open_connection_proxy` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/effectors/workspace.py:277` | `_read_connection` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/onboarding/connections.py:92` | `run` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/onboarding/model_bootstrap.py:151` | `_pending_confirmation` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/onboarding/model_bootstrap_candidate.py:78` | `prepare_candidate` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/onboarding/realtime_voice.py:126` | `_resolve_voice_binding` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/onboarding/realtime_voice.py:294` | `_default_proxy_factory` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/provider_serving_binding.py:277` | `_open_serving_context` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/provider_serving_binding.py:336` | `_open_connection_id` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/providers/api_key_http_provider.py:222` | `_resolve_proxy` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/providers/api_key_http_provider.py:294` | `_complete_sync` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/providers/connection_lifecycle.py:322` | `intentionally_disconnected` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/providers/connection_lifecycle.py:248` | `fence_connection` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/providers/discovery_http.py:116` | `read_granted_discovery_document` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/providers/discovery_snapshot.py:87` | `_context` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/providers/source_display.py:69` | `source_display_name` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/storage/outbound_connections.py:4936` | `_build_credential_broker_dispatch` | broker-local ledger implementation |
| `tinyassets/ta_capabilities.py:56` | `connections` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/universe_tools.py:1295` | `command_center_summary` | authenticated daemon IPC; owner-scoped operation |
| `tinyassets/workspace_intents.py:370` | `_credential_ref` | authenticated daemon IPC; owner-scoped operation |
| `scripts/probes/capability_url_live_proof.py:286` | `main` | fixture provisioning only before role drop; live probes must use authenticated IPC |
| `scripts/workspace_bwrap_oracle.py:789` | `check_full_route` | fixture provisioning only before role drop; live probes must use authenticated IPC |

## Raw SQL, path providers and injected consumers

| File:line | Current access | Required route |
|---|---|---|
| `tinyassets/api/connection_uses.py:129` | raw capability SELECT through ledger connection | named broker capability-presence query; preserve money-floor refusal |
| `tinyassets/providers/discovery_snapshot.py:88` | transaction snapshot of grant, connection and capabilities | one broker snapshot operation, not independent RPCs that lose consistency |
| `tinyassets/request_budget.py:578` | direct read-only SQLite query for source endpoints | broker source-budget facts, owner/grant scoped; unavailable is not an invented allowance |
| `tinyassets/storage/agent_request_usage.py:345` | direct read-only SQLite grant/source validation | broker-local validation after accounting migration; daemon requests it over IPC |
| `tinyassets/bound_requests.py:161,228` | supplies ledger path for discovery; reads incarnation | scoped broker discovery/query |
| `tinyassets/effectors/workspace.py:254,1692,1903` | resolves ledger path for effector authorization | daemon IPC client; preserve effector admission before egress |
| `tinyassets/effectors/authenticated_external_call.py:638,1068` | resolves ledger path for authenticated external calls | daemon IPC client; existing exact-grant/principal checks |
| `tinyassets/onboarding/model_bootstrap_candidate.py:53` | passes ledger path to HTTP discovery | authenticated daemon-to-broker discovery |
| `tinyassets/onboarding/source_connect.py:44` | passes ledger path to HTTP discovery | authenticated daemon-to-broker discovery |
| `tinyassets/providers/discovery_snapshot.py:202` | passes ledger path to HTTP discovery | authenticated daemon-to-broker discovery |
| `tinyassets/providers/api_key_http_provider.py:289` | derives ledger path from universe parent | scoped IPC authority, not cell-visible path |
| `tinyassets/effectors/outbound_boundary.py:48,96` | injected ledger for effector boundary | typed daemon IPC interface preserving authorization; no database fd |
| `tinyassets/cloud_automation_continuation.py:718,869,1343` | requires injected concrete ConnectionLedger | replace concrete-type coupling with explicit trusted IPC interface; retain admission checks |
| `tinyassets/user_owned_cloud_automation.py:391` | requires injected concrete ConnectionLedger | same explicit interface, not a permissive duck-typing bypass |
| `tinyassets/account_deletion.py:696,755,1059` | generic root DB discovery and owner-row deletion | explicit broker erase-owner operation, counted and acknowledged; never silently skip ledger on relocation |
| `deploy/backup.sh:163,169,171` | root-store glob and SQLite backup API | preserve strict consistent ledger backup if relocated; host maintenance under layout lock is not daemon file authority |
| `deploy/backup.sh:205` | full-volume tar | retain broker-owned bytes and ownership in backup/restore; do not omit private subtree |
| `tinyassets/storage_accounting.py:605,630` | classifies proxy and ledger as platform bytes | classification only, no private-state reader; preserve classification after relocation |

`scoped_reset.py:1011` is an owner-home store classifier, not a root outbound
ledger reader. Its owner reset still requires D10 and explicit treatment of any
broker-held owner state; it must not recursively open private broker state.
`storage/rotation.py:122` walks run transcripts, not these root egress paths.

## Proxy state readers and writers

| File:line | Current operation | Required route |
|---|---|---|
| `tinyassets/storage/outbound_connections.py:6080,6081,6087` | derives ledger, universe and proxy-root configuration from ledger parent | broker-owned configuration; separate logical data root from physical ledger parent if relocated |
| `tinyassets/storage/outbound_connections.py:4931,4932` | creates per-grant runtime directory | broker-only mkdir beneath startup-created private proxy root |
| `tinyassets/storage/outbound_connections.py:4936,4941` | opens ledger and derives accounting data root | broker-local open; explicit data-root/accounting route |
| `tinyassets/storage/outbound_connections.py:4924,4950` | appends audit.jsonl | broker-only file write; daemon audit reads use bounded, filtered IPC |
| `tinyassets/storage/outbound_connections.py:1833,1862,1863` | test-fixture network.jsonl existence/read metadata and append | broker-only when explicitly enabled for fixtures; never production fallback |
| `tinyassets/broker/process.py:71,73,79` | builds and caches dispatchers | broker-owned setup; invalidate/revalidate on current authority as before |

At this baseline `.outbound-proxy` holds per-grant audit/runtime files, not the
broker daemon IPC socket. The latter is currently created by
`tinyassets/broker/process.py:100-106`; D6 moves it to
`/run/tinyassets/broker/broker.sock`. Engine access must use the exact scoped
proxy/relay route from D8, never a bind of `.outbound-proxy` or a private parent.

## Accounting and refresh dependencies exposed by the trace

| File:line | Current authority | Required disposition |
|---|---|---|
| `tinyassets/storage/agent_request_usage.py:189,194` | UsageStore opens `.tinyassets.db`, creates four usage tables | move broker accounting state without moving unrelated multi-tenant tables; daemon create/reserve/receipt/settle operations via IPC |
| `tinyassets/storage/agent_request_usage.py:141,176,401,465` | factory, claim and per-send accounting transactions | broker-local, preserve one-use operation/reference checks and pre-send budget/liveness fences |
| `tinyassets/process_liveness.py:82` | owner-state lock probes | preserve authoritative kernel liveness when accounting moves; no UNKNOWN-to-live fallback or shared daemon-store grant |
| `tinyassets/connection_oauth/tokens.py:274,292,326` | remote provider refresh or local vault-writing refresh | authenticated broker trigger; local vault-writing branch cannot execute unchanged as 1002 |
| `tinyassets/credential_refresh.py:104,149,246,253,260` | lock creation, vault admission, network spend, durable write | preserve lock/admission-before-spend and durable rotation; broker owns refresh state, D4 still forbids vault write |

Every row remains an implementation obligation. Merely switching the ledger
constructor does not route raw SQL, root discovery, proxy setup, accounting or
refresh. No engine-class acceptance or completed routing is claimed here.

## Actual ledger SQL entry points

All of these share `tinyassets/storage/outbound_connections.py:5209` (SQLite
connect). The constructor also makes its parent at `:5158`. Methods not listed
here delegate to these entry points; retaining a daemon ConnectionLedger object
with local `_connect` would violate D11 even if some methods were proxied.

| Method | File:line opening connection | Required route |
|---|---|---|
| `__init__` | `tinyassets/storage/outbound_connections.py:5159` | broker-local; named IPC operation for daemon callers |
| `create_connection` | `tinyassets/storage/outbound_connections.py:5312` | broker-local; named IPC operation for daemon callers |
| `_upgrade_http_connection_scopes` | `tinyassets/storage/outbound_connections.py:5356` | broker-local; named IPC operation for daemon callers |
| `extend_http_connection_endpoints` | `tinyassets/storage/outbound_connections.py:5448` | broker-local; named IPC operation for daemon callers |
| `set_access_mode` | `tinyassets/storage/outbound_connections.py:5511` | broker-local; named IPC operation for daemon callers |
| `access_mode` | `tinyassets/storage/outbound_connections.py:5517` | broker-local; named IPC operation for daemon callers |
| `policy_json` | `tinyassets/storage/outbound_connections.py:5531` | broker-local; named IPC operation for daemon callers |
| `incarnation` | `tinyassets/storage/outbound_connections.py:5548` | broker-local; named IPC operation for daemon callers |
| `_resource_policy_snapshot` | `tinyassets/storage/outbound_connections.py:5565` | broker-local; named IPC operation for daemon callers |
| `_get_connection_resource` | `tinyassets/storage/outbound_connections.py:5590` | broker-local; named IPC operation for daemon callers |
| `list_connection_views` | `tinyassets/storage/outbound_connections.py:5625` | broker-local; named IPC operation for daemon callers |
| `configure_capability` | `tinyassets/storage/outbound_connections.py:5667` | broker-local; named IPC operation for daemon callers |
| `get_connection_capability` | `tinyassets/storage/outbound_connections.py:5764` | broker-local; named IPC operation for daemon callers |
| `grant_connection` | `tinyassets/storage/outbound_connections.py:5803` | broker-local; named IPC operation for daemon callers |
| `get_grant` | `tinyassets/storage/outbound_connections.py:5830` | broker-local; named IPC operation for daemon callers |
| `list_grants` | `tinyassets/storage/outbound_connections.py:5861` | broker-local; named IPC operation for daemon callers |
| `revoke_grant` | `tinyassets/storage/outbound_connections.py:5891` | broker-local; named IPC operation for daemon callers |
| `revoke_connection` | `tinyassets/storage/outbound_connections.py:5910` | broker-local; named IPC operation for daemon callers |
| `delete_connection` | `tinyassets/storage/outbound_connections.py:5938` | broker-local; named IPC operation for daemon callers |
| `resolve_scoped_proxy` | `tinyassets/storage/outbound_connections.py:5961` | broker-local; named IPC operation for daemon callers |
| `_active_resource_for_grant` | `tinyassets/storage/outbound_connections.py:6215` | broker-local; named IPC operation for daemon callers |
| `_active_resource_snapshot_for_grant` | `tinyassets/storage/outbound_connections.py:6237` | broker-local; named IPC operation for daemon callers |
| `create_connector_artifact` | `tinyassets/storage/outbound_connections.py:6311` | broker-local; named IPC operation for daemon callers |
| `remix_connector_artifact` | `tinyassets/storage/outbound_connections.py:6353`, `tinyassets/storage/outbound_connections.py:6369` | broker-local; named IPC operation for daemon callers |
| `get_connector_artifact` | `tinyassets/storage/outbound_connections.py:6382` | broker-local; named IPC operation for daemon callers |

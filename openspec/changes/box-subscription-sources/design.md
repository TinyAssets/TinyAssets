## Context
C6 removed CLI sources. Boxes have node/npm but no provider binaries; image changes and production access are excluded. All execution must use OwnerRuntime.tools.run in the admitted owner box.

## Goals / Non-Goals
Restore subscription sources as editable data, on-demand owner-volume packages, native login and inference. No provider-specific Python. Credentials belong in broker custody; packages and credential-free connection records belong in the owner volume.

## Decisions
JSON definitions contain pinned installation, registry hosts, argv templates, output selectors, login URL/code patterns, success probe and HOME-relative state paths. Owner definitions live in ~/.ta-cli/sources. Install into ~/.ta-cli/<id> with a verified npm lock; pip packages require hashes. Registry and runtime egress use a host-filtered local proxy forwarding to the existing owner proxy. The OS box remains the owner boundary.
Login is bounded background owner activity and exposes only validated URL/code/status. A generic input handoff supports native flows requiring a returned authorization code; no provider OAuth is reimplemented. Completed login is verified using the definition's status command or structural native credential-state checks. Terminal-only CLIs use a generic PTY; a definition may end an interactive login once its native state proves completion.

### Lane A custody correction (binding)
Use the existing owner-bound broker channel and HTTP credential custody namespace. Broker operations read/begin/save/delete source state, protected by the existing vault lock and durable atomic publication. Each entry has an unpredictable generation; save only replaces that generation, so delete invalidates outstanding writes. State is a bounded map of relative file names to base64 bytes, never an archive or host path. Account erasure also removes native custody.

The box serializes each source across processes using a stable tmpfs lock. Login, status and invocation hold that lock through refresh writeback. A private 0700 tmpfs HOME contains leased 0600 files and generated config; HOME and config placeholders point there, while the installed binary remains in the owner package directory or image. Cleanup runs on every exit. Ordinary failed invocations still persist refreshed state. Failed/cancelled login does not publish new custody. No TTL permits overlapping refreshes. A broker generation mismatch fails loudly.

Disconnect cancels and joins pending Login under the login registry lock, then takes the source lock, deletes custody, removes legacy state and the connection/install directory. No new login can begin during this sequence. A one-shot first-use migration deposits legacy declared files before deleting them and never overwrites newer custody. The broker never opens an owner filesystem path. SourceRecord state.location becomes broker; paths describe the transient HOME layout.

### Lane A files by running role

Shared by the daemon container, broker container and box harness:
- `tinyassets/providers/source_record.py`
- `tinyassets/providers/source_record.schema.json`
- `tinyassets/providers/source_records/{claude-code,codex,gemini,kimi}.json`

Broker container, additionally:
- `tinyassets/broker/server.py`
- `tinyassets/broker/vault.py`
- `tinyassets/broker/subscription_custody.py`

Box harness (the owner daemon), additionally:
- `tinyassets/providers/subscription_runtime.py`
- `tinyassets/providers/subscription_lease.py`
- `tinyassets/onboarding/subscription_connect.py`

Shared recovery utility: `tinyassets/restore_recovery.py`; disaster recovery erases native custody alongside HTTP custody. Page: no changes. Box image: no Dockerfile, dependency or pinned package changes; include the box/shared code above in subsequent image builds. Existing verified image binaries remain reusable.

Deploy broker support before the matching shared schema/records and box harness. No production access, deployment, push, external reviewer or real provider sign-in is part of Lane A. The existing C6 production-copy finding remains open pending the lead's live proof. The protected owner login page and native CLI process interface are unchanged.

Lane A local verification: 55 tests passed under `scripts/linux_oracle.py` across `test_subscription_custody.py`, `test_subscription_box_runtime.py`, `test_subscription_source_integration.py`, `test_subscription_login_drain.py`, `test_source_records.py` and the affected heavy `test_mcp_instruction_surfaces.py`. A separate 40-test run also covered `test_broker_credential_custody.py`, `test_broker_vault_modes.py` and `test_vault_account_deletion_guard.py` alongside the native/record regressions. The root-only oracle run of `test_box_restore_authority.py` passed all 65 tests. Changed Python passes ruff; the change passes strict OpenSpec validation and its custody requirement is synced to the main spec. No full suite or production proof was run.
The old subscription_cli contract (cea945a5fa9 parent) supplies prompt/system/model to a CLI and decodes its result. Restore that contract with structural JSON selectors and argv data, not old vendor adapters. HTTP and CLI sources share selection and authority, with distinct executors.

## Risks / Trade-offs
Native browser callbacks may be unreachable from a phone/container; use the CLI's own device flow or explicit returned-code handoff. Never fake completion. Package scripts execute as the owner and only after an explicit connect action. CLI inference must not silently enable API billing.

## Migration Plan
Python/data hotfix; deploy broker custody support before the owner runtime. Lane A makes no production changes. First use moves existing declared login files into custody without requiring another sign-in. The one-shot legacy declaration/acceptance/state bridge is removed with cutover code. Packages remain reusable because their installation digest excludes custody location. Do not roll back to a runtime that writes persistent login state.

## Verified constraints
All four pinned packages install and emit native authorization URLs through the owner relay on Node 22.23.3. No real account login or paid inference was authorized. The fake CLI covers the complete approval, routing and persistence path.
Source models are data. Optional ranking evidence names its source and expiry; normal automatic selection prefers the strongest current comparable evidence, and explicit owner preferences win. Bundled native `default` aliases carry no fabricated benchmark scores. Their relative strength remains unknown until evidence is supplied.
Most sources accept prompts over stdin. The bundled argv-only source declares a conservative 32K context and 100KB invocation envelope to stay below Linux's per-argument limit.

## Hotfix roles

WX converges this data into SourceRecord. The current copy manifest is `../model-connect-by-discovery/hotfix-files.md`; the inventory below records the inherited W implementation.
- Owner daemon: subscription source loader/runtime/catalog/provider; source JSON files; model discovery, selection, provider resolution/routing, serving bindings/assignments, work candidates and authority persistence; connection completion and pending-request projection.
- Box harness: `boxes/boxd.py`, `owner_process.py`, `owner_public_egress.py` (supervised interactive input, PTY, separate stderr and phase-specific egress forwarding).
- Broker: `broker/owner_sources.py`, plus the shared provider definition schema in `providers/definition.py`.
- Shared app/router assets: `onboarding/__init__.py` route table and `onboarding/app/main.js`. The new endpoint is forwarded to the owner's existing box door; no new shared credential store.
No Dockerfile, dependency manifest, native service, firewall or image changes. Install broker schema support before exposing the new app controls; swap/restart the owner daemon and harness together. This lane does not perform that deployment.


## Exact file inventory by role

**Broker + owner daemon (shared source schema)**

- `tinyassets/providers/definition.py`

**Broker**

- `tinyassets/broker/owner_sources.py`

**Box harness**

- `tinyassets/boxes/boxd.py`
- `tinyassets/owner_process.py`
- `tinyassets/owner_public_egress.py`

**Shared app/router and owner app route table**

- `tinyassets/onboarding/__init__.py`
- `tinyassets/onboarding/app/main.js`

**Owner daemon (runtime, definitions, serving and selection)**

- `tinyassets/api/connection_uses.py`
- `tinyassets/api/model_options.py`
- `tinyassets/api/pending_requests.py`
- `tinyassets/onboarding/serving.py`
- `tinyassets/onboarding/subscription_connect.py`
- `tinyassets/provider_assignment.py`
- `tinyassets/provider_serving_binding.py`
- `tinyassets/providers/discovery_snapshot.py`
- `tinyassets/providers/model_policy.py`
- `tinyassets/providers/model_selection.py`
- `tinyassets/providers/provider_resolver.py`
- `tinyassets/providers/router.py`
- `tinyassets/providers/served_model_plan.py`
- `tinyassets/providers/subscription_catalog.py`
- `tinyassets/providers/subscription_provider.py`
- `tinyassets/providers/subscription_runtime.py`
- `tinyassets/providers/subscription_source.py`
- `tinyassets/providers/source_records/claude-code.json`
- `tinyassets/providers/source_records/codex.json`
- `tinyassets/providers/source_records/gemini.json`
- `tinyassets/providers/source_records/kimi.json`
- `tinyassets/providers/work_candidate_data.py`
- `tinyassets/storage/provider_work_authority.py`

**Verification only**

- `tests/manual_subscription_packages.py`
- `tests/test_subscription_box_runtime.py`
- `tests/test_subscription_source_browser.py`
- `tests/test_subscription_source_integration.py`

**Specification only**

- `openspec/changes/box-subscription-sources/.openspec.yaml`
- `openspec/changes/box-subscription-sources/design.md`
- `openspec/changes/box-subscription-sources/proposal.md`
- `openspec/changes/box-subscription-sources/specs/box-subscription-sources/spec.md`
- `openspec/changes/box-subscription-sources/tasks.md`
- `openspec/specs/box-subscription-sources/spec.md`

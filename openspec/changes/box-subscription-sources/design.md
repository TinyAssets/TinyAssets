## Context
C6 removed CLI sources. Boxes have node/npm but no provider binaries; image changes and production access are excluded. All execution must use OwnerRuntime.tools.run in the admitted owner box.

## Goals / Non-Goals
Restore subscription sources as editable data, on-demand owner-volume packages, native login and inference. No provider-specific Python, shared-image install, or credential transfer to the broker.

## Decisions
JSON definitions contain pinned installation, registry hosts, argv templates, output selectors, login URL/code patterns, success probe and HOME-relative state paths. Owner definitions live in ~/.ta-cli/sources. Install into ~/.ta-cli/<id> with a verified npm lock; pip packages require hashes. Registry and runtime egress use a host-filtered local proxy forwarding to the existing owner proxy. The OS box remains the owner boundary.
Login is bounded background owner activity, exposes only validated URL/code/status, and retains secrets in the CLI's HOME state. A generic input handoff supports native flows requiring a returned authorization code; no provider OAuth is reimplemented. Completed login is verified using the definition's status command or structural native credential-state checks. Terminal-only CLIs use a generic PTY; a definition may end an interactive login once its native state proves completion.
The old subscription_cli contract (cea945a5fa9 parent) supplies prompt/system/model to a CLI and decodes its result. Restore that contract with structural JSON selectors and argv data, not old vendor adapters. HTTP and CLI sources share selection and authority, with distinct executors.

## Risks / Trade-offs
Native browser callbacks may be unreachable from a phone/container; use the CLI's own device flow or explicit returned-code handoff. Never fake completion. Package scripts execute as the owner and only after an explicit connect action. CLI inference must not silently enable API billing.

## Migration Plan
Python/data/page hotfix only; deploy owner harness and relevant router/broker files together. This lane prepares a draft PR only. No production changes. Users reconnect because C6 deleted old custody. Reverting code leaves private package/login files in the volume without routing them.

## Verified constraints
All four pinned packages install and emit native authorization URLs through the owner relay on Node 22.23.3. No real account login or paid inference was authorized. The fake CLI covers the complete approval, routing and persistence path.
Source models are data. Optional ranking evidence names its source and expiry; normal automatic selection prefers the strongest current comparable evidence, and explicit owner preferences win. Bundled native `default` aliases carry no fabricated benchmark scores. Their relative strength remains unknown until evidence is supplied.
Most sources accept prompts over stdin. The bundled argv-only source declares a conservative 32K context and 100KB invocation envelope to stay below Linux's per-argument limit.

## Hotfix roles
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
- `tinyassets/providers/subscription_sources/claude-code.json`
- `tinyassets/providers/subscription_sources/codex.json`
- `tinyassets/providers/subscription_sources/gemini.json`
- `tinyassets/providers/subscription_sources/kimi.json`
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

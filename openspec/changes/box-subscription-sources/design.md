## Context
C6 removed CLI sources. Boxes have node/npm but no provider binaries; image changes and production access are excluded. All execution must use OwnerRuntime.tools.run in the admitted owner box.

## Goals / Non-Goals
Restore subscription sources as editable data, on-demand owner-volume packages, native login and inference. No provider-specific Python, shared-image install, or credential transfer to the broker.

## Decisions
JSON definitions contain pinned installation, registry hosts, argv templates, output selectors, login URL/code patterns, success probe and HOME-relative state paths. Owner definitions live in ~/.ta-cli/sources. Install into ~/.ta-cli/<id> with a verified npm lock; pip packages require hashes. Registry and runtime egress use a host-filtered local proxy forwarding to the existing owner proxy. The OS box remains the owner boundary.
Login is bounded background owner activity, exposes only validated URL/code/status, and retains secrets in the CLI's HOME state. A generic input handoff supports native flows requiring a returned authorization code; no provider OAuth is reimplemented. Completed login is verified using the definition's status command.
The old subscription_cli contract (cea945a5fa9 parent) supplies prompt/system/model to a CLI and decodes its result. Restore that contract with structural JSON selectors and argv data, not old vendor adapters. HTTP and CLI sources share selection and authority, with distinct executors.

## Risks / Trade-offs
Native browser callbacks may be unreachable from a phone/container; use the CLI's own device flow or explicit returned-code handoff. Never fake completion. Package scripts execute as the owner and only after an explicit connect action. CLI inference must not silently enable API billing.

## Migration Plan
Python/data/page hotfix only; deploy owner harness and relevant router/broker files together. This lane prepares a draft PR only. No production changes. Users reconnect because C6 deleted old custody. Reverting code leaves private package/login files in the volume without routing them.

## Open Questions
Verify each pinned CLI's exact native login and invocation flags against its shipped package. Strongest-source ranking must use existing model evidence, not invented vendor ranks.

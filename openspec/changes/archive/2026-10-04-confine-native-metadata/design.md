# Design

## Context
The custody lifecycle already checks owner, universe, service, generation and digest before copying a snapshot and again after enumeration. The JSON-RPC transport bypasses the shared spawn point. The shared launcher supports an explicit UniverseView, network isolation, checked egress, resource limits and owned-family teardown.

## Goals / Non-Goals
Goals: enforce confinement at the metadata transport itself, minimize visible owner files, retain atomic metadata results and reliable cleanup. Prove actual denial with synthetic children and fail before spawning when no jail is available.
Non-goals: adding Claude enumeration, changing authority/grants, resolving UID split, opening engine routes, modifying host settings or exercising live credentials/accounts.

## Decisions
1. Require an explicit owning universe and exact launch-snapshot directory at the transport. Validate that the snapshot is a plain direct child of the canonical .runtime/provider-launch-credentials directory. Bind only that snapshot; private /tmp supplies runtime homes. Do not bind the universe root or sibling snapshots.
2. Override any ambient provider launch scope with the exact metadata owner and no engine route. Pass the explicit narrow view to aspawn_owned and require a confined result. A missing scope/view cannot reach the process-family launcher's non-provider fallback.
3. Reuse existing read-only install resolution and Codex wrapper-tree registration. Reject unsafe mount sources through existing jail policy. No generic wrapper parsing or new filesystem exceptions.
4. Preserve direct argv, fixed registered metadata protocol and existing output/time/page/model ceilings. Use the existing owned-family teardown instead of targeting a recycled process-group integer. Close pipes within the existing bound and propagate cancellation.
5. Protocol and process-family unit tests may isolate the jail-construction seam; they make no isolation claim. Separate real_jail-marked tests exercise the shipping metadata adapter and confinement with synthetic credentials, forbidden sibling/platform markers and a host sentinel process. Required hosted proof must execute these cases with zero skips.

## Risks / Trade-offs
Metadata becomes unavailable on unsupported hosts rather than running unconfined. The owner-authorized provider default remains governed independently; no discovered model may use fabricated metadata. A narrow view may reveal undeclared install/runtime requirements, which must be addressed within the same existing constraints. The cloud workspace currently cannot execute user namespaces; that is a local setup limitation, not a passing jail proof. The existing hosted oracle supplies required execution without changing its security configuration. The old concern is not retired until real proof and deployment verification exist.

---
severity: P2
title: Workspace provisioning restoration awaits production-image proof
filed: '2026-10-08'
summary: The owner provisioning cell replaces the retired refusal; production-image proof is still required before resolving this concern.
---

# Workspace provisioning restoration awaits image proof

Checkout, push and push reconciliation run in the workspace-remote owner cell.
Dependency acquisition and offline installation now run in workspace-provision,
with a pinned owner lease and an invocation-scoped registry-only relay. The
relay closes before offline installation. Manifest admission, digest checks,
transfer reservation, storage, memory, output, deadline and cancellation bounds
remain enforced. Unknown completion retains the maximum transfer charge.

The Linux oracle passed 380 focused tests with zero skips on 2026-10-09,
including registry transport, provisioning grammar and mounts, owner launcher,
relay, preview and image-oracle contract tests. This does not yet prove the new
cell starts in the production image. The image oracle now includes an actual
acquisition/offline-install leg. Delete this concern only after that proof.

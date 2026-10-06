---
severity: correctness
title: Production command-center git needs an isolating box provider
filed: '2026-10-05'
summary: L11 reconciles the thin-loop BoxProvider contract, but production registration and git egress binding depend on the unfinished isolating provider and parent role launcher.
---

Draft PR #4513 targets feat/per-role-uid-split. Credential-blind git is proved
through the existing /u jail and synthetic authenticated server. It is not a
production /cc receipt.

The only implementation is tinyassets/boxes/local.py, which explicitly has no
kernel boundary. box-provider-foundation tasks 2.1-2.2 still require gvisor.py,
boxd, host-UDS egress and the isolating contract proof. There are no production
configure_box_provider call sites. deploy/role_owner_launcher.py accepts only
image-decoder, workspace-git, ui-preview and preview-write; its per-owner git
cell is not a general box execution provider.

The L11 continuation fixes the caller's bind keywords, output/exit fields and
refusal type against the canonical contract. It does not change role admission
or select the unisolated driver in production. Resume task 2.4 after the owning
provider/role lanes supply the isolating provider: wire trusted turn/agent grant
identity and invocation-lifetime git routes to its egress, register it behind
the per-role switch, prove synthetic authenticated git there, deploy, run a
real-user app pass and assert the deployed SHA. No real repo capability probe.

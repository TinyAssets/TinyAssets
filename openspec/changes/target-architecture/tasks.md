# Tasks: target-architecture (routing only)

This umbrella owns no delivery tasks. The active convergence changes carry the
work and its acceptance criteria:

- **C6 — per-owner boxes, platform state, credentials and recovery:**
  `openspec/changes/one-box-per-owner/` (S1-S6, S11).
- **C7 — one control plane, wake path and two account limits:**
  `openspec/changes/control-plane-scheduler/` and the folded limits and wake
  changes (S8-S10).
- **C9 — one agent loop, native CLIs as packages:**
  `openspec/changes/control-plane-agent-loop/` (S7).

The old slice plan remains in this change's design for historical rationale;
new work and completion marks belong to the three owning changes above.

## Why

Shape registry row S8 is drifted. `BoxProvider` (`tinyassets/boxes/`, 1.8k
lines) has no production caller. Per-owner isolation shipped instead as 33
`role_*` owner cells (7,938 lines: 26 in `tinyassets/`, 7 in `deploy/`). The
registry's figure of 34 counted one module twice; the guard counts 33. Each
cell has its own admission, its own relay and its own failure modes. There
were 17 cell fixes, six of them (#4576-#4581) the day after the cutover.

The frontier baseline (#4624) puts this item first, because it unlocks the
most:

- **G1:** an always-on agent that finishes a 2-hour task with every device
  off.
- **G4:** a browser the owner can watch and take over.
- **G5:** approvals, an egress gate, audit and undo.

The baseline names one real gap (§2.1). Today the agent can write its own
abilities as files (Direction 3). If rules, egress policy and spend caps
live in the same writable space, a prompt injection can loosen them.

Founder decisions, 2026-10-11:

- (a) Build the clean shape now. Every implementation PR is net-negative in
  core code, with its deletions in the same PR.
- (b) Start in parallel with C3.
- (c) Add an owner-only policy layer that nothing in the box can write.
- (d) Email goes out through the owner's connected account.

## What Changes

- **One box per owner** (ADR-019, amending ADR-012). One gVisor sandbox per
  owner account, run by a box host service outside the daemon container.
  - **Inside the box:** the agent loop, the four tools plus `ta`, child
    sessions, resident processes, the browser, and native CLIs as
    external-agent packages.
  - **Outside the box:** the control plane, the broker (credentials, model
    calls, egress, spend, owner policy, receipts) and the box host.
- **Admission happens once, at box entry.** A session binds a box handle
  once. Every tool call, child session, browser action and CLI run uses that
  handle. This replaces the per-path cell admissions.
- **Lifecycle.** The box stays awake while any session or resident process
  runs. It keeps running when the app closes and when the daemon deploys. It
  suspends when idle and resumes when the control plane wakes it. No host
  machine is involved.
- **Hooks for the next rows:**
  - snapshot and restore (N6), on the same path as off-region backup (S27);
  - a browser and desktop view stream (N4), readable only by its owner;
  - one egress gate per box at the broker (N5).
- **Owner-only policy layer (registry N10).**
  - Approval rules, the egress gate and spending caps form one owner policy
    per account. It is stored in broker-private state.
  - It is written only through the owner door, with the interactive
    owner-session token. The broker verifies that token itself.
  - The broker enforces the policy on every request that leaves the box.
  - The box sees a read-only projection. `ta policy` can read the policy or
    propose a change, never write it.
  - Every policy write and every enforcement decision leaves a receipt.
- **Email.** Email is a connector call on the owner's own account, governed
  by the policy's `people.message` rule. SMTP stays refused at the egress
  floor. There is no platform mail domain.
- **Deletes:**
  - all 33 `role_*` modules;
  - the bounded mapper and the owner launcher;
  - the provider, tool, node, browser and egress jails;
  - the scattered rule stores (`agent_rules.py`, `approval_scopes.py`,
    `broker/http_policy.py`);
  - the eight `scripts/role_*` probes.

  Estimated net: about −11k executable lines in core, and about −14k
  including scripts (design §9).

## Capabilities

### New Capabilities

- `owner-box`: one box per owner, admitted once, with its lifecycle, limits
  and hooks.
- `owner-policy`: owner-only policy, enforced at the box boundary.

### Modified Capabilities

- `runtime-process-roles`: owner cells, the mapper and the launcher's
  privilege are removed. Owner identities become box identities. The one
  migration becomes the box cutover.
- `owner-cell-execution`: cell admission and diagnostics are removed. Tool
  networking goes through the box egress gate.

## Impact

- **Floor class (ADR-015)** for cross-user isolation, authority, money and
  data loss. Tasks P1-P6 and P8 each get a cross-family `peer-agents` round.
- **One maintenance window** (P2), after a verified full backup. No owner
  bytes move: each owner's tree is attached to its box in place, under the
  same UID.
- **Folds:**
  - It deletes `target-architecture` and `box-provider-foundation` at close.
  - It folds the open tasks of `resident-agent-processes`,
    `remote-box-ta-bridge`, `remote-box-extension-runtime`,
    `agent-access-controls` and `platform-state-outside-command-centers`.
- **Consumes:** `credential-vault-reshape` (S28) for browser-session
  custody, and C3 (`one-model-call-path`) for model calls that leave the
  box.
- **Public surface:** no MCP handle changes. A new owner-door route,
  `/app/api/policy`, carries the owner write.

SPEC LANE N5 + N6. Worktree C:\Users\Jonathan\Projects\wf-n5n6-spec, branch spec/n5-n6-egress-undo (from main).
Spec only: one openspec change per primitive (proposal, design, spec delta, tasks) — no product code. Open one PR.

N5, owner-set network egress gate (registry row N5 in docs/architecture.md): owner policy (open, allowlist, or
ask-before-internet) enforced at the box boundary, not in the agent. design.md C7 already puts one public-egress
check at the broker's egress proxy for every box connection, and ADR-022 lists "P5 N5 owner egress policy at the one
relay: open, allowlist, ask". Design N5 as owner policy evaluated by that one check (no second check), where the policy
is written only by the owner (N10 owner-only policy layer; T9 one writer) and "ask" raises an owner approval through
the existing request/approval path. The agent can read but never write it.

N6, undo by box snapshot (registry row N6): snapshot the box filesystem before agent actions, restore on request;
"one snapshot path shared with S27 backup". design.md C1-C2 define per-owner volumes and per-owner snapshots; reuse
that one snapshot path. Decide what triggers a snapshot (cost: snapshot time and storage per owner under the 5 GiB
free tier), retention, what "undo" restores (files only vs. the owner DB and history; consistency with a running
turn), and how it's exposed (owner action in the app; agent can request but the owner confirms).

Both: name the shared code touched (should be none beyond the one egress check and the lifecycle snapshot call), the
tests that prove each guard in the registry ("egress-gate": box egress passes the owner policy; "box-snapshot": one
snapshot path), and what blocks implementation (C6 close-out). Keep each short: decisions, not essays.

SHAPE: read openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 at the top of
openspec/changes/owner-daemon-in-box/design.md before designing anything. The shared layer (router, broker/egress
proxy, waker) holds no owner business logic and no host path to owner data; owner logic runs in the owner's box;
provider credentials stay in broker custody. Before patching any failing test or finding, ask "can this class exist in
the target shape?" If not, move the work and delete the shared path; never add a guard to shared code. AGENTS.md
Direction: provider-agnostic (no provider-specific code), fewer docs and less process.

HARD CONSTRAINTS: no sub-agents (no scripts/peer_agent.py, no claude/codex subprocess). Work only in the worktree
named above unless this brief says otherwise. No production access, no secrets. Never touch git config. Never use
--no-verify. No full suite: run targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff (and
node --check for JS). No model identifiers in commits or PR text. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
PR descriptions end with:
Generated with Claude Code - https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Report: head SHA, what changed, tests run and their result, anything left undone and why.

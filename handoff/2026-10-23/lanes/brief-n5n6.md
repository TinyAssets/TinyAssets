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

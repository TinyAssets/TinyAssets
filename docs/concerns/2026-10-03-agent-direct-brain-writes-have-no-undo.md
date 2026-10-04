---
severity: P2
title: Agent-direct brain writes get no harness history entry, so no Undo
filed: '2026-10-03'
summary: 'D7a records history only for owner-door writes. The agent writes its own MEMORY.md and the other AGENT_BRAIN_FILES straight through its bind-mounted tool jail, which never reaches harness_history, so those changes have no prior-content row and the owner cannot Undo them. Intended for D7a; the gap is coverage, not authority.'
---

# Agent-direct brain writes get no harness history entry, so no Undo

Filed with harness D7a (#4342), which adds `MEMORY.md` to
`universe_tools.AGENT_BRAIN_FILES`. That is deliberate — the agent writes its
own brain continuously (founder direction), so a root `MEMORY.md` is
bind-mounted agent-writable rather than `ro-bind`, and `_promote_brain_files`
promotes an agent-written copy to the root.

What does not follow it: `harness_history` records a prior-content row only on
the **owner door** (`/app/memory`, via `memory_items`). When the agent writes
`MEMORY.md` itself through its tool jail, nothing passes through
`harness_history._write`, so there is no history row and nothing for the owner's
Undo to restore. The same is already true of the other brain files
(`identity.md`, `AGENTS.md` and the rest), which have been agent-writable since
before D7a; `MEMORY.md` joins that set rather than opening it.

So the owner's Undo covers exactly the edits the owner made, and silently
covers none of the agent's. An agent that rewrites persistent memory — whether
by its own judgement or because a prompt injection steered it — leaves the owner
no recorded prior content to go back to.

This is a coverage gap, not an authority gap: every one of these writes is
already inside the owner's own command center, by the owner's own agent, under
the jail. Nothing here lets one account reach another's memory.

## What a fix needs

Journal agent-direct brain writes the same way the owner door does, which means
a history row written on the **jail** side of the boundary rather than in the
request handler:

- the write must still be atomic and link-free (`_replace`'s fresh-inode +
  `os.replace` within a verified dir fd);
- the prior content has to be captured before the agent's write lands, bounded
  by `MAX_PRIOR_BYTES` as the owner door already bounds it;
- history rows need an actor so the owner can tell their own edit from the
  agent's, and `undo` needs to stay digest-checked so a stale Undo is a 409
  rather than a clobber;
- `_promote_brain_files` promotion is a second write site and needs the same
  treatment, not just the in-jail path.

Until then, treat Undo as owner-edit-only in anything user-facing. Do not
describe it as covering everything that changed the brain.

Raised as `DISAGREE_CONCERN` in the cross-family review of `e539def6` on
#4342 and acknowledged in that PR's description.

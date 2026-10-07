## Why

A universe is meant to be the agent's editable brain + project folder: the served
intelligence should read it and write durable changes to it, and those changes
should be injected into the NEXT turn's system prompt. Today only the READ half
exists — the daemon rebuilds the persona system prompt each turn from the OKF
brain files (identity/founder/origin/body + soul + self-model). The WRITE half is
a post-hoc extractor that mines the founder's message; the agent itself has no
in-turn way to read or write its own brain. The raw full-folder version of this
(PR #2475) was rejected as host RCE, so the write half must be a governed surface.

## What Changes

Add two founder-scoped engine-MCP tools to the served universe agent:

- `read_brain` — read the agent's own brain: the OKF grounding bodies
  (identity/founder/origin/body, frontmatter stripped) + self-model + which
  sections are governed-editable. Read-only, pinned to the agent's own universe.
- `write_brain` — durably write those grounding sections (and a learned name)
  through the existing governed writer (`commit_learning` → `apply_soul_edit`):
  only files whitelisted in the universe's `soul.edit.md`, under a per-universe
  lock with compare-and-swap and managed frontmatter. The written files are the
  ones the system prompt is rebuilt from, so the change lands in the next turn.

Safety boundaries (this is the #2475 RCE space):

- `soul.md` is excluded from `write_brain` (its frontmatter carries the executable
  `loop_branch_def_id` / `effect_authority` control-plane).
- The write sink refuses a governed file that is a symlink, is hardlinked
  (`st_nlink > 1`), or resolves outside its universe slot — closing the inode-
  aliasing bypass where a planted hardlink redirects a whitelisted write onto a
  control-plane or external file.
- Every written file is markdown read into the prompt as TEXT, never executed.
- Pinned to the agent's OWN universe; allowlisted (single vetted founder while
  multi-tenant confinement is hardened); per-section size cap; rolling write
  limit that fails closed; least-privilege capabilities (no `costly`/submit).

Wiki/knowledge (`canon`) writes are deliberately NOT part of this change — they
are not injected into the system prompt and belong in a later `read_page` /
`write_page` slice.

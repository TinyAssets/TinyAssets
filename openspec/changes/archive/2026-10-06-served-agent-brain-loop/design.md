## Context

The served turn runs the universe intelligence in an OS sandbox with an empty
tmpfs workspace, so it cannot read/write its universe files directly. The engine
MCP is an HTTP server the daemon runs OUTSIDE the jail; the agent calls it over
loopback with a per-turn bearer. This is the governed seam: the agent proposes a
brain edit; the daemon (bound to the founder identity, pinned to the agent's own
universe) validates and writes it. No raw folder access is granted — which is why
this avoids the PR #2475 host-RCE.

## Why governed, not raw folder

PR #2475 mounted the universe read/write into the workspace; Codex rejected it
because the daemon executes several universe files outside the jail
(`branch_tasks.json`, `soul.md`'s `loop_branch_def_id`, `dispatcher_config.yaml`,
`.claude/settings.json`). A raw write to any of those is RCE. The governed path
writes only self-descriptive markdown grounding files through `apply_soul_edit`,
which are consumed as prompt TEXT, never executed.

## Key decisions

- Reuse `commit_learning` → `apply_soul_edit` (the existing governed writer) rather
  than a new writer: it already enforces the `soul.edit.md` whitelist, a
  per-universe lock, compare-and-swap, and managed frontmatter.
- Exclude `soul.md`: `write_brain` accepts only identity/founder/origin/body (+
  name). soul.md's executable frontmatter must never be agent-writable here.
- Inode-safety at the sink: validate the resolved FILE OBJECT (reject
  symlink/hardlink/outside-resolve), not just the filename string — a planted
  hardlink otherwise redirects a whitelisted write onto a control-plane file.
- `read_brain` returns bodies with frontmatter stripped so a read→edit→write
  round-trip does not nest managed frontmatter.
- Provider surfacing: the served codex turn's `enabled_tools` is narrowed to the
  safe set (read-only commons + read_brain/write_brain); remix/run stay off the
  served path pending their own hardening.

## Risks / open

- Multi-tenant: the allowlist restricts writes to one vetted founder until the
  cross-universe hardening lands; no other users exist yet.
- Durability of the deploy: the engine flag (`TINYASSETS_ENGINE_MCP_TOOLS`) and
  the served-turn wiring must be baked into the deployed image.

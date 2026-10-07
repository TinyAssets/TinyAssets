## 1. Implementation

- [x] 1.1 Add `read_brain` (own-universe, frontmatter-stripped bodies + self-model
      + editable sections; read/list caps; fail-closed unbound).
- [x] 1.2 Add `write_brain` (identity/founder/origin/body + name) routed through
      `commit_learning` → `apply_soul_edit`; soul.md excluded; pinned universe;
      allowlisted; per-section size cap; rolling limit fail-closed; least-priv caps.
- [x] 1.3 Inode-safety guard in `apply_soul_edit` (refuse symlink / hardlink /
      resolve-outside) protecting every soul-edit caller.
- [x] 1.4 Narrow the served codex `enabled_tools` to the safe set (read-only
      commons + read_brain/write_brain); remix/run held off the served path.

## 2. Verification and foldback

- [x] 2.1 Tests: next-turn injection, round-trip no-nest, hardlink refusal,
      oversized refusal, off-allowlist refusal, least-priv caps, fail-closed
      admission, read fail-closed. Ruff clean; plugin mirror parity.
- [x] 2.2 Independent cross-family (Codex) review of the exact head; address
      blockers (hardlink bypass), correctness (canon), and hardening (bounds,
      caps) before rollout.
- [ ] 2.3 Deploy the image + enable `TINYASSETS_ENGINE_MCP_TOOLS` for the vetted
      founder; verify canary green + serving git_sha.
- [ ] 2.4 Live proof: a rendered founder conversation where the agent writes its
      brain and a subsequent turn reflects it from the system prompt; rollback via
      the flag if red.
- [ ] 2.5 On land, sync this delta into `openspec/specs/served-agent-brain-loop/`
      and archive the change.

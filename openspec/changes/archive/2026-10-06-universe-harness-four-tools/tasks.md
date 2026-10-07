## 1. Build

- [x] 1.1 `tinyassets/universe_tools.py`: the tool jail (reusing
      `provider_jail.jail_argv`: `/u`, masks, no network, `--clearenv`, seccomp
      link filter) and `read`/`write`/`edit`/`bash`.
- [x] 1.2 Resource limits: `prlimit` in the jail, wall clock, output cap,
      process-tree count/RSS watch, disk floor, flock slots; fail closed.
- [x] 1.3 Serve the four as engine-MCP handles and add them to
      `SERVED_ENGINE_MCP_TOOLS`; no public connector handle.
- [x] 1.4 Skill index + harness section in the founder turn that has tools.
- [x] 1.5 Mask every hidden root dir but `.runtime` in provider launch views;
      move the claude engine-route config under `.runtime/`.

## 2. Verify

- [x] 2.1 Real-jail proofs in `tests/test_universe_tools_jail.py`, asserted by
      `linux-jail-proof.yml`: foreign reads, no network, limits, skill next turn.
- [x] 2.2 Portable tests: tool inventory, jail argv, fail-closed refusals,
      cross-user refusal with real SQLite authority, edit semantics, index.
- [x] 2.3 Provider, served-turn, jail and universe-intelligence suites; ruff;
      `build_plugin.py`; `check_channel_agnostic.py`.
- [ ] 2.4 Cross-family (Codex) review of the exact head; fold in the verdict.

## 3. Land

- [ ] 3.1 After #3958 merges and deploys: merge, `deployed_sha.py --assert-contains`.
- [ ] 3.2 Live acceptance in the founder's app: the universe makes itself a
      skill, follows it next turn, and reading another universe's folder fails.
- [ ] 3.3 Sync the delta into `openspec/specs/universe-harness/` and archive.

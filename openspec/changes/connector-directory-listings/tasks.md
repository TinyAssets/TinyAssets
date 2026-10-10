## 1. Prepare directory listing

- [x] 1.1 Research dated primary directory sources and inspect the live auth/policy surfaces.
- [x] 1.2 Correct aggregate tool annotations and descriptions, test and regenerate the plugin mirror.
- [x] 1.3 Write submission answers, reviewer test plan, asset list and founder actions.
- [ ] 1.4 Sync the surface spec and run the requested validation gates.
- [x] 1.5 Commit explicit paths, push and open a non-draft PR with remaining readiness gaps.

Validation: 21 MCP tests, 56 isolation tests, 587 structural guards, ruff, plugin
rebuild, schema/ZIP checks and hygiene passed. Task 1.4 remains open solely for
the authenticated canary: no token; secret loader lacks op. Spec is synced.
PR: https://github.com/TinyAssets/TinyAssets/pull/4587. No Drain-Review receipt
per user instruction; its CI scope gate remains blocked.

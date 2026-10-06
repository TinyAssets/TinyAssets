# K1 merge-queue repair

PR #4519 remains draft. `origin/main` was fetched and merged before repair (already contained). Original head: b3dfa3ca88. Initial Linux reproduction: 15 failed, 393 passed, no skips.

## Root fixes

- App reads / remaining MCP actions: the extension revocation read now uses `Owner.read`, preserving complete owner replies and account-switch checks.
- Background authority inventory: classify `RemoteMcp._exchange -> self.stream` and its plugin mirror as the governed remote-MCP connection transport, not a new graph execution root.
- Connect discovery, served systems guidance, guidance preservation / wrong first call, share-after-publish: restore the original resident chapter index, starter skill paths and byte-preserving base64 warning. No prompt budget changes.
- Custom UI discovery: restore the complete `tinyassets.app-ui.v1` component example and update semantics. Extension activation/pinning explanation stays in the on-demand interfaces chapter.
- Full channel access / git scope as verb: preserve the scoped-verb refusal contract on the non-stream HTTP path; binary git IPC remains separately validated and credential blind.
- Control-plane inventory: classify `git_upload.Upload.read` as CALL_SCOPED. Its bounded wait rechecks cancellation, deadline and grant authority and ends with the broker call.
- Onboarding route set: register K1's already-mounted `/app/outside-clients` in the exact route inventory and assert POST-only. It requires an interactive owner session and current owner authority.
- Orphaned answered turn: completion hooks run inside the initialized `_run` lifecycle; `run` retains its unconditional release wrapper over arbitrary completion/failure bodies.
- Storage registry: classify `.outside-client-authority.sqlite3` as platform authorization bookkeeping (grants, revocation generations and effect leases), never quota-gated.

## Verification in progress

- Linux regression set: 421 passed, zero skips, including all 15 failures, prompt budgets and extension hooks.
- Gate selection: `python scripts/affected_tests.py --gate --base origin/main` returns ALL. Advisory selection also returns ALL because auth middleware is in conftest import closure.
- Full CI required runner and supplementary inventory/guidance/storage runs are in progress. No full-green claim until exact outcomes are recorded.
- Plugin rebuild and import probe pass. Ruff checked across changed Python files.
- Claude cross-family review dispatched through peer-agents, pending.

This repair does not claim deployed SHA or real-user acceptance. No guard was removed, skipped, xfailed or weakened. The only guard edit extends the exact route inventory with a POST assertion.

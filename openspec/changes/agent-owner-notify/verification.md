# Verification and remaining work

Branch: `feat/agent-notify-and-self-knowledge`; draft PR: https://github.com/TinyAssets/TinyAssets/pull/4488.

## Reachability

Main and custom agents: `write_graph target="pending_request" operation="notify" payload_json={"title":"Done","body":"Your report is ready"}`.

Workflow agent steps use the same call. Workflow code steps declare `notify` in `tools_allowed` and call `invoke_mcp_action("notify", title="Done", body="Your report is ready")`. Optional `link_to_thread` resolves another agent within the same owner's command center; `item_id` locates a message and `attachment_ref` carries a file reference. Originating agent and recipient are server-bound.

Reuses pending-request storage/projection, Needs-you, existing owner device resolution and push transports (web/desktop/mobile), delivery dedupe ledger, and withdrawal/clear. No new MCP handle, transport or notification rate limit. Notifications never yield a workflow waiting for an answer.

## Results

- Windows combined notification, agent node, guidance, owner notifications, request, bundle, browser, engine and branch-authoring run: 351 passed; 3 existing symlink tests skipped because Windows could not create symlinks.
- Windows affected heavy files `test_branch_runner.py` and `test_node_enqueue_concurrency.py`: 51 passed.
- Linux oracle: 225 passed, no skips, Python 3.11.16, git 2.47.3, bubblewrap 0.12.0, uid 1001. Initial snapshot attempt stopped on a changing source directory; only the completed rerun counts.
- Cross-family review: APPROVE, no floor/correctness findings. Peer fixed the resident guidance size; 19 guidance tests pass. See `review-response.md`.
- CI subsequently found history test doubles without dataset support; guarded the optional item marker and retained the original assertions. Focused history/browser verification and final Linux rerun are recorded below when complete.
- Ruff on affected Python files passes. Plugin mirror parity: 604 files match. OpenSpec change validates; spec synced to `openspec/specs/agent-owner-notify/spec.md`.
- Test hygiene: 8 tests added, 0 removed, 0 tampering findings (parameterized cases are additional).

## Remaining

Keep the PR draft. Merge/deploy and real-user live app proof are pending. No deployment claim is made: `deployed_sha.py --assert-contains 19048946e6a65b7e1d2dc572fc0d36e209bbf580` could not run without `TINYASSETS_WIKI_CANARY_TOKEN`; the prescribed secrets loader failed because the 1Password CLI `op` is absent. Registered-device receipt and a live scheduled notification still need post-deploy proof.

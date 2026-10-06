# Remote box ta bridge evidence

Draft PR #4525, branch `feat/remote-box-ta-bridge`, owner Codex.

## Implementation and decisions

The served thin loop now uses the canonical BoxProvider bind keyword names and
opens the existing engine bash grant for a fixed ta broker-only program. Remote
bash remains in the bound box. No credential, host route or model-selected
authority crosses into it. The reverse channel uses exec output and safe box
writes; replies and request IDs are scoped to the bound execution.

Host SQLite receipts commit intent before dispatch. Same-ID retries return the
recorded result; an interrupted in-flight receipt returns unknown. Cancellation
revokes admission before cancelling pending trusted calls. Effects already sent
are not claimed rolled back. A changed payload with the same ID is refused.

Local capabilities and connection gates remain in `ta_capabilities.py`.
Extensions resolve and execute in the box. Static prompts and provider branches
are unchanged. The extra fixed local jail launch per RPC is a deliberate latency
trade-off for reusing the exact capability enforcement path.

The checkout contains no deployed remote driver. Tests use real BoxProvider
auth, durable exec/write receipts and subprocesses behind real bwrap namespaces.
They do not stub exec, stream, filesystem isolation or capability results in the
engine-parity proof. This is local Linux contract evidence, not deployed proof.

## Verification

- Linux oracle initial regression batch: **83 passed, zero skips**.
- Expanded batch: **101 passed, zero skips**: `test_remote_box_ta.py`,
  `test_agent_loop_box_tools.py`, `test_agent_loop_tool_session.py`,
  `test_agent_loop_served_chat.py`, `test_ta_capabilities.py`,
  `test_ta_capabilities_jail.py`, `test_converse_turn_cost.py`.
- After adding a credential-bearing host sentinel and output-cap revocation:
  **50 passed, zero skips** (remote proofs, box tools and served chat).
- All commands used `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q
  <files> --basetemp /tmp/b`.
- Real faults injected after committed exec starts, consumed stream frames and
  committed reply writes: repeated execution produced exactly one effect.
- Cross-owner, center and turn handles refused; forged authority in real-box
  messages refused by the actual ta dispatcher. Closed turn requests refused.
- Real owner engine handler/local jail/ta broker invocation succeeds from the
  isolated remote process and its catalogue matches local ta.
- Credential custody scans env, process environments and box files, with a
  positive host `/proc` control and an inaccessible host credential file.
- Ruff and whitespace checks passed. Plugin build staged 629 files; import
  probe passed. Prompt budgets unchanged and passed.

Claude review, final hygiene and final main merge are pending. No deployed-SHA
assertion or real-user app pass is claimed; this PR remains draft.

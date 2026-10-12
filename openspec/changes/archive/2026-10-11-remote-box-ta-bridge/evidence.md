# Remote box ta bridge evidence

Draft PR #4525; branch `feat/remote-box-ta-bridge`; owner Codex.

## Final implementation

Remote bash receives only public worker/client source and command text. A private
Unix socket serves ta inside the isolated execution. Requests are framed on exec
output; replies use idempotent, owner/center/epoch/turn-bound execution stdin.
The extended BoxProvider protocol and local reference host implement this path.
No reply files or client copies remain in the durable command-center filesystem.

The existing authenticated engine session sends JSON through a private MCP
resource, which calls the SAME engine_dispatch/Capabilities used by local ta.
The signed launch must grant bash. No new model-facing tool, provider branch or
static prompt is introduced. Extensions resolve and execute in the remote box.

Host SQLite intent receipts commit before dispatch. A reconnect retains its
request ID; delivery IDs separately protect stdin retry. Same-request retries
return the recorded answer or unknown. Changed arguments are refused. New-ID
command reruns are new intent. Cancellation revokes admission before cancelling
pending trusted calls. Already-sent effects are not claimed rolled back.

## Cross-family review

One peer-agents / Claude round, 176 seconds, read-only, no reviewer tests:
**VERDICT: ADAPT**. Authority binding, credential custody and cancellation held up.
No blocking lane collision was found. All findings were AGREE:

- TB-1: replaced the bash relay with private MCP resource transport; no shell
  argument limit, wait-note parser or bash stdout cap lies in RPC transport.
- TB-2: worker readiness distinguishes `remote_ta_worker_unavailable`; Python 3,
  bash and isolated ephemeral runtime requirements are explicit in the design.
- TB-3: replaced durable mailboxes with execution stdin. Replies do not bump file
  generations; scratch client source is ephemeral and cleaned on normal exit.
- TB-4: the CLI retains a request ID across socket reconnects; receipts dedupe the
  same identity, and the spec explicitly distinguishes a new-ID command rerun.

The original review verdict is retained; no post-fix reviewer approval is claimed.

## Linux proof

Run using `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q <tests>
--basetemp /tmp/b`. Real bwrap namespaces, actual box host exec/stream/reply
implementation and durable receipts are exercised, not scripted box outcomes.
The owner-capability test uses a real in-process MCP client, real EngineToolSession
route/authority checks, the shipping resource handler and actual ta dispatcher.
This does not claim a deployed HTTP remote-driver test.

Coverage includes owner success; forged owner/center/turn payloads; foreign
handles and reply handles; closed-turn attempts from a real box; cancellation
mid-call; committed start, stream and reply loss; same-ID reconnects and payload
reuse rejection; process restart receipts; startup failure; 300 KB request and
response transport; and env/proc/files credential scans with a real host process
holding the credential plus a positive host /proc control.

Historical verified slices: 83-pass initial regressions; 101-pass expanded batch;
50-pass follow-up; 51-pass transport-limit batch. Revised transport proofs:
**16 passed, zero skips**. Fixture-order follow-up: **98 passed, zero skips**.
Final affected regression batch: **254 passed, zero skips** in 51.25 seconds.
This includes the remote proofs, box tools/session/served chat, ta capabilities
and jail, engine client/server, unchanged turn budgets, local box driver and
nine explicitly selected BoxProvider execution/auth cases.

An exploratory full box-contract run included the pre-existing unsupported local
disk-bound test skip. It is not acceptance evidence. Final execution/auth tests
are selected explicitly; no test is weakened, skipped or xfailed by this change.
One broader run exposed test fixture import-order contamination (238 passed,
2 failed); the remote proof now uses the existing dynamic-data-root fixture.

Ruff and whitespace checks passed. Plugin build staged 629 files and passed the
import probe. Prompt budgets are unchanged. Final hygiene against origin/main: **12 added, 0 removed,
0 tampering**. Runtime/fix SHA: eb9c9f836ecd5cf9fb61a5be4c6a04827375ebfd. Main spec has been synced and both change and
spec validate strictly.

## Round 2 merge-queue repair (2026-10-06)

Implementation: `f54474fff3`. Merged `origin/main`
`fb22e770bd74333f54786233ba6046ccfb0b03c2` first and re-fetched/re-merged before
push (already up to date). PR #4525 is draft again. #4529 and #4530 were still
open, so neither the structural runner nor workflow generator was available.

1. Added `tests/test_remote_box_ta.py` and its bridge implementation to the
   linux-jail-proof trigger paths.
2. Fenced the receipt store with a captured execution-owner generation. Schema,
   intent and answer transactions use BEGIN IMMEDIATE plus check_fence. Added
   FENCED inventory and independent restore discovery, with no exemption.
   Tests prove a stale bridge cannot insert or dispatch; handover during dispatch
   cannot write an answer; successor retries remain unknown and fresh requests work.

Linux oracle, Python 3.11.17, bubblewrap 0.12.0, uid 1001:

```text
MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q tests/test_owner_stores.py tests/test_linux_jail_proof_workflow.py tests/test_real_browser_proof_workflow.py tests/test_storage_registry_complete.py tests/test_control_plane_inventory.py tests/test_background_authority_inventory.py tests/test_remote_ta_owner_fence.py tests/test_owner_lease.py tests/test_remote_box_ta.py tests/test_agent_loop_box_tools.py tests/test_converse_turn_cost.py tests/test_ta_capabilities.py tests/test_ta_capabilities_jail.py --basetemp /tmp/b
210 passed in 74.39s; zero skips

MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q tests/test_agent_turn_journal.py tests/test_agent_loop_tool_session.py --basetemp /tmp/b
103 passed in 24.57s; zero skips
```

Ruff on all changed Python sources and tests: pass. Plugin mirror: 645 files,
import probe and commit mirror parity pass. Whitespace and strict change/spec
validation pass. Hygiene against origin/main: 14 added, 0 removed, 0 tampering.
No test weakened, skipped or xfailed; static prompt budgets unchanged.

Cross-family review via peer-agents / Claude: exit 0 in 145 seconds,
VERDICT: APPROVE; no floor or correctness findings. AGREE with approval.
Two nonblocking observations accepted: a reserved dispatch may race with handover
and retain an unknown receipt (existing at-most-once contract); database.parent
and the journal's universe parent both resolve to data_dir in production.
No new scope or post-review runtime edits. Main and delta specifications synced.
The optional local actionlint hook lacked its binary; workflow structural checks
passed, and CI remains responsible for actionlint.

### Unperformed deployment work

No deployed remote driver exists in this checkout. Deployment, deployed-SHA
assertion and a real-user app pass remain unperformed; this is a draft plumbing
PR, not a shipped K2/provider-inventory cutover. The remote driver must support
interactive_stdin/send_stdin and the documented isolation/runtime contract.
origin/main a97c17c26ea2a7a25764c02e9e095b87589edc67 was explicitly merged
before the final push (already contained). The branch is pushed and the PR
remains draft. All requested local verification and the one Claude review round
are recorded above.

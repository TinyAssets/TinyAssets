Review the C9 shared-loop and model-stream checkpoints for floor/correctness findings.
Commits: 0109758c and 7c3aed63 on codex/c9-shared-loop, based on 922b8719.
Compose the separate, pending-C6-approval patch /tmp/c9-aclient-inference-usage.patch
before exercising the stream path; branch alone intentionally omits that owned file.

HARD CONSTRAINTS:
- Review yourself; do not dispatch agents or run peer CLIs/subprocess reviewers.
- Do not merge, deploy, change credentials, spend money or delete owner data.
- Do not run the full suite; read the diff and run at most one focused test file.
- Report floor/correctness findings with exact file/line and concrete failure.
- End with AGREE or DISAGREE_EVIDENCE; do not claim review if access failed.

Contracts to examine:
- ExecutionOwner preserves contextvars, executes turns as tasks, drains cancelled
  synchronous writes before releasing task ownership; off_loop futures cannot be
  cancelled by event-loop task teardown.
- ProviderAssignmentAdmission shares writer-priority local state between sync
  callers and task-identified async callers. Async waits and cross-process flock
  retries do not block the loop. Lock path moved from owner root to
  <base>/.agent-sessions/<cc>/.provider-assignment-admission.lock. Mixed old/new
  binaries must not overlap; parent maintenance cutover must enforce this.
- Journal instances pin a KeyLease; mutation transactions retain check_fence and
  reject journal rows created under a different owner generation.
- Coordinator commits begin-round/start-tool before dispatch. Cancelled journal
  workers update the local frontier before cancellation propagates.
- Router reserves and captures budget handles before cancellation can escape,
  drains observers and settlement off-loop, keeps existing authority and usage gates.
- HTTP provider preparation/decoding is one generator. The async transport uses
  ScopedConnectionProxy.stream, retaining scope, peer, fence, grant and usage
  verification in the real broker. Client previews only scanned delta.content;
  tool arguments/reasoning never become previews. Canonical result is journaled.
- The aclient seam ONLY forwards the existing opaque inference_usage document.
  Broader S6 upstream thread limits and refresh cancellation behavior are unchanged.

Evidence:
- 177 core tests passed; 124 provider/interactive/authority tests passed.
- Broad integration: 299 passed and one old fake Channel missing stream; fixed
  explicit test seam, 28 affected/new tests then passed.
- 29 stream/upstream tests passed; mock 500 waiting broker streams use one socket
  and add no worker threads (not a claim about upstream broker worker threads).
- Ruff and git diff --check clean.
- Linux oracle attempted; image metadata mirror.gcr.io/library/python:3.11-slim
  returned Forbidden. Required oracle/live proof remain open; no manual CI dispatch.

Bounded structural repair: f7144464 (cherry-pick independently of stream).
- Fixed platform assignment lock now opens beneath pinned no-follow descriptors;
  base/namespace/center require current uid and no group/world writes. Leaf must
  be a single-link regular file with equivalent custody. No owner root is created.
- POSIX descriptor guarantees are mandatory: Windows admission explicitly refuses.
- Timer classification is call-scoped; old direct Path.open guard exemption removed.
- Source-pooling fixture injects its synthetic coordinator adapter explicitly.
- 83 focused tests passed, 2 skipped (platform coverage); ruff/diff clean.

Broader direct HTTP-caller selection stopped with 93 passed and three failures:
source-pooling adapter mismatch (now fixed); two agent-node owner-write assertions
whose bash bwrap operation fails to create uid mapping on this read-only host.
Those two have not been proven baseline here; treat as unresolved environment gates.
Parent separately reports 21 heavy-suite failures reproduced on baseline 922b8719.

Draft stream PR: https://github.com/TinyAssets/TinyAssets/pull/4940
Base codex/c9-shared-loop-core contains 0109758c. Parent combined draft #4939.
Cross-family review remains pending through parent's working Claude MCP route.
Local Claude CLI is unavailable; this does not mean external review is unavailable.
No merge, deployment, credential change, cost authorization or data deletion done.

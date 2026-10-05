# Remote MCP delivery - 2026-10-05

Branch: `feat/mcp-connect-ladder`, stacked on `feat/per-role-uid-split`
initially at `688a3e91f121c5b299afe2df536e75c5b4f78a00`, now integrated
through foundation `c7a43a85af` (merge `cb6aad0141`).
Draft PR: https://github.com/TinyAssets/TinyAssets/pull/4496.

Current resume status: task 1.2's OAuth machinery is implemented and verified.
MCP request-shape discovery/activation and ta integration remain under 1.6;
the founder's pasted-link-to-tools flow is **not complete**. Earlier remaining-task
lists below are historical receipts. The branch has integrated #4469 and #4483,
including #4483's later `dda3c01331` repair. No deployment is claimed.

## Verified slice: typed attachment metadata

Added broker-owned version-1 HTTP MCP metadata keyed by owner, backing
connection and incarnation. Existing HTTP records and grants remain unchanged.
All reads/writes require the live owner/center grant; updates compare the prior
metadata revision. Removal marks attachment metadata revoked while preserving
the independent HTTP connection. Unknown schema versions fail without deletion.
No upstream session or credential field is accepted.

Windows and Linux oracle: 22 tests passed (attachment and broker capability
suites); Ruff passed; plugin build/import probe and 602-file mirror parity passed.
Task 1.1 remains open until the complete request/card prerequisites are proven.

## Verified slice: remote transport

Added Streamable HTTP protocol negotiation (2025-06-18 and 2025-03-26),
paginated discovery, JSON/SSE parsing and tool calls exclusively through
AsyncBrokerClient. Sessions remain instance-local. Discovery renews expired
sessions; tool calls never retry an uncertain outcome. Catalog hashes and
argument schemas fence stale calls. Broker send guards bind endpoint,
incarnation and metadata revision; attachment revocation serializes with sends.

Linux oracle: 70 passed across attachment, remote protocol, real broker socket,
broker server and upstream stream suites. Windows protocol/storage tests: 23
passed. Ruff passed. The real broker test proves initialize/list/call and
attachment revocation, but is not a live remote OAuth/user-app proof.

## Verified slice: OAuth primitives

Added path-aware protected-resource metadata discovery, exact resource matching,
authorization-server metadata, public DCR followed by advertised CIMD followed
by configured static ID. CIMD reuses the existing TinyAssets-hosted document.
Generic authorization and token exchange/refresh carry the resource. The broker
refuses another resource and disables redirect expansion for resource-bound
tokens. The optional provider directory is unchanged.

Local fake OAuth server covers DCR, real PKCE code exchange, refresh and resource
parameters. Existing OAuth regression tests passed (88 tests before the final
resource-confinement test); the final focused run passed 21 tests. Combined Linux oracle: 169 passed, no skips; plugin import probe and 606-file
mirror parity passed. This is not a
server-held PKCE/owner-session or end-to-end MCP card proof.

## Remaining by task

- 1.1: metadata is implemented; full prerequisite and rollback-cleanup acceptance remains.
- 1.2: discovery/registration/resource primitives are implemented; protected
  server-held generic PKCE and initiating owner-session integration remain.
- 1.3: broker transport core is implemented; production client wiring,
  MCP cancellation notification and durable operation reconciliation remain.
- 1.6: ta catalog/dispatch, annotation-to-owner-policy integration, consolidated
  card activation and durable continuation are not implemented.
- 1.4 remains deferred. 1.5 and 1.7-1.10 are not complete.

The full paste -> card -> sign-in -> tools/list -> tools/call test does not exist.
No browser app proof, deployment, deployed-SHA assertion or as-built spec sync
has occurred; no task checkbox is marked complete.
The stack predates #4469; reconcile card and #4483 owner-session prerequisite
before wiring the user path. Do not claim a working pasted-link connection.

Dependency evidence on 2026-10-05: #4469 was merged to main as
99fa48fd6d837ea22a06a3ced6f31138453cc4ef but is not an ancestor of this stack.
#4483 remains OPEN on fix/consent-asks-owner-session. Both own the request/card
surfaces this lane must consume. The isolation foundation concurrently advanced
through dc34dd2b10, including HTTP deposit and owner-metadata broker routing.
Keep the PR draft and reconcile dependencies before editing their overlapping
request/approval integration surfaces. Do not add a parallel approval mechanism.


## Cross-family review and final verification

Claude review completed successfully (317 seconds); raw report: [review.md](review.md).
Reviewer verdict: **ADAPT**, not APPROVE. One review round, as required. All six
findings were adjudicated and corrected; there was no second review verdict.

1. **AGREE**: enforce the original HTTP endpoint allowlist before narrowing a
   resource-bound token's egress. Regression proves a matching resource URL
   outside the owner's allowlist sends nothing.
2. **AGREE** with the activation authority risk: generic metadata operations
   refuse `active`, reject endpoint changes after draft, and cannot regress a
   connecting record back to draft. Connecting records admit initialization and
   tool discovery only. The active-state transport test explicitly seeds the
   future coordinator result in its fixture; production activation is absent.
   The broker owner channel is not directly available to agent processes;
   nevertheless no future consumer should trust agent-configurable activation.
3. **AGREE**: post-send malformed replies, authority loss and ambiguous HTTP
   statuses become AmbiguousProxyOutcome, with no automatic replay. Matching
   JSON-RPC errors/auth/session refusals retain their typed outcomes. A broker
   END that proves an authority rejection before every network write remains a
   definite GrantResolutionError; a regression verifies this distinction.
4. **AGREE**: authentication failure clears session, negotiated version and
   catalog; subsequent discovery initializes again. Failed initialization also
   resets state.
5. **AGREE**: remove the substring session-echo heuristic, which rejected valid
   short session IDs. Session headers remain private instance state and are not
   projected into metadata/catalog responses. Broker credential scanning remains.
6. **AGREE**: untrusted pattern/patternProperties schemas are refused explicitly
   until isolated, bounded validation exists. A worker thread alone cannot stop
   a Python regex holding the GIL. External schema reference retrieval is denied.

Validation:
- Full post-foundation Linux run before review fixes: 220 passed, no skips.
- Expanded review-fix Linux run including SSRF/HTTP suites: 365 passed, one
  error-classification regression (pre-send revocation reported as uncertain).
  Fixed using the broker's actual END evidence, without weakening that test.
- Final Linux rerun of all changed MCP suites plus capability IPC: **55 passed,
  no skips**. Includes real broker cross-owner denial, inactive-call rejection,
  active calls, revocation, OAuth local server and all six review regressions.
- Final focused Windows run: 44 passed. Ruff and plugin build/import probe pass.
- Mirror parity: 606 canonical files matched after foundation integration.
- Hygiene before the review-fix commit: 21 added / 0 removed / 0 tampering;
  final post-commit result is recorded in the PR.

The final Linux command was `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py --
-q tests/test_mcp_attachment.py tests/test_mcp_remote.py tests/test_mcp_broker.py
 tests/test_mcp_oauth.py tests/test_broker_capability_ipc.py --basetemp /tmp/b`.
No tests were removed or loosened to accommodate the corrections.

## Resume: prerequisite reconciliation (2026-10-05)

Merged isolation foundation through `5b6f175951` without rebase. Integrated the
existing #4469 sheet and continuation implementation and #4483 consent guard
(commits 210ae91df8, bfd76c7d04, 94b8b8385e, 4f4539a73f). The protected answer
wrapper/route from their earlier patch-intake prerequisite is included explicitly;
isolation broker owner metadata reads remain intact. This is dependency
integration, not a second approval engine. #4483 is still open upstream.

Linux oracle: 223 passed, zero skips across MCP, updated workspace broker,
consent, approval scope, continuation, generic OAuth and rollback suites.
Windows: 157 focused tests and 32 browser/removal tests passed. The initial
Linux attempt failed during tar because the working tree changed during copy;
its result is discarded. Mirror parity: 608 files. No deployment claimed.

## Resume slice: session-bound MCP PKCE

Resource-bound flows now generate and seal PKCE server-side and bind the live
initiating owner session. Another session (even of the same owner), logout during
exchange, replay, and pre-approval registration are refused. Finalization takes
owner control before the session write lock, matching the approval coordinator.
Flow schema migration runs inside BEGIN IMMEDIATE.

Claude review: ADAPT; raw report review-resume.md. AGREE to all three findings:
regenerate mirrors; align lock ordering (with contention regression); serialize
schema migration (with four concurrent legacy-open regression). No second review
verdict is implied. Final focused Linux: 130 passed, zero skips (MCP/generic
OAuth, hosted auth/persistence, inline and onboarding model-connect). Windows:
44 passed for MCP/generic OAuth. Plugin build/import, 608-file parity and Ruff pass.

The affected app regression test also exposed imported #4483's transient-answer
message regression (63 passed / 1 failed). The source lane just pushed its repair
as dda3c01331, overlapping OAuth consent capture; reconcile that prerequisite
next while retaining MCP's stronger live initiating-session check. Full-flow MCP
activation/ta/continuation still has not been implemented or claimed.

## Resume prerequisite repair: dda3c01331

Imported the consent lane's later fix after it reproduced the transient-answer
message failure. Generic/inline sign-in records a single-use owner-started proof;
its callback need not carry an owner cookie. MCP retains the live initiating
session check at take and deposit, since its verifier is server-held. Missing
cookie, different session, logout and replay tests remain in place. No test was
weakened: the MCP ASGI helper explicitly sends its required real session cookie,
while the upstream generic helper now deliberately omits callback cookies.

Validation after reconciliation: Linux oracle 278 passed, zero skips across
MCP/generic OAuth, consent, inline/onboarding model connect, hosted-model app,
real first-run browser, sign-in source and inline approvals. The preceding
47-file Linux run passed 1299 with one platform-specific skip; it predates this
last prerequisite repair. Ruff, plugin build/import, 608-file mirror parity,
strict OpenSpec and whitespace checks pass. Task 1.2 is checked for the complete
OAuth machinery; request-shape hookup and full-flow proof stay in task 1.6.

## Final resume verification

Pushed verified slices 8b6f2bf6ea (sheet/consent prerequisites), 9ab9c52ccb
(MCP PKCE/session binding), and 12f1ec901a (latest consent repair). Merged the
advancing isolation foundation through c7a43a85af as cb6aad0141, without rewriting
history. Post-merge Linux oracle: 223 passed, one skip, explicitly the Windows
junction test test_in_platform_agent_systems.py:205. A skip is not counted as a
pass; the affected Linux broker/isolation tests passed. Command included all four
MCP suites plus broker injected authority, read-only liveness, usage source,
outbound ledger, cloud automation/control, and in-platform agent systems.

Final Ruff, 609-file mirror parity and whitespace checks pass. Plugin import probe
passed before the final foundation merge; the merge's generated mirrors match.
Hygiene against c7a43a85af: 75 added / 0 removed / 0 tampering. Claude's resumed
review verdict remains ADAPT, findings corrected as recorded above.

Current remaining work: 1.1 rollback/cleanup acceptance; 1.3 production transport
wiring, MCP cancellation and durable reconciliation; 1.6 request-shape discovery,
coordinator activation, ta catalog/dispatch, annotation policy, original-turn
continuation, and the full pasted-link integration test. 1.4 remains deferred;
1.5 and 1.7-1.10 remain open. The separate local OAuth sign-in/custody and broker
initialize/list/call tests are NOT a full pasted-link/app acceptance test.
No deployment, deployed-SHA assertion, real-user MCP proof or capability sync.

## Resume slice: cancellation and reconciliation (2026-10-05)

The async broker client now has a production constructor requiring the admitted
running broker, and a status query for durable operation reconciliation. Remote
MCP cancellation closes the broker stream and attempts notifications/cancelled
with the original request ID. No call is replayed. Status remains conservative:
a completed HTTP transport does not prove an external effect's semantic result.
Windows protocol: 22 passed. Linux protocol/real broker/server/upstream: 69 passed,
zero skips. Ruff passed. Task 1.3 stays open for end-to-end production wiring.


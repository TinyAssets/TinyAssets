# Legacy connection identity repair

September 15, 2026 UTC. Follow-up within the existing approved-download delivery;
owner Codex, branch `codex/legacy-connection-policy`. No live data inspected or
permissions changed. App-owned projects #3840/#3842 remain outside this lane.

## Evidence and proposed outcome

The rendered app reply September14 17:11 PDT reports that its log-download access
extension could not create a request. Its #3842 body gives the exact refusal:
"Could not capture this connection's redirect approval policy."
On the unchanged runtime, `python -m pytest -q tests/test_http_redirect_approval.py
-k legacy_connection_can_request --tb=short` reproduces precisely that refusal
for both exact and full HTTP connections whose stored incarnation is empty:
2 failed, 34 deselected, 1.03s, Windows. This proves a matching defect, not the
contents of the user's live connection database.

Expanded regression run: `python -m pytest -q tests/test_http_redirect_approval.py
tests/test_outbound_http_connection.py --tb=short --show-capture=no` produced
5 failed / 64 passed in4.31s before runtime edits. Existing authority cases pass;
new failures also cover the pre-column migration, repeated/concurrent openings
and rollback-created blank identity. A per-row identity/no-normal-path-UPDATE
case was added afterward and still awaits the implementation run.

ConnectionLedger initialization adds the incarnation column with default empty
text but does not initialize existing records. New create_connection calls use
uuid4; older records (and inserts from rollback writers omitting the column)
remain empty. Redirect request capture correctly rejects an empty incarnation.
Relaxing this check would discard the replacement-connection approval fence.

## Shape

Complete the existing ConnectionLedger initialization migration: assign a fresh
opaque 128-bit hex incarnation to each row whose incarnation is exactly empty.
Keep the existing column and representation; no API or tool shape change.
Use SQLite per-row `lower(hex(randomblob(16)))`, with `WHERE incarnation = ''`
as the write-time guard, after the existing ALTER loop. There is no transaction
covering the preceding schema script/DDL: keep the EXISTS check outside an explicit
transaction and let the conditional UPDATE start its normal deferred transaction,
committed by the existing connection context manager. Do not add BEGIN IMMEDIATE.
The initial EXISTS check avoids acquiring a write lock for the ordinary already-initialized
case. The guarded update handles concurrent initialization without replacing a
winner's token. Re-run the narrow repair on open so rollback-created blank rows
are adopted too; do not repeatedly rotate initialized connections.

Only incarnation changes. Owner, connection id, provider, credential reference,
mode, endpoint/scopes JSON, revocation and grants must remain byte/value identical.
No authority is granted and no pending request is answered. New request capture
then sees the persisted nonempty token in its existing single-row snapshot.
Old empty/missing-snapshot redirect requests still refuse; no grandfathered consent.
Deletion/recreation gets a fresh token and stale approvals remain invalid, even
when policy and ids otherwise match. Do not substitute a deterministic token
derived from a connection id or silently normalize other malformed values.
Database errors remain errors; do not silently pretend repair succeeded.

## Acceptance and rollout

Before runtime edit: Claude Fable5.1 shape/basic-safety review of this proposal.
Tests: real request/owner-answer exact/full legacy cases; pre-column database;
stable repeated/reopened/concurrent initialization; distinct row tokens; untouched
policy/grants/revocation; blank insert after first upgrade; stale pre-replacement
and missing-snapshot approval still refused. Existing redirect/full-channel tests
remain the executable authority contract. Focused Windows and existing Linux CI,
plugin mirror/import, exact-head independent review, then normal draft PR gates.
No transport, sandbox, filesystem helper or workspace runtime change intended.

Rollback to the prior runtime leaves the populated field intact; no down-migration
or data deletion needed. Old writers that create blank rows will need the repair
again on re-upgrade. Existing redirect opt-in is not created/erased by this patch.
After protected deployed-SHA and public-canary verification, send the ordinary
checklist retest, then let the app attempt its own blocked work and request any
actual new authority from its owner. No maintainer click-through or direct log
retrieval substitutes for rendered agent success.

## Shape disposition

Claude Fable review84530 completed330s, VERDICT APPROVE with three precisions:
use the conditional deferred UPDATE rather than an unconditional explicit write
transaction; place it after all ALTERs; regenerate the plugin mirror. All accepted.
Durable review: docs/reviews/2026-09-15-legacy-incarnation-shape-fable.md.
The reviewer independently reproduced both exact failures. Deferred observations:
an opener unable to write a legacy DB must fail loudly; old snapshot-less full-mode
asks retain their pre-existing weaker comparison (not redirect consent). No claim
that this patch closes the latter; do not broaden the repair into unrelated policy.

## Implementation verification — September15 UTC, Windows

Runtime adds only the ten-line empty-only initialization after the ALTER loop.
No approval/transport code changed. The two legacy request tests now pass.

- `python -m pytest -q tests/test_http_redirect_approval.py tests/test_outbound_http_connection.py tests/test_full_channel_access.py tests/test_pending_requests.py tests/test_http_connection_provisioning.py tests/test_http_redirect_policy.py --tb=short --show-capture=no`:249passed17.15s, zero skips.
- `python -m pytest -q tests/test_http_redirect_chain.py tests/test_http_redirect_composition.py tests/test_http_redirect_transport.py tests/test_workspace_authority.py --tb=short --show-capture=no`:140passed49.15s, zero skips,8existing deprecation warnings. Real spawned broker/code composition remains exercised.
- After adding explicit readonly-database loud-failure coverage, the two directly changed test files reran:71passed4.92s, zero skips.
- Focused Ruff, strict OpenSpec validation and diff checks pass. Plugin rebuilt441files, import probe passes. Canonical and delta spec scenario synchronized.

The HTTP effector constructs the parent ledger in `_read_connection_context`
and `_open_connection_proxy` before the trusted child creates its own ledger in
`_build_credential_broker_dispatch`; no new readonly-child bypass is introduced.
A deliberately readonly blank DB explicitly raises rather than silently losing
the fence. This is local/code evidence, not a live migration observation.
Actual Linux CI, exact-head runtime review, deployment and rendered acceptance
are pending. Existing full-channel snapshot-less legacy comparison stays unchanged.

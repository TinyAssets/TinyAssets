# Founder command-center reports, 2026-10-04

Branch: `fix/app-ui-run-and-code-checks`, PR #4442. Review fixes and push only;
deployment and real-user verification are still pending.

Review-repair validation: related source-guard, bid, sandbox, branch-runner
(affected heavy file), approval-description, path-I/O, storage-accounting,
storage-registry and jail-disk tests: **388 passed / 7 platform skips on Windows;
395 passed / no skips in the Linux oracle** (Python 3.11.16, bwrap 0.12.0,
uid 1001). After the final module-registry alias/wildcard cases, the source-guard
file passed **66 tests on each platform**, no skips. All pytest temp roots were
outside the repository. Ruff passed for changed canonical/mirror Python and
tests; the mirror build/import probe and whole-tree parity check passed. Every
main-branch test name in changed test files was retained. No full suite or
additional agents were used.

## Failed run reads

`app_ui.js` treated a snapshot's execution `error` as a read refusal. The bridge
now checks the returned run identity; server scope/visibility checks remain in
`api/runs.py`. The owner door already preserves error-as-data.

Red: `test_failed_run_details_and_partial_output_remain_readable` failed with
"that run is not one of yours" before the fix. Green: the bridge/live-state
tests (11 tests), scoped listing (2), and real owner-door tests (21, including
failed status/error/partial output and denial to another account). Mirror rebuilt.

Pending: production deployment and real-user verification remain outside this
push-only request. Delete this finding once those are proved.

## Source guards

`node_sandbox.NodeSandbox.validate_source` and the bid producer/executor scanned
raw source for `open(` and other patterns; the compiler's narrower list had the
same prose false positives. Shared AST inspection examines calls and module/name
references, preserving each list. Comments, prose literals and docstrings pass;
direct calls (including spaced, parenthesized, attribute and f-string-expression
calls) and references to forbidden callables are blocked. The universe path-I/O
test was already AST-based and needed no change.

PR #4442 cross-family review found that ignoring literals let string-running
APIs and reflective namespace lookups through. The repair refuses the constructs
themselves: string-running modules (including aliased imports), namespace and
attribute reflection, dunder references, module registries and wildcard imports.
The refusal explains that dynamic execution/reflection is unsupported; inspecting
literal arguments alone cannot prove dynamically assembled code safe. The OS jail
remains the authority boundary. `test_source_guard_syntax.py` has a negative for
each of the five reported bypasses, plus alias, nonliteral argument, reflection
and module-registry variants; prose remains positive at all four callers.
Null bytes return syntax diagnostics/reason codes at all four callers, including
the Python 3.11 `ast.parse` ValueError path.

Red: `test_source_guard_syntax.py` had 8 failures before the fix (3 prose
rejections, 5 whitespace/parenthesized-call bypasses). Green: all 113 tests in
that file, `test_node_bid.py`, `test_describe_branch_approval.py`, and
`test_universe_path_io_guard.py` passed on Windows and the Linux oracle
(Python 3.11.16, bwrap 0.12.0, uid 1001; no skips).

The subsequent runtime-boundary regression failed on both an isolated `open(`
string and comment before the sandbox change. It now uses the same AST helper
with its original, wider forbidden list. The affected heavy file is
`test_branch_runner.py`; `test_node_sandbox.py` exercises actual execution and
the Linux jail. Windows skips for those six jail cases are not Linux proof.
Final runtime checks: 179 passes on Linux, no skips (syntax regressions,
sandbox, branch runner, path-I/O guard); 169 passes / 6 POSIX skips on Windows
for the first three files.

## Storage accounting

The initial repair exempted five consent filenames inside a universe. PR #4442
review correctly identified that names do not prove platform ownership: a jail
can grow those files across runs without reaching the owner's quota. All five
exemptions are removed, including `.effector_consents.db.premigration`, because
the migration backup also remains writable inside the universe. A genuine legacy
backup is therefore charged conservatively. No migration or deletion is performed.
The authoritative database in `.universe-sidecars` remains outside the account's
measured universe, as proved by the retained
`test_platform_consent_artifacts_do_not_exhaust_the_owners_pool`.

`test_consent_lookalike_growth_remains_charged_between_runs` covers every name at
the root and nested (10 cases): three admitted appends remain charged after
remeasurement and the fourth reservation is refused. Jail growth accounting is
unchanged.

Accounting findings:

- The pool belongs to the owner, across every `universe_owner` binding. Defaults
  are 2 GiB free / 20 GiB paid, with deployment env overrides. Volume pressure
  uses a separate 1 GiB free-space floor and inode floor: 24 GiB free says nothing
  about the account's available quota.
- Files, permanent workspaces, agent activities and owner-attributed shared
  rows/blobs count: run records/events/receipts, checkpoints, uploads, branches,
  project memory, UI assets/library, daemon memory, commons pages, automations,
  packages. These can live outside the command center's folder. Other owners'
  files/rows are excluded by scope/owner predicates.
- Root `.universe-sidecars`, consent DBs and platform logs/cache/runtime are not
  charged. Inside a universe, `.runtime` and `.workspace-staging` are excluded;
  ordinary cache/log files elsewhere remain part of that universe's files.
  Other in-folder metadata remains counted according to `UNIVERSE_ENTRIES`.
- In-flight reservations count in addition to measured bytes. Each jail can
  reserve up to 1 GiB, leaves 16 MiB headroom, renews every 120 seconds, and
  releases on settle. A successful measurement reaps reservations older than
  600 seconds and committed rows covered by its start sequence. Refusal rescans
  dirty/missing or >60-second-old stores. Existing tests cover release, expiry,
  renewal, delete/retry, scan races and owner separation. No evidence of a
  blanket never-release bug was found.

Production was not inspected; the founder's particular refusal remains
undiagnosed. Inspect the effective account type/env quota,
all owner bindings, and the following ledger rows using a read-only connection
to `<data>/.storage_accounting.db` (bind the actual owner ID; do not delete rows):

```sql
SELECT scope_id, store, state, SUM(bytes), MIN(created_at), MAX(created_at)
FROM pending WHERE account_id = ? GROUP BY scope_id, store, state;
SELECT scope_id, store, bytes, measured_at, dirty, start_seq
FROM measurements ORDER BY bytes DESC;
```

Filter measurements to that account and its owned universe IDs. Compare the
largest stores with `storage_accounting.usage(base, owner)`'s measured/reserved/
committed components and the refusal's requested bytes. Inspect the exact
consent artifact sizes under each owned folder; compare reservations with live
processes/renewal times and measurement failures in service logs. A deleted
115 MiB file does not cancel a live jail's reservation or remove retained
checkpoints, outputs and uploads elsewhere. Do not remove reservations by hand.

## Unconfirmed send

`onboarding/app.html:9519` restores the browser's durable in-flight record.
Without a consumer request key, it only recognizes the latest matching founder
message in the fetched history, or a matching active turn; otherwise line 9647
emits the reported notice. A live transport failure already attached the
read-only saved-conversation check (`:4929`); the reload path omitted it. The fix
attaches that same check at `:9651`, including foreground/online refresh, and says
the reply did not arrive *here* and the request may already have acted.

Red: `test_restored_unconfirmed_send_can_observe_a_server_accepted_reply` failed
because the restored notice had no check. Green: 68 targeted recovery,
not-delivered and conversation-admission tests; all 16 Chromium recovery cases,
including the added restored-send/resume scenario. The recovered reply is
visible, the local request remains, and no new converse is sent.
Additional app checks: 81 addressed-agent/account-transition/message-expansion/
working-indicator cases passed. Onboarding and owner-read cases had 156 passes
and two old exact-button-list assertions; both existing tests were retained,
updated for the added read-only button, and passed on rerun (alongside the
served-error case, whose button list stays unchanged).

The reported timestamp alone cannot prove a server drop. A native `converse`
can be accepted and perform work, then lose its response to a disconnected SSE
stream, watchdog or deploy/edge failure (`app.html:1625`). Unlike the consumer
workflow path (`app.html:3927`, `storage/conversation_run_admissions.py:29`), it
has no durable per-send request key. `universe_server.py:3263` persists the native
founder/reply pair only after completion; exceptions record a failure at `:3234`.
A process death before either write can leave no terminal conversation row.
No automatic replay or text-based attribution was added: it could duplicate
effects or associate an older identical message with this send.

To distinguish the actual October 4 sends, inspect the owner's exact home and
agent session in `.conversation_memory.db`, `conversation_turns` (session_id,
turn_no, speaker, content, ts, execution_json, failure_json) around **10:19 UTC**
and **10:42 UTC**, then through the **17:04 UTC** observation. Compare the saved
browser in-flight record's timestamp, scope, agent and consumerRequest. If it
has a request key, inspect the owner/session-matching admission in `.runs.db`
(`conversation_run_admissions`: run_id, created_at, updated_at, projection_state,
terminal_json) and the corresponding run. For native sends, correlate `/mcp`
access/edge request IDs, SSE/5xx diagnostics, provider completion and service
restart logs in the same windows. Native history can establish what was saved,
but cannot prove a particular identical send's receipt without that correlation.

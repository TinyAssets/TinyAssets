# Founder command-center reports, 2026-10-04

Branch: `fix/app-ui-run-and-code-checks`. Push only, no PR or deployment requested.

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
same prose false positives. Shared AST
inspection now examines calls and module/name references, preserving each list.
Comments, literals and docstrings pass; real calls (including spaced,
parenthesized, attribute and f-string-expression calls) remain blocked. The
universe path-I/O test was already AST-based and needed no change.

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

Reproduced a real overcharge in `_universe_files`: 10 KiB of user files plus
500 KiB of platform consent files produced a 510 KiB charge and refused a
20 KiB write against a 100 KiB test quota. The consent migration keeps
`.effector_consents.db.premigration` inside the universe; legacy consent DB,
WAL, SHM and rollback journal names were charged there too. Exclude those exact
platform artifact names from account measurement. Jail growth measurement is
unchanged, so this does not exempt those paths from a running jail's disk bound.

`test_platform_consent_artifacts_do_not_exhaust_the_owners_pool` failed with
`StorageRefused` before the change, then passed. All 74 storage-accounting,
registry and jail-disk tests pass in the Linux oracle (no skips); Windows has
73 passes and one POSIX-only skip.

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

Production was not inspected; the consent overcharge is proven but not tied to
the founder's particular refusal. Inspect the effective account type/env quota,
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

# Founder command-center reports, 2026-10-04

Branch: `fix/app-ui-run-and-code-checks`, PR #4442. Review fixes and push only;
deployment and real-user verification are still pending.

Final revision validation: **580 passed / 6 POSIX skips on Windows** (Python
3.14.3), including all 16 browser recovery cases; **570 passed / no skips in the
Linux oracle** (Python 3.11.16, bwrap 0.12.0, uid 1001). Both runs cover source
guards, bids, sandbox, the affected heavy branch-runner file, approval receipts,
path-I/O, storage accounting, failed-run reads, onboarding and owner-door reads.
All pytest base temp directories were outside the repository. The 82 source-scan
regressions pass on both interpreters. Ruff, mirror regeneration/import probe,
whole-tree mirror parity and diff whitespace checks pass. Pattern-list ASTs were
compared against `origin/main` and are identical. The other three fixes are
unchanged; no full suite or sub-agents were used. Deployment and real-user
verification remain pending under this push-only request.

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

Final revision replaces the PR's AST denylist with the original `origin/main`
substring scan at the compiler, sandbox and both bid call sites. The pattern
lists, matching order and per-caller policies are unchanged. Before matching,
Python `tokenize` STRING, COMMENT and f-string literal-part tokens are replaced
with equal-length whitespace, preserving line endings and all code offsets.
Tokenization failures (including ERRORTOKEN) scan the unmodified source instead;
null bytes still return syntax diagnostics/reason codes at all four callers.
This is a pre-check, not a Python security analysis. The OS jail is the boundary.

All nine round-2 ordinary-code examples pass: `is_open = True`, `retrieval = []`,
`super().__init__()`, `x.__class__`, `code = s['code']; code.strip()`,
`profile.get('name')`, `trace.append(1)`, `from types import SimpleNamespace`,
and `self.modules`. None matched the original scan. Original substring quirks
are retained: `is_open()` and `retrieval()` match the relevant call patterns,
while `open (...)`, `(open)(...)`, `os . system(...)` and callable aliases do not
acquire new AST-based refusals. Strings holding API names are exempt regardless
of how a caller might later use them; the scan makes no reflection-safety claim.

Python 3.11 tokenizes a whole f-string as STRING, so the whole token is blanked.
Python 3.12+ exposes FSTRING literal parts separately and leaves expression code
available to the original scan. Tests record that interpreter-defined behavior,
along with nested f-strings, format text, multiline/Unicode/CRLF offsets, every
original forbidden pattern, raw fallback and legacy compile ValueError handling.
The AST-only tests removed in this revision were all introduced by this PR;
no tests from `origin/main` were changed or removed in this revision.

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

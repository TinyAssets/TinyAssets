# Proposed app byte intake into existing custody

Status: shape accepted with Fable 21633 ADAPT and root disposition, 2026-09-20.
Implementation is authorized; no production changes or live acceptance claimed.
This closes the missing HUMAN app upload step of `bind-immutable-run-files`.
It does not finish tool-only binary producers, workspace/cross-owner intake,
materialization, durable chat-only attachment retention or the umbrella change.

## Verified primitive inventory and gap

Source citations refer to the isolated file candidate; symbols are stable lookup
keys, line numbers are inspected on September 20, not a deployment assertion.

| Existing primitive | Reuse / observed gap |
| --- | --- |
| `onboarding/app.html:3729` paperclip, `handleFiles:3751`, `buildTurn:3762` | Text-only 200 KiB per file / 400 KiB total, `File.text()`, binary refusal, exact accepted text embedded in message. No binary upload or custody handles. |
| `authoring/io.py:349,440` `bind_inputs`; `authoring/service.py:549` `run_test`; `api/extensions.py:844` | Only production path to `put_file_handle`; legacy actions are hidden by `universe_server.py:2616,3634`. No ordinary advertised source-creation route. |
| `tests/test_run_file_capture.py:26–42` `intake` | Internally creates session/handles; existing public execution proof begins after missing intake. Not fresh-user acceptance. |
| `onboarding/__init__.py:534,549` `_app_identity_required`, `_read_home`; `storage/current_home.py:38` `check_current_home` | Resolved named bearer identity, existing complete current home without provisioning; transaction-local home/deletion fence. |
| `onboarding/model_preferences.py:30–89` `handle_model_preferences`; `onboarding/__init__.py:98` `_same_origin_json` | Authenticated settings route with origin scheme checks, bounded body, home expectation and explicit identity propagation into worker. JSON helper itself must NOT be reused unchanged for binary body. |
| `run_file_capture.py:56,131` `_authority`, `capture_authoring_files`; `storage/run_files.py:124,300,404` | Existing admin/tombstone checks, operation lock, reservation, inventory, commit, cleanup and exact references. Extract shared ingestion mechanics; retain authoring-source fence only for that source. |
| `execution_authority/blob_stream.py:25,47` `CHUNK_BYTES`, `stage_stream`; `workspace_pool.reserve_transfer_bytes` | Verified bounded stage/publish and byte-only accounting. No whole request buffering, second object store, new job or price. |
| `app.html:2554,2568,2600,2770` recovery and `sendTurn`; `universe_server.py:2371,2506,2539` `converse`; `consumer_runtime.py:184,256` | Existing message string reaches both default agent and selected consumer unchanged, and queue/inflight/history preserve it. No typed attachment field today; use reference-only metadata inside that same message. |
| `storage/run_files.py:25,332`; `api/run_files.py:37,55` | One-hour unbound retention; bound files do not expire. Limits/read/release remain existing handles. Conversation text is not a run binding. |

Inventory before proposal: repository `rg` found no upload/authoring mapping in
canonical/served handles, no upload endpoint in onboarding route list
(`onboarding/__init__.py:1617`), and only the caller chain above for handle writes.
`python scripts/check_primitive_exists.py action upload_run_file` and
`... action app_file_upload` both returned CLEAN. These are collision checks,
NOT new MCP handles or a claim that the HTTP route is already implemented.
OpenSpec resolved this existing change repo-locally and `check-change ...
--provider codex` returned ALLOWED. PLAN minimal primitives, browser-only coverage,
and canonical API principles were rechecked; no PLAN change is required.

## One small new boundary

Propose ONE `POST /mcp/app/files` endpoint on the existing authenticated app.
It accepts one raw `application/octet-stream` body, not multipart, a path, URL,
base64 bytes inside a model turn, or an executable draft. No GET/download URL or
new MCP tool is introduced. The paperclip uses this route; successful references
continue through the existing graph handles for binding, reads and release.

Request metadata is one required `X-TinyAssets-Upload` header containing base64url
UTF-8 JSON with exact keys `{version:1,label,expected_universe_id,filename,
media_type,size_bytes,sha256}`. It contains metadata only, never file bytes or
credentials. Proposal bounds: header at most 8192 ASCII bytes; stable random
label 16–128 characters; exact nonnegative integer size at most existing
`MAX_FILE_BYTES` (8 MiB); lowercase 64-hex SHA-256. No boolean integers, extra
identity/path fields or silent filename/media rewriting. Reject metadata beyond
header/reference bounds rather than truncate it. A zero-byte body is valid.
Each browser request is a single file; existing 32-file intake bound also caps
one pending picker selection. Larger files are visibly unsupported for this MVP.

Authenticate before any body read; require existing complete home and fresh
admin/tombstone checks. Owner/home come only from resolved bearer identity and
server current-home lookup. `expected_universe_id` is an equality precondition,
not a target selector. A stale account/home refuses. Require exact configured
app origin from the existing request-Host OR configured-public-resource set
(exact scheme, authority including port, no path/query/fragment), raw-body
content type and custom header. Reject missing/foreign/null origin and simple
cross-site forms; no permissive CORS. Apply the existing app identity middleware
to this route and prove the full ASGI boundary, not a preseeded context alone.
Responses use existing JSON error conventions and `Cache-Control: no-store`.

Use the SAME custody operation table, allocation, inventory, physical store,
byte-transfer ledger and collector. Namespace upload operation IDs separately
from authoring capture; digest source kind/version plus exact owner/home/metadata.
Resolve committed same-label retries to original references. Changed content or
metadata conflicts. Prior unfinished copy is recovery debt, never implicitly
overwritten or restarted; no resumable-upload protocol or new durable queue.
Failed labels follow existing consumed-label behavior and the UI must say so.

Reserve declared bytes and check verified-filesystem headroom before consuming
the body. Stream from ASGI into the existing verified stage/publish machinery;
do not use `request.body()`, whole-body `_read_bounded_body`, `form()`, a temporary
authoring store or inline base64. Enforce cumulative exact size, SHA-256 and
hard maximum regardless of Content-Length (if present it must match metadata).
Use an explicitly bounded stream bridge to existing workers, no new job pool;
reslice actual ASGI messages into chunks no larger than CHUNK_BYTES before queueing.
At most two 1 MiB chunks are buffered, with no unbounded producer queue. A
four-slot per-process upload semaphore refuses 503 before bytes/worker allocation
when full, preserving threadpool capacity for other app operations. The worker owns
the operation guard for its entire lifetime on one thread; SQL writer locks are
short and never span upload streaming. Actual ASGI chunk/framing behavior must
be measured in the ingress memory test, not assumed from the blob chunk limit.

Retain the existing whole-copy shared maintenance barrier. This MVP bounds but
does not eliminate possible exclusive-maintenance starvation; shortening the
barrier requires separate safety proof. Total upload deadline is 120 seconds
and idle chunk deadline is 10 seconds. Request disconnect, explicit UI
abort, timeout, current-home change or tombstone stops copying and marks exact
inventoried cleanup debt. Cancellation must wake both producer and consumer,
join/unwind the worker, and release the operation guard without an orphan thread.
Recheck current home/admin/tombstone at bounded chunk checkpoints and inside the
final short author-store then runs-store commit fence. The transaction-local
current-home check at this fence is new, not inherited from admin/tombstone checks.
A digest is integrity,
never ownership. Only complete verified bytes become ready. Settlement debits
actual transferred bytes; uncertain disk cleanup retains allocation debt.

Return `{universe_id,files:[<existing versioned public_reference>],
unbound_retention_seconds:3600,unbound_expires_at:<original deadline>}`. The
deadline comes from the existing operation row, not response time; replay does
not extend it. This wrapper metadata is not added to the immutable reference.
On committed replay, check current ready/bindable status: expired unbound custody
refuses even if collection has not deleted the body; already-bound custody may
return `unbound_expires_at:null`. No session ID, client path, storage key, bearer
URL or secret appears. Extend existing file-limits discovery truthfully with
app upload availability and this ceiling; do not advertise installed upload
support before the route and UI are available. No provider call is needed to
upload into an already initialized signed-in home.

## App and agent relay, with explicit limitations

Keep the current accepted text attachment behavior byte-for-byte; do not turn
small text attachments into extracted summaries or normalize their content.
Add a separate binary/as-file upload state to the existing paperclip. Process
files sequentially so the browser hashes one bounded file and streams one request
at a time. Display exact filename/size plus uploading/ready/failed/expired state.
Abort on signout or login epoch change. Never forward a late response into the
new account's composer. All files ready is required before Send; a partial
failure blocks Send until the user explicitly retries or removes failed chips.
Do not silently send a subset, clear the typed draft, or auto-run any workflow.
Successfully uploaded siblings remain independently owned; there is no claim of
all-or-none multi-file upload. Existing run admission atomically binds a requested
bundle. Removal offers release of that newly uploaded file using existing owner
release semantics; active binding refusal is visible, never override authority.

At Send, serialize only ready returned references ONCE in a clearly delimited
JSON attachment-metadata block containing expiry beside the exact files array,
never inside the immutable six-field reference, alongside unchanged message/text
blocks. Preserve exact reference fields/order; JSON-escape filename/media data.
This is ordinary untrusted context, not hidden instructions, permission, an
execution-use grant or a new provider-specific input. Existing `MCP.converse`
forwards this same message to default intelligence or custom consumer intent;
no new conversation schema/attachment registry is needed. The agent selects
declared workflow fields and passes refs to ordinary `run_graph`; admission
revalidates owner, universe, exact metadata and ready state. Platform operators
do not build the user's consuming workflow.

Queue/inflight/history retain that exact composed message and original account/
home scope. In-flight records gain explicit scope using the existing queue rule;
legacy/unscoped/other-home records are preserved but never displayed or offered
to another user. Reconnect does not upload again, rebuild metadata or create a new
consumer request key. The compact displayed bubble shows filenames/status while
the exact reference block remains available to the agent. A restored stale-home
attachment refuses. A passed unbound deadline means availability needs checking,
not proof that a subsequently bound file expired. Exact same-label observation
on an explicit retry returns still-bound custody or an expired-unbound refusal;
only the latter requires explicit reselection. Do not reconstruct
bytes from a model or silently send unavailable refs.

IMPORTANT: one-hour unbound expiry remains unchanged. A sent chat message is NOT
a run binding and does NOT make an upload permanent. The UI states its expiry
and checks it before sending/restoring. Once actually bound to a workflow run,
existing nonexpiry-until-release/erasure applies. Permanent chat-only attachment
retention would be a separate reviewed lifecycle change, not a fabricated run
or authoring draft. This MVP does not claim it.

## Refusal, rollout and rollback

401: no resolved identity. 403: origin/admin violation. 409: home change,
same-label conflict or held recovery. 400: malformed metadata/length/digest.
413: byte/header limit. 503: missing capacity or storage unavailable. Return
stable safe reasons without filenames, raw bodies or DB details in logs. A
network failure means acceptance may be unknown: retry the EXACT label/request
to observe committed custody before creating another copy. Upload retry never
replays a workflow. Explicitly disclose a burned/cleanup label rather than an
automatic replacement request. The user's selected File remains local until
they retry/remove or reload; reload may require reselection, never guessed bytes.

Ship UI, endpoint, metadata relay, limits, auth and tests together after shape
review. Root owns deployment and normal global capacity configuration. Unset
capacity stops NEW byte uploads/capture only; existing references remain usable
for binding/execution/export and bound files nonexpiring. Disabling the UI alone
is not a backend security fence. An existing-file authorization issue requires
a targeted compatible hotfix, never old code or destructive data rollback.

## Acceptance required before calling this usable

1. Red-first full-ASGI fresh identity/home request creates files without calling
   authoring/session/put_file_handle helpers. Origin/header/auth/home failures
   read zero bytes and create no file/allocation/provider effects.
2. Actual app picker selects a non-UTF8 file larger than one chunk, an empty file
   and several files; exact filename/media/size/digest survive. A returned ref
   reaches the default AND selected custom conversation through the real send
   path, with identical retry/queued message and no new consumer intent fields.
3. The ordinary app agent authors, publishes and runs its own consuming workflow;
   selected entry/downstream hashes and bounded export match local selected bytes.
   No internal source fixture, hidden tool or platform-authored workflow substitutes.
4. Preserve existing small-text attachment golden tests; test mixed text/binary,
   uploading/failed chips, partial-success removal, tab reload, signout/account
   switch, delayed response, old-home queue and one-hour expiry explicitly.
5. Enforce bytes/digest/count/metadata limits, absent/lying Content-Length,
   malformed/chunked request, very slow source, disconnect/abort at reserve,
   stage and commit, tombstone/home-change races, ENOSPC and restart cleanup.
   Measure bounded ingress/worker buffering; prove no orphan worker or lock.
6. Same-label committed response loss returns original refs with no copy/charge;
   changed request conflicts; incomplete label is held and not implicitly replayed.
   Run existing erasure/reset/retention, source-authority and scalar regressions
   on native Windows plus Linux oracle, mirrors, relevant lint and full required CI.
7. Independent opposite-family shape then exact-code review, configured global
   rollout, deployed SHA/canonical canary and rendered ordinary-user app proof.
   Ask the app agent for its checklist response; local tests alone do not close it.

Concrete shape-review questions:

- Does the new raw-body origin/metadata/current-home boundary authenticate and
  refuse before bytes without weakening the existing JSON helper or home gates?
- Does shared streaming/reservation/cleanup avoid another custody system and
  bound memory, cancellation and same-label uncertainty without a new queue?
- Does server-authoritative returned reference metadata reach both conversation
  paths with exact text and retry intent preserved, without manufacturing authority?
- Are one-hour unbound expiry, partial success, offline/reload and future
  tool-only binary-producer limitations explicit and acceptable for this MVP?
- Do the predecessor's narrow rollback wording corrections accurately state
  new-intake-only capacity fencing versus a targeted existing-file hotfix?

The last question verifies the documentation disposition of the PREVIOUS code
review; it is not a claim that that reviewer reviewed this new upload design.

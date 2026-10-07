## Context

The September 18 live signup reached OpenRouter's workspace rather than its
pending authorization continuation. A later authorization crossed a daemon
restart and failed; that callback-state repair has a separate owner. A starter
key is an acquisition alternative, not evidence of TinyAssets model authority.
The generic `paste-blob` flow calls `resolveConnection` and `connectHTTP` only;
it neither registers compute nor asks for model-access approval. It is not an
unpowered-safe recovery path.

The existing `model_bootstrap.complete_bootstrap(key=...)` already deposits an
owner-scoped credential using the installed preset's exact endpoint policy,
discovers eligible free/tool-capable models, prepares the bootstrap binding,
and creates the ordinary unanswered `bind_model_access` request. This proposal
adds a key-acquisition caller, not a second bootstrap or approval system.

## Goals / Non-Goals

Goals: a signed-in, unpowered browser-only user can explicitly paste their own
OpenRouter key and reach the existing free-model approval without an LLM or a
working OAuth callback. Clear signup/dashboard/error recovery copy must state
what remains incomplete. Preserve model preferences and every existing grant.

Non-goals: paid fallback, credit purchases, arbitrary providers/endpoints,
automatic approval, credential replacement, account provisioning at OpenRouter,
OAuth storage repair, new MCP handles, new credential storage, or new custody
policy. This uses the existing private universe vault custody mode only.

## Decisions

### 1. Add one operation to the existing authenticated app ingress

Proposed request: `POST /mcp/app/model-connect/deposit_key`, JSON containing
exactly `preset_id` and `key`. This is an additive app API contract requiring
independent shape approval before implementation. Existing operation routing,
app identity, same-origin HTTPS JSON checks, bounded 8192-byte body, no-store
and no-referrer responses remain. No caller-selected owner, universe, URL,
model, grant, price policy, or approval is accepted.

The installed preset must resolve before creating any home or making an
external request. V1's bundled acquisition document explicitly opts only the
existing OpenRouter free-model bootstrap preset into `manual_key_entry: true`.
The generic loader requires that exact boolean from trusted installed data;
absence, false or non-boolean values refuse. Merely installing another preset
does not admit it, and callers cannot supply the opt-in field. The loader also
compiles the matching discovery contract, checks its catalogue/benchmark URLs
and requires owner-filtered bearer discovery before home creation. A caller
cannot weaken the existing bootstrap's free-only policy. Match the provider-key
validation: 1..2048 printable ASCII characters without whitespace/control
characters; do not truncate or silently transform the credential. `preset_id`
retains the existing bounded-string validation. Reject unknown/extra fields,
malformed bodies and invalid keys with stable non-secret error codes.

### 2. Reuse bootstrap, with current owner and lifecycle checks

Resolve the authenticated principal's current home on the server, creating the
ordinary inert home only if the authenticated account remains live. Require
current founder ownership and empty model setup before accepting this new key.
Do not treat unavailable, exhausted, pending or partially configured providers
as empty. Call `complete_bootstrap` with that exact owner/home/preset and key;
its per-universe gesture lock and empty-state recheck remain authoritative.

Before implementation, verify account-deletion fences around home creation and
each credential/binding/request write. A check only at HTTP entry is not enough
if deletion can begin during discovery. If the existing bootstrap/lifecycle
mutation boundary cannot exclude tombstoned owners or serialize deletion, add
the smallest shared guard there after the review explicitly approves that
scope; do not invent a manual-only resurrection path. Two concurrent manual
requests, or manual plus OAuth, must not replace credentials or overwrite a
newly connected setup. A loser must refuse or recover existing state without
redepositing the supplied key.

Original implementation boundary (Claude Fable ADAPT, September 18) required a
literal preset guard. CI exposed that guard as new provider-specific substrate
code; the correction above replaces it with installed-data capability admission,
not a relocated/disguised vendor branch. Fresh independent review must assess
its authority equivalence; the original review is not approval of this change.
The trusted metadata participates in the existing preset digest, so changing it
invalidates outstanding older flows fail-closed. Deploy must not interrupt a
user's in-flight consent ceremony. Deletion takes the
existing exclusive provider admission only while writing the tombstone, then
releases before renaming the home (Windows open-handle semantics). The vault
writer checks that tombstone inside its existing `BEGIN IMMEDIATE` before DML,
while holding admission through owner-row commit and file persistence. The
shared check does not require founder-home scope: legitimate non-home admin
deposits remain supported. Missing legacy tombstone tables are empty; malformed
or unreadable tables fail closed. See `shape-review.md` for the independent
verdict and required T1–T4 proofs.

The gesture lock is process-local and authoritative for the deployed
single-process daemon only; it is not a multi-worker serialization guarantee.
Future multi-worker activation must replace that assumption. Inherited inert
post-discovery orphan rows, empty lock-created ghost directories, first-contact
TOCTOU and SQLite busy-timeout tuning are non-blocking follow-up hardening, not
claims fixed by this patch. The secret-bearing vault resurrection race is fixed
in this lane before manual acquisition is enabled.

No success result implies activation: reuse `confirmation_required` and the
existing request ID/summary; only the existing owner-approved request answer
can bind model access. Reuse installed free-only eligibility and runtime
selection, never the key's broader potential paid capability. No paid model
or paid fallback becomes available through this route.

### 3. One secure input, explicit recovery mode

The prominent hosted-card key affordance reveals/focuses the existing sole
`paste-blob` field, but switches it to an unmistakable OpenRouter free-model
setup mode. Explain that the key stays in the user's private connection and
that model access still needs the next confirmation. A specifically labelled
manual setup button submits directly to this fixed app operation, bypassing
`resolveConnection` and generic credential inference. The generic Add it
action must not accidentally consume this recovery input; switching modes
clears sensitive contents and restores normal generic behavior explicitly.

Capture the key only inside its explicit submit handler; clear the textarea
before the first await. No key enters chat, URL, local/session storage,
analytics, console, exception text, or response JSON. Disable duplicate submit
until settlement. Capture the current login/view generation before async work
and ignore stale results; never replay a secret request after 401, reconnect,
navigation, timeout or login change. A bounded timeout reports an unconfirmed
outcome, not failure or permission to retry.

Reuse the hosted controller's existing confirmation renderer and explicit
answer handler. After uncertain deposit/discovery, a user-directed `resume`
uses the existing private vault/pending request, never a retained browser key.
Definitive validation refusal permits fresh input. An incomplete setup is not
labelled connected. Hosted OAuth begin/exchange paths stay available unchanged.

### 4. Honest signup and error copy

Signup may display a starter key or land on the provider workspace. Instruct
users who landed there to return to TinyAssets and continue the connection;
do not promise provider navigation returns automatically. If TinyAssets shows
an authorization error, say setup is incomplete and do not instruct blind
authorization/key-creation repetition. The manual path is only advertised as
usable once it can reach the ordinary approval, not when it merely stores a
credential. This patch must not claim to repair the independent callback bug.

## Risks / Trade-offs

- A provider key may possess paid access outside TinyAssets. The server's
  installed preset plus existing approval/runtime enforcement, not copy or a
  claimed key limit, is the no-paid boundary. Test mixed paid/free catalogues.
- Discovery may fail after vault deposit. Preserve a resumable private deposit,
  show unconfirmed/incomplete state and never replay the key automatically.
- Deletion/login/home changes during network work can invalidate entry checks.
  Existing lifecycle guards must be evidenced or minimally extended before
  release; this remains an explicit review question, not an assumed guarantee.
- Shared-file overlap: callback owner modifies `hosted_model_auth.py` and its
  tests; this lane owns `model_connect.py`, controller and focused tests. Merge
  latest main and rerun focused combined regressions before exact-head review.

## Migration / Rollback

No schema or data migration. Existing OAuth clients and operations remain.
Release the route and matching UI atomically after independent shape and
exact-head safety review. Rollback removes manual acquisition UI/operation;
existing private deposits, approval requests and bindings remain governed by
the original bootstrap and request-answer flows. Do not delete user data or
revoke existing connections as rollback. Lead owns deployment, protected
canary/deployed-head proof and ordinary live-user acceptance with user consent.

## Open Questions / Build Gates

1. Independent review must identify the authoritative deletion/account-liveness
   guard and decide whether the existing bootstrap writes need its protection.
2. Confirm the installed preset identity and key-length contract in code before
   implementation; no newly inferred provider privilege or contract.
3. Callback persistence is a separate dependency for OAuth recovery. Manual
   acceptance can demonstrate its own path, never stand in for callback proof.

Independent Codex supporting review identified a concrete inherited race:
`shared_self.require_founder_home` checks home/ACL, not account tombstone;
`credential_vault.write_credential_vault` holds provider assignment admission
while committing owner rows and then persisting the file, but
`account_deletion.delete_account` does not take that same admission boundary.
A deletion between owner-row commit and `_persist_credential_vault_file` can
finish before the latter's `universe.mkdir`, recreating a deleted home with
the supplied credential. Verified code locations on this branch:
`credential_vault.py:467`, `:488`, `:634` and
`account_deletion.py:864`, `:867`. A second entry check does not close this
race. Fable must assess coordinating deletion with the existing exclusive
provider admission and rechecking lifecycle inside the write lock. Do not
wrap the whole bootstrap in a nested non-reentrant provider lock. The race
must have a barrier-based regression test and a reviewed shared fix before
this new acquisition path is enabled.

## Verification Plan

Route tests: unauthenticated/wrong origin/non-JSON/oversized/extra fields,
unknown or non-free preset, malformed key, collaborator/wrong home, deleted or
deleting account, nonempty setup, concurrent submissions and OAuth race;
assert no write/upstream call where denied. Assert exact preset endpoints and
secret-free response/logs for upstream exceptions. Valid input must reuse the
real bootstrap pending approval, not call an activation shortcut. Exercise
free-plus-paid catalogue and no-free/tool model failures.

Actual controller/DOM tests: prominent shortcut, one field, focus and explicit
manual mode; clear-before-await, no generic parser, one bounded request,
pending approval render and explicit answer only, duplicate click, timeout,
late success, login/view change, no storage/URL/console credential and no
automatic retry. Phone/desktop isolated Chromium proof, then lead-owned live
owner approval and model-connected readback. Existing onboarding/model/auth
focused regressions, lint, plugin mirror and brand parity remain required.

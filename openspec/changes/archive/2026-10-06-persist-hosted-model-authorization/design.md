## Context

The current `_pending` dictionary uses monotonic deadlines, intentionally bounds
state to1000 total/10 per owner and consumes a valid binding before any exchange.
Those safety properties stay; process lifetime must not be part of authorization.

## Decisions

Use `.hosted-model-auth.db` under canonical `storage.data_dir()`, following root
satellite SQLite and account-deletion discovery. Table hosted_model_flows stores:
SHA256 flow-handle lookup key, owner_user_id, bound_home_id, preset_id,
preset_digest, PKCE challenge, callback_origin, created_at and expires_at UTC epoch.
It stores NO authorization code, browser verifier, API key, bearer token, plaintext
flow handle or callback query. Reconstruct callback URL only after valid take.

The owner_user_id column deliberately participates in existing personal erasure;
bound_home_id is a binding constraint, not an ownership scope that could retain a
former-home pending record. Retain all current home/admin/setup checks in ingress.

Deletion race fence: before creating the satellite database or inserting a row,
hold the canonical author-store BEGIN IMMEDIATE reservation, check the owner's
deletion tombstone and exact founder-home/admin binding, and hold it through the
satellite commit. Lock order is author then satellite, matching existing guarded
management writes. If begin wins, the satellite exists before deletion writes
its tombstone/enumerates stores and gets swept; if deletion wins, a delayed begin
refuses without recreating personal metadata. Apply the same fence to take; no
author lock remains held across provider exchange. Test a barrier after initial
ingress scope followed by completed deletion before durable begin.

SQLite BEGIN IMMEDIATE serializes bounded admission and consume across workers.
Begin prunes expired rows, enforces existing per-owner/global bounds, inserts a
600-second binding, then returns the existing response. Take looks up the hashed
handle and verifies owner, current home, verifier challenge, expiry and preset
digest in the same transaction. Only a valid take atomically deletes the row and
commits before provider exchange; concurrent takes have exactly one winner.
Invalid/foreign callbacks cannot burn another user's flow. No automatic retries.

UTC expiry is required across process restart; reject timestamps outside the
original ten-minute window and tolerate no lifetime extension on take. Sweep on
begin/take keeps active capacity bounded; account deletion clears personal rows.
Do not expand persistence to the OpenAI device flow or other auth mechanisms.

## Migration / Rollback

Additive CREATE TABLE IF NOT EXISTS; no existing secrets migration. An in-memory
flow already lost cannot be recovered or replayed; user may explicitly authorize
again. Rollback fails existing pending callbacks closed and must never redeem
them without checks. Store failure is explicit, never an in-memory fallback.

## Verification

Fresh-process begin/take; cross-process single-use race; current owner/home/preset
and verifier mismatches retain valid owner flow; wall-clock expiry and admission
bounds; account deletion for former-home records; no code/key/verifier/handle in
stored columns; existing HTTP/bootstrap/app/free-only regressions and Linux oracle.

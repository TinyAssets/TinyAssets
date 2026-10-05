## Why

Founder report, 2026-10-04: after leaving and returning to the app, a delivered
and answered message was offered "Send it again" and was sent twice. The
recovery check could not tell whether the message had landed. A first repair
matched it by text and a time window, and two independent reviews showed that
an identical earlier prompt ("yes", "continue") then confirmed a new send that
had been lost, drawing the stale reply as the answer. Recovery needs causal
evidence, not text or timestamps.

## What Changes

- **Public surface (additive):** `converse` and the app turn path accept an
  optional `client_send_id`: ASCII letters, digits, `_` or `-`, 1-128
  characters; empty means legacy. Invalid values are refused with
  `invalid_client_send_id` before any work starts.
- **Storage (additive):** the saved founder turn and the steering/active-turn
  record each gain a `client_send_id TEXT NOT NULL DEFAULT ''` column, through a
  race-tolerant additive migration. Reads select `''` where the column is
  missing, and a store that cannot migrate saves the turn without the id.
- **Readers:** the active turn (`/app/turn/pending`) and the recent conversation
  rows echo the id, but only within the caller's own owner/home/agent thread.
- **App:** each send mints an id, keeps it in the inflight record and the
  uncertainty notice, and confirms delivery only on an exact id match, checking
  the running turn first. A missing id keeps the cautious resend offer. Turns
  without an id (sent by a connector, or running across a deploy) get their
  reply drawn for display only, and nothing is confirmed.

The id grants no authority. It is never a lookup key, a Stop or replay handle,
or an idempotency key.

## Capabilities

### Modified Capabilities
- `onboarding-web-app`: causal return-to-app confirmation.

## Impact

tinyassets/universe_server.py (converse boundary validation, persistence),
tinyassets/api/status.py (row echo), the conversation and steering stores,
tinyassets/onboarding/app.html (and its plugin mirror), and the app, steering
and server tests.

Delivered by PR #4458: Codex authored it; an independent Claude cross-family
review ran three rounds, ending APPROVE at db2e5181b2; it merged with main at
ce20cd3b56. This change directory reconciles the public-API and storage
artifact after the fact (founder direction: build, prove, then spec what
shipped), and is archived on landing.

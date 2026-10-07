# Design: requests with items, delivered to the owner's devices

Grounded on the code as of `161187aa`: `tinyassets/storage/pending_requests.py`,
`tinyassets/api/pending_requests.py`, `tinyassets/automation_events.py`,
`tinyassets/api/permissions.py::owner_run_identity`,
`tinyassets/onboarding/__init__.py` (the `/mcp/app` route table #4112 moves to
`/app`), `mobile/` (Capacitor 8 shell).

## Why this extends requests instead of adding a primitive

ADR-013 says a new top-level primitive ships only on an
irreducibility finding, and that a behaviour with many plausible shapes belongs
to the commons. "The universe tells its person something and they answer" has
exactly one shape already in the repo, and it is the request: a durable,
cross-surface, owner-gated ask with Accept / Deny / Clear / Reply. A second
"owner message" concept would be two definitions of one fact — the failure
class behind seven straight review rounds elsewhere in this repo. So there is
no new MCP handle, no new target, and no new answer path. Requests grow two
optional things: **items** and **delivery**.

The morning note follows for free and is not built here: an automation, a run
that raises one request whose items are today's tasks, and the owner answering
them from their phone.

## 1. Items

### Shape

An `items` entry on the ask:

```json
{"item_id": "standup-notes", "title": "Send standup notes",
 "body": "Yesterday's thread had two open questions.",
 "fields": [{"name": "note", "type": "text", "label": "Reply"}]}
```

- `item_id` is **agent-chosen and stable**, matching
  `[a-z0-9][a-z0-9_.-]{0,63}`, unique within the request. Agent-chosen because
  the universe needs to correlate an answer back to the thing it planned — a
  server-minted id would force it to re-read the request to learn what it just
  asked. It is a handle, never authority: every read and write re-derives the
  principal, exactly as the existing `request_id` does.
- `fields` reuses `_validated_fields` with **`type: "secret"` refused
  unconditionally**. A request carrying items is required to have
  `action.type == "answer"`, so the credential-deposit paths
  (`connect_http` / `connect` / `rotate_http` / `remove_http` / `extend_http`)
  can never be reached with items attached. That is the same boundary that made
  "construct them however you like" safe, applied one level down, and it is
  what stops an item from becoming a harvesting primitive.
- **Items satisfy the "at least one field" rule.** Verified against the real
  handler (`tests/test_request_items_and_delivery.py`): an ask with no
  top-level `fields` is refused today with
  `{"error": "request_invalid", "detail": "a request needs at least one field"}`.
  A note whose answerable parts are all items has nothing at the top level, so
  `_validated_fields` must accept an empty top-level list when at least one
  item carries a field. Without that, every multi-item request is refused.
- Items are bounded at **50 per request**. This is payload validation of one
  ask, the same class as the existing `_MAX_FIELDS = 16` and
  `_MAX_BODY_CHARS = 600`, and for the same reason `MAX_PENDING` exists: a note
  of 200 tasks is not a note, and a notification payload has to fit. It is not
  an account limit — a universe may raise another request.

### Storage

`items_json` on `pending_requests` (registered in `_ADDED_COLUMNS`, because
`CREATE TABLE IF NOT EXISTS` silently skips an existing table and every live
universe has one — the 2026-08-28 rail outage was exactly that), plus:

```sql
CREATE TABLE IF NOT EXISTS request_item_answers (
    request_id  TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    status      TEXT NOT NULL,          -- answered | dismissed
    answer_json TEXT,
    feedback    TEXT,
    resolved_at REAL NOT NULL,
    PRIMARY KEY (request_id, item_id)
);
```

A separate table rather than mutating `items_json` so that an item answer is an
append under a primary key — one answer counts once, the same guarantee
`resolve_request`'s `status = 'pending'` guard gives the whole request.

### Answering

`answer_request` gains an optional `item_id`:

- `{"request_id", "item_id", "values"}` — answers that item. Insert into
  `request_item_answers` **only if absent** (`INSERT ... ON CONFLICT DO
  NOTHING`, then report whether it moved). The request stays `pending` unless
  this was the last unresolved item.
- `{"request_id", "item_id", "dismiss": true}` — that item is cleared.
- When every item is resolved, the request itself resolves as `answered` in the
  same transaction, with `answer_json = {"items": {<item_id>: {...}}}`, so the
  agent reads one answer object whichever way the owner worked through it.
- A whole-request answer or dismiss closes the request as it does today, and
  the remaining items are reported as `unanswered` rather than being invented.
- `dont_ask_again` stays a whole-request concept: the dedupe key is a hash of
  the request tuple, so a standing decision about one item has no key to hang
  on. An item-level `dont_ask_again` is refused with that reason.

`get_request` / `list_pending` / `list_resolved` project `items` with each
item's current `status` / `answer` / `feedback` joined in.

### Event

`resolve_request` already emits `pending_request_answered` for every surface.
An item answer emits the same event with `item_id` in the payload, and
`item_id` joins `EVENT_FILTER_KEYS[pending_request_answered]` so an automation
can subscribe to one item. Required filter keys are unchanged (still empty), so
existing subscriptions keep waking. This reuses the event path #4107 documents
rather than adding a second one; the two changes touch the same two dict
literals and the merge is mechanical.

## 2. Delivery

### The routing key is the owner, derived server-side

```sql
CREATE TABLE IF NOT EXISTS owner_devices (
    device_id     TEXT PRIMARY KEY,      -- server-minted
    owner_sub     TEXT NOT NULL,         -- the authenticated principal
    platform      TEXT NOT NULL,         -- android | web
    token_sha256  TEXT NOT NULL,
    token_json    TEXT NOT NULL,         -- FCM registration token / web-push subscription
    label         TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL,
    last_seen_at  REAL NOT NULL,
    retired_at    REAL,
    retired_reason TEXT NOT NULL DEFAULT '',
    UNIQUE(owner_sub, token_sha256)
);
CREATE TABLE IF NOT EXISTS owner_notify_settings (
    owner_sub  TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS request_notifications (
    request_id TEXT NOT NULL,
    item_id    TEXT NOT NULL DEFAULT '',
    device_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,            -- raised | clear
    sent_at    REAL NOT NULL,
    receipt    TEXT NOT NULL DEFAULT '', -- a class, never a body or a token
    PRIMARY KEY (request_id, item_id, device_id, kind)
);
```

Three rules, each with a failure this repo has already had:

- **`owner_sub` comes from `permissions.current_actor_id()`** at registration,
  never from the request body. A payload-supplied subject is the identity leak
  shape (`never-infer-identity-from-adjacent-tables`).
- **A token re-registered under a different `owner_sub` moves.** Registration
  deletes every row with that `token_sha256` *regardless of owner* before
  inserting. A phone that signs out of account A and into account B must stop
  receiving A's notifications; leaving the old row is a cross-user delivery,
  which is the one platform floor (`the-floor-is-cross-user-only`).
- **Lookup is by `owner_sub` only**, and the only owner a dispatch may name is
  the one resolved from the universe's `admin` ACL row. No parameter, payload
  field or request content selects a destination — the `app-reply-authority`
  rule ("message text cannot select destination"), applied here.

`request_notifications` is content-free: a request id, a device id, a class. It
holds no body and no token, so the delivery ledger is not a second copy of the
owner's content.

### Who the owner is when a background run raises the request

`request_from_user`'s `_owner_gate` already resolves the actor and requires an
explicit `admin` ACL row for it. A background run binds the owner through
`owner_run_identity`, so inside a run that actor *is* the owner. Dispatch takes
the owner explicitly from the gate's verdict rather than re-reading ambient
identity later, because dispatch may run after the request context is gone. If
the gate's actor and the universe's admin owner ever disagree, dispatch sends
nothing and logs it — fail closed, never "send to whoever looks closest".

### Transport is server-owned and pluggable

```python
def dispatch(devices, payload, *, transport) -> list[Receipt]
```

`transport` is resolved by the server from configuration
(`tinyassets/notify/fcm.py`, `tinyassets/notify/webpush.py`), takes a
`(device, payload)` pair, and returns a redacted receipt. Modelled directly on
`AppOutboundAdapter`: no caller-supplied credential, URL, token or provider
override; a transport exception becomes a bounded failure class, never
exception text (`exceptions-carry-more-than-their-message`).

**No transport configured** → dispatch records `no_transport` at WARNING and
the request still lands. This is a degrade, not a mock: nothing fake is
returned, the caller is told, and the request is readable in the rail as it is
today. Push is additive to a durable request; it must never be able to fail the
ask (Hard Rule 8 cuts the other way here — the loud failure is the log line and
the reported receipt class, not a dropped request).

**Retirement.** FCM `UNREGISTERED` / `NOT_FOUND` and web-push `404`/`410` retire
the row (`retired_at`, `retired_reason`). A device that is gone must stop being
retried forever.

### What the notification says, and what it cannot say

| Part | Composed by |
|---|---|
| Title | **The platform**: the universe's own display name, from the universe record |
| Body | The agent: `kind` + `title`, control characters stripped, bounded |
| Data | The platform: `request_id`, `universe_id`, `item_ids`, `kind` |

The agent never composes the identity line, and no field lets any text claim to
come from the platform or from another user. The channel name, small icon and
app identity are native resources. A request's `fields` — including anything
typed into them — never enter a payload, so nothing the owner pasted can land
on a lock screen.

### Cost

**No usage meter and no rate limit here** (founder, 2026-09-30: account limits
are only cloud storage GiB and concurrent agent-run seats; every rate meter is
being deleted by the limits-two-dims lane). Notification volume is already
bounded by three things that exist for their own reasons, and stating them is
the cost story:

1. **A notification is sent once.** The idempotency key is
   `(request_id, item_id, device_id, kind)`, so a retry after a lost response
   does not push twice, and a re-raise that `create_request` dedupes on
   `dedupe_key` returns the existing row and dispatches **nothing**. Only a
   genuinely new pending row notifies.
2. **`MAX_PENDING = 50`.** A universe cannot have more than fifty unanswered
   requests, so the pile a loop could build is bounded before delivery is
   involved at all.
3. **Seats.** The runs that raise requests hold concurrent agent-run seats,
   which is the account limit. A runaway automation is bounded where every
   other runaway is bounded, not by a second mechanism here.

Adding a notification meter would be a fourth bound on a path that already has
three, and a rate limit the founder is in the middle of deleting.

### Answering on one device clears the others

Resolution dispatches a data-only `clear` to the owner's devices other than the
one that answered, carrying `{request_id, item_id?}`. The app cancels the
matching local notification by tag. Best-effort and after the fact, like the
event emit it sits beside: it never fails the answer.

## 3. Surfaces

### Browser slice as implemented (2026-09-30)

The founder split native Android into a later PR. This slice implements the
browser surface only; Electron push support is not claimed. Account has the
owner-wide on/off control, a separate explicit browser-registration action,
and the owner's token-free device list. Turning on asks permission, registers
`/app/sw.js` with `/app` scope, reads the authenticated VAPID public key, then
registers the subscription before enabling delivery. The page CSP allows
same-origin workers while keeping page scripts nonce-only. An existing
subscription is rebound on verified login; sign-out unsubscribes it.

Items use the existing answer API: Accept supplies item values, Deny dismisses
that item, and Send reply submits its values and feedback as an item answer.
Whole-request Send reply retains its existing conversational behavior. Polls
update item completion in place without rebuilding the card or other drafts.
`?request=<id>&item=<id>` opens the request and focuses the named item.

Main's simplified clear is request-level: only whole-request closure clears
the notification. A partial item answer leaves it displayed. Main also removed
the pending-request cap; references below to `MAX_PENDING` describe the earlier
design, not a current bound. This slice introduces no limiter.

### Routes (slice 2, after #4112)

Under the new app prefix, bearer-authenticated like `/app/me`:

- `POST /app/devices` `{platform, token, label}` → `{device_id}`
- `GET /app/devices` → the caller's own devices, **tokens never returned**
- `POST /app/devices/retire` `{device_id}`
- `GET|POST /app/notify` `{enabled}` — the owner's on/off
- `GET /app/sw.js` — the web-push service worker

#4112 replaces the `startswith("/mcp/")` auth-challenge rule with an enumerated
`_is_app_path`. **A new app route that is not added to that enumeration answers
anonymously.** That is the single highest-risk line in slice 2 and gets its own
negative test.

### App (slice 2)

The rail renders `items` as a checklist inside the tab, each with its own
Accept / Deny / Reply. A `?request=<id>` query opens the app with that tab
expanded. A settings row turns notifications on and off and lists the owner's
devices.

### Native (slice 2)

`@capacitor/push-notifications` in `mobile/`, plus a small Java plugin under
`mobile/native/android/` (the existing pattern — `mobile/android/` is
generated and gitignored, so native additions are a committed `.java` file plus
a `mobile/scripts/configure_android_push.py` patcher, matching
`configure_android_release.py`).

- `google-services.json` is **not committed**: it is materialised at build time
  from a secret, and `mobile/android/` is already gitignored. This repo is
  public.
- Tap → `pushNotificationActionPerformed` → the WebView navigates to
  `/app?request=<id>`.
- `Accept` / `Deny` / `Reply` (RemoteInput) actions **open the app and submit
  through the WebView's own session.** A truly background reply would need a
  device-scoped credential that can answer without the app — a second custody
  of the owner's authority — and that is explicitly out of scope. The UX cost
  is one screen flash; the alternative is duplicating the session credential
  into native storage.
- Android permits three notification actions, so per-item buttons are not
  possible on a multi-item note. Items are answered in the app. Stated rather
  than half-built.

### Desktop (slice 2, stretch)

`desktop-app/src/main.js` currently denies **every** web permission, notifications
included. Web push needs `notifications` allowed for the app origin only, with
every other permission still denied.

## Sequencing with the app-URL lane

#4112 moves the app to `https://tinyassets.io/app` and bumps the Android
release to 1.0.4. Push changes the native shell (a new plugin, a new
notification channel, `google-services.json`), so it **must ride the same
native release** — a Play closed-test clock that starts on a 1.0.4 without push
means a second submission. Slice 1 is route-free and native-free so it can land
independently; slice 2 rebases onto #4112 and its native half lands before the
1.0.4 upload.

## Live proof

The founder's universe wires its own morning note to the request items and its
owner answers it from the phone. Supporting evidence: the canary
(`scripts/mcp_public_canary.py --assert-handles`, since the connector surface
gains parameters), and a fake-transport HTTP test. Neither is the proof.

Diff and the yardstick are read. Now checking C2's inbound spec and a few code anchors the design cites.

**Review of PR #5228 (N1 inbound-door spec), read-only**

Most of the spec matches the yardstick. There is one route, `/hooks/inbound/{binding}`, with no aliases. The shared side does only verification and positive mapping, then wakes and forwards. Parsing, agent selection, the inbox, replies and subscriptions all run in the box (T8). Credentials stay with the broker and replies go through the proxy (T6). Deferral reuses C2's wake pump and adds no new waker (T7). It deletes the right code: `_enqueue_branch_run`, `_emit_source_event`, `_inbound_event_run_fn` and the body-hash dedupe in `webhook_inbound.py:103`. Dedupe moves to `(revision, event ID)`, with the start's idempotency key handed off to C7/C8. The places where it breaks follow, most severe first.

**HIGH: the main routing decision is left open, and one option puts payload parsing in shared code**
- `design.md:44` lets an app-wide provider signature be paired with an "authenticated recipient selector as identity data".
- For a platform-owned Slack, WhatsApp or Teams app, the provider posts to a single URL per app. The only way to tell owners apart is the body (`team_id`, `phone_number_id`, the Teams tenant).
- Reading those fields at the shared door is the "payload interpretation" that the table at `design.md:19` forbids. It is also owner routing in shared code, which goes against T1 and T5.
- The spec never decides between:
  - (a) each owner brings their own app or subscription, so every owner has their own binding URL and signing credential; or
  - (b) one platform app per channel, with a selector stored as declared data in the binding.
- If (b), the spec must say who stops two owners from registering the same selector. Today a box registers its own profile through its relay (`design.md:29`). Nothing requires selectors to be unique across owners, so one owner could claim another owner's workspace.
- Fix: decide this per channel in the `design.md:35-41` table. If (b), require unique selectors across owners in the binding map, and test a claim for another owner's selector.

**MEDIUM: an unadmitted owner gets 404, which conflicts with today's maintenance 503 and with the cutover window**
- `design.md:49` and `spec.md:15-17` return 404 for "unknown, disconnected or unadmitted".
- The router today answers an unadmitted owner with retryable 503 `maintenance` (`owner_router.py:118-120`).
- Direction #5 plans a maintenance window. In that window every admitted-but-closed owner would get 404, and many providers treat 404 as permanent: they drop the event or disable the subscription. Events would be lost during the cutover.
- Fix: keep 404 for unknown, disconnected or revoked bindings. Use 503 for admission that is temporarily closed, or state the trade-off explicitly.

**MEDIUM: making verification a broker call breaks T1's check that the router forwards with the control plane down, and the spec doesn't admit it**
- `design.md:22` and `:25` make every inbound POST call the broker to verify. A broker failure fails closed.
- T1's done check: with every control-plane lookup unavailable, the router still forwards.
- For inbound traffic it now won't. That may be the right trade, since secrets stay out of the router. But the spec should record it as a stated exception to the T1 check and say which T1 test changes.
- "Wired in existing broker RPC dispatcher" doesn't name which one. The broker has several (`client.py`, `lifecycle_control.py` and others), and `router_credentials.py` has its own socket. Name it.

**MEDIUM: existing generic webhooks have no event ID, and the spec doesn't say what happens to them**
- `design.md:57` requires generic senders to supply a stable idempotency key and drops body-hash dedupe.
- The spec doesn't name the header or profile field that carries the key.
- It also doesn't say what an existing token-URL sender without a key gets after cutover: rejected (breaking), or accepted with no dedupe (duplicate sessions on retry).
- `design.md:63` keeps those URLs, so the cutover needs this answered.

**MEDIUM: the test proving "one route, every event lands as `session.start`" only half exists**
- `design.md:76` defines `tests/test_inbound_door.py` well: it enumerates the route table, traces each channel to the real session entry, and fails on any direct enqueue.
- But task 2.1 (`tasks.md:14`) also runs `tests/test_served_webhook_triggers.py` and `tests/test_automation_events.py`. Neither file exists in this tree, though C2 marks both as done (`automation-event-triggers/design.md:58`, `tasks.md:11`).
- The tests that do exist are `test_owner_router.py`, `test_automations.py`, `test_owner_waker.py` and `test_owner_next_wake.py`.
- Fix the paths, or record that C2's tests went missing.
- The spec also has no scenario requiring the route table to contain exactly one inbound route. `spec.md:7-10` only says "no channel-specific ingress is used". Add a scenario that matches the guard.

**LOW: possibly over-built shared surface**
- `design.md:29` gives the shared broker JWT verification with "key refresh" fetches to public destinations. That is a shared network fetcher that only Teams and some mail providers need.
- Limit it to pinned keys that the box supplies with its profile through the relay, or justify a shared fetch per channel.
- The profile language is also broad: signed-byte recipes, header locations, JWT claims. Keep it to the smallest set the five reference channels need, with registration rejecting everything else (already stated, good).

**LOW: smaller gaps**
- **Unbounded receipts:** keeping accepted IDs for the whole life of a binding revision (`design.md:57`) grows the box inbox without limit. The provider freshness window would be enough to bound it.
- **Credential scope:** the profile's "credential reference" (`design.md:29`) should state that it resolves only within the binding owner's broker custody. A box shouldn't be able to point at another owner's credential.

**Process:** the docs are proportionate. The proposal duplicates part of the design, but nothing is over-built at the process level.

VERDICT: DISAGREE_EVIDENCE

# Channel-agnostic inbound: universal webhook URL + Source nodes + event triggers

## Why

Today, connecting a new channel (GitHub, Twitter, Stripe, Slack, anything) means the
platform ships channel-specific code. That is the wrong shape: it means we patch the
platform every time a user wants a new channel or a different use of one, and it makes
every channel a platform concern instead of a user-composed one.

The agreed shape (founder + universe design session, 2026-08-19 Slack): **a user brings a
channel by composing it from general primitives, and the platform never needs to know the
channel exists.** Once one user builds a channel's connector, the next user gets it for
nearly free (publish + remix, like any node). A brand-new channel tomorrow needs no
platform patch. This is the democratized-commons shape ([[platform-shape-democratized-commons]],
[[channels-must-be-user-built-not-hardcoded-effectors]], [[user-buildable-all-the-way-down-source-channels]]),
and it is the enabler for multi-user: every user is the founder of their own universe and
connects their own channels via their own credentials
([[every-user-is-founder-of-own-universe]]) — a universe can only ever fire a channel it
holds the credentials for.

Modeled on Pipedream's Sources/Workflows split (the cleanest of Vercel/Zapier/Pipedream/n8n).

## What changes

Three layers, phased. The whole system is inbound-channel-agnostic; Floor 1 alone delivers
end-to-end value.

1. **Universal inbound webhook URL** (platform builds once) — a stable, unguessable per-
   branch URL `https://<domain>/mcp/hooks/<branch-trigger-token>`. Any channel that can POST an
   HTTP webhook hits it and the bound branch runs, with the POST body as run input, executed
   as the branch's owning universe. Zero user code, zero platform patching. **The fastest
   floor — covers the majority of modern channels instantly.** This change's shippable slice.

2. **Source nodes** (community builds, users share) — a graph node whose one job is to emit
   events, kept "live" by the platform (cadence, or a held connection), channel logic inside
   the node and opaque to the platform. Publishable/forkable like any node. For channels
   that are not simple HTTP push (polling, WebSocket, OAuth refresh) or that want richer
   filtering. Forward design here; built in a follow-on phase.

3. **Event-trigger primitive + internal event bus** (platform builds once) — branches have a
   cadence trigger today; add a `source:<source-node-id>` trigger ("run this branch when this
   Source emits") and a tiny in-process event bus routing Source events → listening branches.
   Forward design here; built with Floor 2.

## Impact

- New public surface: an inbound `POST /mcp/hooks/<token>` receiver. Public-surface change → the
  §11 canary + trust-boundary review apply. The token is unguessable and scoped to ONE
  branch+universe, so an inbound POST can only ever trigger that one branch as that universe.
- No change to the canonical `/mcp` handle set. No change to existing effectors.
- Enables the founder's test plan: connect Twitter (creds already vaulted) end-to-end, then
  connect an undecided channel as a true "how easy is it for a user" test.

## The whole program (founder directive, 2026-08-19)

**Every channel — GitHub, Twitter, Slack, and all future channels — must work this way.**
The current Slack ingress, GitHub, and Twitter integrations are old, per-channel "spaghetti"
(e.g. `effectors/twitter_post.py` is a hard-coded, env-var, non-node-shaped outbound sink) and
are to be MIGRATED onto this channel-agnostic model, not extended. Both directions are
user-composed from general primitives:

- **Inbound** (this change): universal webhook URL (Floor 1) + Source nodes (Floor 2) +
  event-trigger/bus (Floor 3). A channel event triggers a branch, no per-channel platform code.
- **Outbound** (sibling change): one general authenticated-external-call node + named per-universe
  connection ([[channels-must-be-user-built-not-hardcoded-effectors]],
  [[push-is-a-user-built-graph-from-primitives]]). A branch acts on a channel (post a tweet, open a
  PR, send a message) via the universe's OWN vaulted credential — replacing the hard-coded
  effectors. This is what the founder's "connect Twitter, should work out of the box" test needs.

Once one user builds a channel connector, others reuse/fork/extend it like any node — no platform
patch, ever. This IS the multi-user enabler: every user founds their own universe and connects
their own channels via their own credentials ([[every-user-is-founder-of-own-universe]]).

## Non-goals (this change)

- Building Floor 2/3 (Source nodes + event bus) — designed here, built next.
- The outbound general-effector primitive + migrating existing Slack/GitHub/Twitter — a sibling
  change (needed for the Twitter test), tracked next.

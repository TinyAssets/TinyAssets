# ADR-031: Subscription Use Follows Each Vendor's Sanctioned Paths

## Status

Accepted (founder, 2026-10-26). Amends ADR-022 (its P2b row: "CLI packages
(token leased for one run, ADR-012 D5)") and ADR-012 D5 for consumer
subscriptions, and constrains ADR-023 (hosted CLIs), ADR-024 (device
runner) and the outside-agent wake recipes. Amended (founder, 2026-11-01):
a subscription powers any of the user's agents, scheduled and unattended
included, wherever its vendor's terms allow; decisions 1 and 3 are replaced.

## Date

2026-10-26; amended 2026-11-01

## Context

Users bring their own model access: API keys, local models, or consumer
subscriptions (ChatGPT, Claude, SuperGrok, Kimi). Vendors have closed
unsanctioned doors before: on 2026-04-04 Anthropic stopped Pro and Max limits
from covering third-party harnesses such as OpenClaw. A 2026-10-26 read of
the vendors' own terms (sources below) found:

- **Anthropic** allows a platform to host the *unmodified* Claude Code
  binary in a per-user sandbox where the user signs in through Anthropic's
  own flow, under Anthropic's Commercial Terms. It forbids developers to
  "collect, store, or intermediate Claude.ai credentials or session tokens",
  to "route requests through Free, Pro, or Max plan credentials on behalf of
  their users", or to offer claude.ai login or rate limits in their product
  unless approved. Its documented automation paths are scheduled-task `/fire`
  calls (30 an hour per task, 100 an hour per account, no overage), Claude
  Code Channels on the user's machine (allowlist during preview), and
  Max/Team API credits used through the user's own API key.
- **OpenAI** says "the right way to authenticate automation is with an API
  key". ChatGPT-login automation is documented only on trusted private
  infrastructure, one `auth.json` per serialized stream. Sign in with
  ChatGPT plan use is open to open-source and locally hosted apps; paid or
  hosted apps go through an interest form. ChatGPT scheduled tasks have no
  webhooks.
- **xAI** offers subscription use only through named partners. **Kimi**
  members may use their keys in third-party platforms, but must not tamper
  with the client identifier. **Meta Muse** is a paid API. **Cursor**
  automations take a webhook with the user's key.

## Decision

1. **Any source the user connects can power any of their agents,** scheduled
   and unattended work included: an API key, a sign-in, or a subscription.
   Most users with a subscription will power everything with it. A
   subscription is used only through a path its vendor sanctions: the
   vendor's own unmodified CLI in the user's sandbox or on their device, the
   vendor's own agent on its own platform reaching TinyAssets over MCP, or
   an approved sign-in program. TinyAssets' own loop never sends requests
   with a consumer subscription's credentials (Anthropic forbids routing
   requests through plan credentials on users' behalf).
2. **TinyAssets never holds consumer login tokens.** Claude.ai login
   tokens, `~/.codex/auth.json` and Grok session tokens are never read,
   copied, stored, proxied or logged by trusted code, and never leave the
   sandbox or device where the vendor's own sign-in wrote them. The
   per-task Claude `/fire` token is the exception, because Anthropic
   documents storing it in the caller's secret store.
3. **Hosted CLIs run any of the owner's work, scheduled and unattended
   included, where the vendor's terms allow it.** Each source record states,
   with the vendor page it cites, whether its vendor allows unattended use
   of a hosted CLI; a record that cannot cite it is refused for unattended
   turns until it can. Anthropic's legal page (read 2026-11-01) allows an end
   user's own sign-in to the unmodified Claude Code binary where a platform
   hosts it, without limiting it to human-started turns; its usage limits
   assume ordinary, individual use. The binary is unmodified and its sign-in
   methods are intact. Vendor names are
   never part of a TinyAssets product or feature name. Accepting
   Anthropic's Commercial Terms is a precondition for hosting Claude Code.
4. **Room-triggered work reaches subscription agents through the vendor's
   own door,** each one an agent recipe or source record (data), never
   vendor code in the platform (ADR-007, Direction 2): MCP pull and MCP Events for ChatGPT, scheduled-task `/fire`
   for Claude, Automations webhooks for Cursor-hosted agents, and Claude
   Code Channels on the user's device once the TinyAssets channel is
   allowlisted.
5. **The device runner launches only the user's own logged-in, unmodified
   CLI on the user's own machine,** reports its real client identity, and
   never copies credentials off the device.
6. **Subscription sign-in features wait for written approval** from that
   vendor's partner program (Sign in with ChatGPT interest form; Anthropic
   "previously approved" status; xAI partner integrations).

## Consequences

- A subscription user's agents, scheduled ones included, run on their
  subscription: in the vendor's hosted CLI where its terms allow, or as the
  vendor's own agent reaching TinyAssets. Usage counts against the user's
  plan limits, never billed or intermediated by TinyAssets.
- Broker custody of consumer login tokens is removed: the native
  subscription custody in `tinyassets/broker/subscription_custody.py`, the
  `state.location: broker` declarations in
  `tinyassets/providers/source_records/` (for example `codex.json`'s
  `.codex/auth.json`), and the custody requirement of the
  `box-subscription-sources` change. A hosted CLI keeps its sign-in inside
  the owner's box, written by the vendor's own flow. API keys stay broker
  custody (S28).
- A source record whose `invoke` drives a subscription CLI declares whether
  its vendor allows unattended hosted use, citing the vendor's page; the
  platform refuses an unattended turn only where that is not allowed or not
  cited. The record runs the CLI as the agent's engine, never as a hidden
  completion backend for TinyAssets' own loop.
- Build order: `openspec/changes/device-first-build/`.

## Sources

- https://code.claude.com/docs/en/legal-and-compliance
- https://code.claude.com/docs/en/routines
- https://code.claude.com/docs/en/channels
- https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers
- https://www.anthropic.com/legal/consumer-terms
- https://learn.chatgpt.com/docs/auth/ci-cd-auth
- https://developers.openai.com/siwc/quickstart
- https://help.openai.com/en/articles/10291617
- https://x.ai/news/grok-opencode
- https://www.kimi.com/en/help/kimi-code/membership-guide
- https://cursor.com/docs/cloud-agent/automations
- https://techcrunch.com/2026/04/04/anthropic-says-claude-code-subscribers-will-need-to-pay-extra-for-openclaw-support/

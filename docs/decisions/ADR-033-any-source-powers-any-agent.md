# ADR-033: Any Connected Source Powers Any Agent

## Status

Accepted (founder, 2026-11-01). Amends ADR-031 decisions 1 and 3, and the
Consequences bullets that follow from them (the paid hosted-CLI tier for
interactive work only; `trigger: human`). Its other decisions stand.

## Date

2026-11-01

## Context

The founder ruled on 2026-11-01 that users may power their agents with
whatever model source they choose. Most users who hold a subscription, from
any provider, will use it for all their agents, scheduled and unattended ones
included. ADR-031 had made API keys the default for unattended work and
limited hosted CLIs to turns their owner started. Anthropic's terms set
neither limit for the hosted, unmodified CLI.

A 2026-11-01 read of the vendors' own pages:

- **Anthropic** (https://code.claude.com/docs/en/legal-and-compliance, read
  directly): its credential rules do not prevent "an end user from signing
  in to the unmodified Claude Code binary with their own Claude
  subscription, including where a platform hosts Claude Code", under the
  Commercial Terms. It sets no limit to
  human-started turns, and its usage limits "assume ordinary, individual
  usage". It forbids third parties to offer Claude.ai login, to route
  requests through Free, Pro, Max or Ultra credentials on users' behalf, to
  collect, store or intermediate Claude.ai credentials or session tokens, and
  to pay for, resell or intermediate usage. It publishes no approval route
  for Claude.ai login; it points to sales.
- **OpenAI** recommends API keys for automation. It documents ChatGPT-login
  automation only on trusted private infrastructure, and Sign in with ChatGPT
  for hosted apps goes through an interest form.
- **Google's** Gemini CLI terms forbid third-party software using Gemini CLI
  OAuth (read through the vendor's repository, because its site is blocked
  from the research session).

## Decision

1. **Any source the user connects can power any of their agents,**
   scheduled and unattended work included: an API key, a sign-in, or a
   subscription. A subscription is used only through a path its vendor
   sanctions:
   - the vendor's own unmodified CLI, in the user's sandbox or on their
     device (ADR-031 decision 5, ADR-024);
   - the vendor's own agent on its own platform, reaching TinyAssets over MCP
     (ADR-031 decision 4);
   - a sign-in program the vendor has approved in writing (ADR-031
     decision 6).

   TinyAssets' own loop sends requests with a consumer plan's credentials
   only through such an approved program. Anthropic approves none, so the loop
   never runs on Claude plan credentials.
2. **A subscription CLI is the agent's engine, never a backend.** It runs the
   whole turn with its own loop and its own tools, from the agent's own
   definition (instructions, skills, files) and TinyAssets' MCP. TinyAssets'
   loop never sends it per-step completions.
3. **A hosted CLI runs any of the owner's work, scheduled and unattended
   included, where its vendor's terms allow it.** Each source record cites a
   vendor page that permits a user's own sign-in to the hosted, unmodified
   CLI and sets no limit to human-started turns. The Claude Code record meets
   that bar with the page above.
   - A record that cannot cite such a page is refused for unattended hosted
     turns.
   - That vendor's subscription then runs unattended on the user's device or
     as the vendor's own agent over MCP. OpenAI's and Google's plans are in
     this case on the 2026-11-01 reading, as is any vendor with no citable
     page.

## Consequences

- **What it covers:** a subscription user's agents, scheduled ones included,
  run on their subscription:
  - in the vendor's hosted CLI where its terms allow;
  - on the user's device;
  - or as the vendor's own agent reaching TinyAssets.
- **Billing:** usage counts against the user's plan limits. It is never paid
  for, billed or intermediated by TinyAssets.
- **Records:** a source record for a subscription CLI declares, with its
  citation, whether unattended hosted use is allowed. It also declares how
  the CLI runs as an engine:
  - its new-turn and resume invocations;
  - its event fields;
  - its instruction and skills paths;
  - its MCP configuration;
  - its tool and approval flags;
  - its config directory;
  - its credential files, which never leave the sandbox (ADR-031
    decision 2).
- **Unchanged:** ADR-031's decisions 2, 4, 5 and 6 stand.
- **Build:** `openspec/changes/clean-sheet-platform/` (the sandbox slice).

## Sources

- https://code.claude.com/docs/en/legal-and-compliance (read 2026-11-01)
- https://code.claude.com/docs/en/authentication
- ADR-031's sources, for OpenAI, xAI, Kimi and Cursor
- github.com/google-gemini/gemini-cli (its terms of service page), read
  through the repository 2026-11-01

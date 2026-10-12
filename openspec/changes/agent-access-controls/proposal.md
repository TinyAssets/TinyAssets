# Agent access controls (capability C27)

**Tier 2: authority.** One blocking review. The cross-family review is owed
before landing.

## Why

What a universe agent may DO is the owner's to set through the chatbot/MCP
(founder, 2026-08-18). The primitives that hold that authority already
exist: effector consents, the connection's `access` mode, the universe's
accepted model sources with their spending ceilings, and the pending-request
rail. The served agent cannot see most of them and can change almost none:

- There is no single readback of what it holds. The pieces are spread over
  `connections` and `agent_bindings`, and outbound channel consents and
  spend ceilings have no read on the served surface at all.
- `source_channel` is served as `approve` only. Nothing takes a consent back.
- The agent can raise a request but never withdraw one, so the owner's rail
  fills with asks the agent already knows are wrong
  (`docs/concerns/2026-08-28-an-agent-cannot-withdraw-its-own-stale-ask.md`).

Evidence: `2026-09-24-capability-gap-audit.md (archived in git)` row C27 (PR
#3947), `docs/concerns/2026-09-08-served-tool-capability-parity-gaps.md`.

## What changes

1. **`read_graph target=access`** (served and connector). One owner-only,
   secret-free readback of what the agent holds in this universe: channels
   with their `access` (`exact`/`full`), every active channel consent,
   workspace consents, spend allowances (accepted model sources and their
   ceilings), the requests it is waiting on, and standing decisions.
2. **`source_channel` gains `revoke`** in the shared implementation, so
   both the served `source_channel` verb and the connector's
   `write_graph target=source_channel` can take a channel consent back. The
   response carries the post-state read back from the enforcement store.
3. **`write_graph target=pending_request operation=withdraw`** (served;
   `operation=withdraw_request` on the connector's request operations).
   Withdraws a still-pending request the agent raised, records the reason,
   and takes the tab off the rail. Requests are stamped with an `origin`, so
   a platform-raised ask (onboarding's model confirmation) cannot be
   withdrawn by the agent.

No new top-level MCP tool. Every write is owner-scoped to the pinned
universe; a caller who does not own the universe reads and changes nothing.

## What does not change

- `approve` keeps every existing refusal: the agent still cannot grant
  itself workspace consent or approve `source_code`.
- `get_policy`/`set_policy` stay off the served surface -- and, as of
  2026-09-25, do not exist at all: the store had no reader, so they were
  deleted rather than served or rewired (see design D3). The owner loses no
  setting that was ever enforced.
- Answering, dismissing and un-muting stay person-only.

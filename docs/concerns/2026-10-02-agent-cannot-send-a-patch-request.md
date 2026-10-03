---
severity: P1
title: The agent cannot send a patch request; every attempt fails on consent or schema
filed: '2026-10-02'
summary: Asked to "send a patch request when blocked", the free-account agent tried three ways in prod turn d80d4383 and all three failed (self-approval of patch_intake refused; pending_request ask missing kind, then title), spending 3 of the day's 50 requests and sending nothing.
---

# The agent cannot send a patch request

**Filed:** 2026-10-02 (free-model-reliability lane). **Verified:** 2026-10-02, prod
`/data/.tinyassets.db` agent_turn_tools for turn `d80d4383d9c347c1ac19e2498100fcc5`, read-only.
**Owner:** dots-research (assigned by the lead, 2026-10-02).

## Source (verbatim tool results, prod)

- Round 31, `source_channel` `{"action":"approve","payload":"{\"channel_type\": \"patch_intake\", ...}"}` →
  `patch_intake consent cannot be self-approved: it is answered by the command center's owner on the
  request rail, where they read exactly what it allows. Ask for it there; this verb approves outbound
  channel sinks only.`
- Round 32, `write_graph target=pending_request operation=ask` with an `action` and `reason` →
  `request_invalid` / `kind is the tab header (e.g. 'API'); it is required`
- Round 33, same with `kind: "API"` → `request_invalid` / `title is required; the user is being asked for something`

The founder's instruction that turn: "whenever you hit something the platform can't do for you, send a
patch request about it through …". Nothing was sent; the founder never saw a request.

## Why it matters

On a free tier (50 requests/day) each failed attempt is a request. The agent learned the required
fields one error at a time. Either the agent's resident guidance should name the one correct way to
file a patch request (and its required fields), or the verb should accept what the agent naturally
sends and fill the rest.

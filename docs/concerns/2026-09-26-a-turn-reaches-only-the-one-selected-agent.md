---
severity: P2
title: A turn reaches only the universe's one selected agent
filed: '2026-09-26'
summary: reserve_prepared_turn re-resolves the single turn_consumer and refuses any other binding, so a custom UI can render rooms but cannot make "click room B" a turn; the bridge refuses by name instead of retargeting
---

# A turn reaches only the universe's one selected agent, so a custom UI cannot address a room

**Filed:** 2026-09-26
**Verified:** 2026-09-26, reading `tinyassets/consumer_runtime.py` on
`claude/custom-ui` (base `e32a6f40`)
**Severity:** P2 — a named founder use case is blocked; nothing is unsafe, and the
refusal is explicit rather than silent.

## Source (verbatim)

The custom-UI lane brief, requirement 5:

> Agent sessions: the bridge addresses a specific agent/session in the viewer's
> universe, so an office-building UI can "click a room → talk to that agent".

## The premise, checked

`converse` accepts a per-call `consumer_request` carrying `binding_id` and
`binding_revision`, which reads like per-turn agent addressing. It is not.
`reserve_prepared_turn` re-resolves the universe's selection and refuses anything
else (`tinyassets/consumer_runtime.py`):

```python
selected = resolve_selection_in_transaction(scope.author, owner=owner, universe=universe)
if (selected is None or selected["binding_id"] != intent["binding_id"]
        or selected["binding_revision"] != intent["binding_revision"]):
    raise PermissionError("consumer selection changed")
```

So `consumer_request` is a *receipt* for the one selection the universe already
has, not a chooser. A universe has exactly one selected conversation at a time
(`configuration.turn_consumer`). A custom UI can ask to change it
(`set_conversation_design`, approved by the person in the app's own prompt), and
the Switch UI dialog restores the default; there is no other chooser.

An office-building UI can therefore render rooms and read who is in them, but
"click room B while room A is selected" cannot become a turn.

## What shipped instead

`AppUI.sendMessage` resolves the named agent against the viewer's own bindings and
refuses by name — `"this universe sends turns to its selected conversation only,
and <name> is not it; change the conversation design first"` — rather than quietly sending to whichever
agent happens to be selected. Silently retargeting a message the user aimed at one
agent is the failure this avoids; the UI can act on the refusal.

## What would close it

Per-turn consumer selection: admit a turn against any of the viewer's own
configured consumer bindings instead of only the currently selected one. That is
an authority change plus a recovery-shape change, not a UI change:

- `reserve_prepared_turn`'s equality check becomes an ownership+eligibility check
  over the caller's own bindings.
- The in-flight record (`rememberInflight`, one `INFLIGHT_KEY`) assumes one
  outstanding turn; `keepInflight` already refuses a second. Parallel rooms need
  per-agent in-flight records or an explicit one-at-a-time rule.
- `resolve_selection_in_transaction` currently supplies `branch_version_id` /
  `content_hash` for source authorization; a per-turn path must authorize the
  source of the *requested* binding, and the owner-remix gate
  (`consumer_remix_required`) has to hold per binding.
- **Reservation is not the only gate.** There is a second selection check at
  execution time (`tinyassets/consumer_runtime.py`, in the dispatch path around
  line 328). Changing reservation alone would admit the turn and then reject it
  when execution starts — a worse failure than today's refusal, because the user
  would already believe the message was sent. Both checks move together or
  neither does. (Codex, reviewing PR #4038, 2026-09-26.)

Belongs in its own OpenSpec change — it is public surface and authority. Until
then, [the `composable-ui-experiences` change](https://github.com/TinyAssets/TinyAssets/tree/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/composable-ui-experiences) carries it as remaining work.

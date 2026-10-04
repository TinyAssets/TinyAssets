---
severity: P2
title: The app's whole-library UI write rounds numbers inside entries it carries through
filed: '2026-10-03'
summary: 'AppUI.install writes the whole ui_library, carrying entries it cannot render so they are not destroyed. The row has already been through the browser JSON parse by then, so an integer outside JavaScript''s exact range is rounded before the client sees it (9007199254740993 -> ...92). Reachable only via an extra field on an already-unrenderable entry; no client-side fix exists. The fix is for install to splice server-side with add_ui/replace_ui instead of rewriting the list.'
---

# The app's whole-library UI write rounds numbers inside entries it carries through

Found 2026-10-03 by a Codex cross-family review of PR #4360, which reproduced
it against the real Python store and the shipped controller.

## What happens

`AppUI.install` (`tinyassets/onboarding/app_ui.js`) writes the whole
`ui_library` field in one compare-and-set `save`. Since #4360 it carries the
entries it cannot render through that write, so installing a new UI beside a
broken one no longer destroys the broken one.

Those carried entries are not byte-exact. `fetchRow` has already parsed the row
as JSON, so any number outside JavaScript's exact integer range (|n| > 2^53-1)
was rounded *before* `install` ran:

    stored   {"future_state": {"seed": 9007199254740993}}
    rewritten {"future_state": {"seed": 9007199254740992}}

The surviving entry is still the person's data, so silently changing it is a
data-integrity bug, not merely cosmetic.

## How narrow it is

The component contract has only two numeric fields, `version` and
`assets[].size`, and `size` is bounded well below 2^53. So a number large
enough to round can only appear in a field *outside* the contract -- which is
itself one of the reasons the entry is unrenderable. The founder's actual
incident (`"version": 1791005187`) is nowhere near the limit.

## Why there is no client-side fix

Every client-mediated whole-list write has this property: the loss happens in
the browser's `JSON.parse` of the HTTP reply, before any application code can
intervene. Re-serialising more carefully does not help, because the precise
value is already gone. Before #4360 the bug was unreachable only because
`install` refused to run at all when any entry was unparsable -- and that
refusal is exactly the P1 that hid every one of the founder's UIs.

## What would resolve it

Stop having the client rewrite entries it did not author. The server already
has targeted, single-entry operations that splice the list server-side:
`write_graph target="app_ui" operation="add_ui"` / `"replace_ui"`
(`custom_agents._apply_app_ui_entry_operation`). If `install` used those
instead of a whole-library `save`, entries it cannot render would never be read
back and rewritten at all, and the carry-through logic could be deleted.

Not done in #4360 because that PR is a live P1 fix: `install`'s `save` path is
load-bearing for the remix and first-install flows, several tests assert its
CAS/revision behaviour, and the shared JS harness double only answers
`operation:"save"` for `write_graph target="app_ui"` -- so the change needs the
harness extended and those flows re-proven, which should not ride along with an
urgent fix.

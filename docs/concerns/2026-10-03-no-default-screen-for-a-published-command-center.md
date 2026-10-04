---
severity: P2
title: A published command center has no default screen
filed: '2026-10-03'
summary: 'Publishing a named screen packages the whole command center, but there is no stored default screen, so an installed copy does not open on the screen it was published as. Needs a storage decision; nothing in the tree carries the concept today.'
---

The founder's recommendation while fixing the publish discoverability bug
(#4379) was that **publishing a named UI should package the command center with
that UI as its default screen** — so that "publish my Fantasy Village" gives the
installer something that opens on Fantasy Village.

The first half shipped: the agent's guidance now routes a named screen to the
package form (`publish` + `ui_id` + `"package": {}`), so the screen travels with
the workflows and files that make it work. The second half cannot be written as
copy, because **the concept does not exist**: there is no `default_ui`,
`default_screen` or equivalent anywhere in the tree (the `default_view` hits in
`providers/provider_jail.py` are the jail's mount view, unrelated). An installed
package lands its screen in the person's library alongside their others, and
which one they see is whatever the switcher last remembered.

## Why it needs a decision rather than a patch

A default screen is per-command-center state a person can change, so it is
storage shape, which `AGENTS.md` wants specced before code. Open questions a
wrong guess makes expensive:

- **Whose default is it?** The publisher declares one in the package, but the
  installer owns their own copy and may reorder it. If the package's default
  wins on install and the person then switches, does their choice stick, and
  where is that recorded?
- **Is it a property of the command center or of the library entry?** The
  switcher moves between screens inside one command center, so "default" is
  most naturally a field on the command center; but packages install a *set* of
  screens, and the natural key for "the one to open" may be the library row.
- **What happens when the default is deleted or its UI is retired?** It has to
  degrade to the chat, not to a blank stage.
- **Does the existing published-screen flow need it too?** Publishing a screen
  without `"package": {}` already works and has no default to carry.

## What is true today

- `api/publish_requests.validate_action` accepts `ui_id` as "one UI in the
  owner's library" — a published screen, not a declared default.
- `command_center_packages` installs the screen into the library; it records no
  default.
- The app's switcher ("Switch command center") remembers the person's own last
  choice, which is the behaviour an installed package currently inherits.

Not a defect in anything that shipped: publishing and installing work, and the
guidance is accurate about what they do. This records the gap between that and
the founder's stated intent, so the slice that closes it starts from the
questions rather than rediscovering them.

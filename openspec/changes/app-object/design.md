# Design

## Decision

The app object is a projection of the command center, in `tinyassets/app_object.py` only.

- **Identity** is `universe_id`. There is no second id.
- **Name** is the universe row's learned display name. A display name that is
  still the id is not a name. When the row has no learned name, `universe.json`'s
  `name` is used if it has one. Neither source is written.
- **Description** is the universe row's `metadata.description` when that is a
  string, otherwise `universe.json`'s `description`.
- **Kind** is `command_center` for every existing command center.
- **Default screen** is the active UI the signed-in app can render. When there
  is no choice, the choice names a missing UI, or that UI cannot render, the
  screen is the platform blank screen (`platform:blank`), which is what the
  app already mounts.
- **Owner and author** are the account the copy is charged to. An installed
  copy runs as the installer, so that account is the author of the copy. A
  signed-in home that was never charged to an account is still listed, and
  listing it does not insert an owner row. A home charged to someone else is
  not listed.
- **Version** is the existing UI revision, `0` when no UI row exists.

## Launch

`GET /app/apps` returns the account's apps. The Apps control in the existing
`/app` shell lists them. Open on the signed-in command center shows the
command center view, whose default screen is already mounted. Open on any
other copy is refused with a reason: switching the signed-in command center
is not this change.

## Why not a new table

A stored app row beside `universes` would drift from the command center it
copies, and it would be a second path next to N11. Publish and install stay
where they are.

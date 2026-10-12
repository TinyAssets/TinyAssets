# An app is a command center you can open

## Why

The owner wants people to build and share any kind of app (a game, a fitness
app, a social app, a command center, a web app), gain users inside the
platform, and later export. A command center is the working prototype of an
app, and it is the app: it already has an id, an owner, a name and a screen.
A second apps table would be a second path.

## What Changes

- One read-only projection, `tinyassets/app_object.py`. An app's identity is
  the command center id. Its manifest is the learned name, the stored
  description, kind `command_center`, the screen the signed-in app already
  mounts, the account that holds the copy (owner and author), and the UI
  revision as the version.
- `GET /app/apps` lists that account's apps. The Apps control in `/app` opens
  the signed-in command center, which is the screen the app already shows.
- No new table, no migration, no MCP handle, no change to publish or install.

Not in this change: export, per-app users, offline support, store packaging,
metering, and opening a command center other than the one the account is
signed into.

## Capabilities

### New Capabilities

- `app-object`: a command center projected as an app, and the launch list.

### Modified Capabilities

None.

## Impact

- App route `GET /app/apps`, identity-gated like `/app/me`. The owner is the
  authenticated account. No request field names an account.
- Shape registry row N11. S16 stays the platform shell; this object lives
  inside it.
- Storage and publish paths are untouched.

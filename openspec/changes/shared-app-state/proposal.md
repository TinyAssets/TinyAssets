## Why

Several signed-in accounts have to use one app at the same time. The app
definition is stored once, under its owner. Each record's bytes belong to the
account that owns that record, and each run draws the seat and the provider
of the account it is charged to. Live shared state cannot wake an owner box
on every change: the box is per-owner, untrusted, and a cold start is the
slow path.

## What Changes

- One `app/v1` document per app, charged only to the app owner. The owner may
  replace that document at its etag. A second copy is refused.
- Records, an ordered public log, and a per-app sequence live in the author
  database, outside every box. A write conflicts on the record etag.
- Invites redeem into an app grant. A view grant sees public fields only.
  Neither copies the app. An invitee needs their own account.
- A declared transfer moves one integer field in one transaction, including
  across two apps that declare the same transfer. The record stays where it
  was stored.
- A declared clock step adds velocity to a position. Fields listed as server
  fields keep the stored value on a later client write. No seat and no run.
- A declared run is queued by the host on its interval and charged to the app
  owner. The pump records that owner's provider and seat. It does not call a
  model.

## Impact

- `tinyassets/shared_state.py` is the only path for this state.
- Storage accounts gain `app_definition` and `app_records`.
- A run started here draws `account_pool` for the charged account, not the
  app owner's pool unless that account is the owner.

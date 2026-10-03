# Design: owner UI preferences

## Context

- The chat cloud (#4277) keeps `{v:1, mode, open:{x,y,w,h}, bubble:{x,y}}` in
  `localStorage` under `app.chatCloud.v1:<owner>:<agent>:<viewport>`.
- Per-owner settings already live at the data root, keyed by the owner. The
  closest precedent is `account_timezone` (`tinyassets/storage/account_timezone.py`):
  one table in the canonical root database (`DB_FILENAME`), primary key
  `owner_user_id`. Per-universe platform state does not belong inside the
  provider-writable universe directory (concern filed in #4258), so this record
  sits at the root too.
- Account deletion builds its plan from the schema: every root-database table
  with a column in `PRINCIPAL_KEYS` (`tinyassets/account_deletion.py`), which
  includes `owner_user_id`, has the owner's rows deleted. A table keyed that way
  is deleted with the account without being named anywhere.
- App routes are identity-gated by `_app_identity_required()` and take the
  owner from the resolved identity (`tinyassets/onboarding/notifications.py`
  is the pattern).

## Goals / Non-Goals

**Goals:** the owner's chat-cloud placement follows them to any browser or app
install they sign into, per viewport class; it is private to them; it goes away
with their account; the app still works offline or when the route fails.

**Non-Goals:** syncing in real time between two open devices (last write wins on
the next load is enough); a general settings system; any MCP tool, engine tool
or custom-UI bridge action for it; preferences for other people. (The routes
trust the resolved owner identity like every app route, so a client holding
the owner's own bearer acts as the owner. This change adds no agent-facing
integration.)

## Decisions

### D1. One table in the canonical root database, one row per (owner, agent, viewport, key)

```
CREATE TABLE owner_ui_prefs (
  owner_user_id TEXT NOT NULL,
  agent_id      TEXT NOT NULL DEFAULT 'main',
  viewport      TEXT NOT NULL CHECK (viewport IN ('phone','wide')),
  pref_key      TEXT NOT NULL,          -- 'chat_cloud' today
  value_json    TEXT NOT NULL,
  updated_at    REAL NOT NULL,
  PRIMARY KEY (owner_user_id, agent_id, viewport, pref_key)
);
```

`pref_key` keeps the record from being chat-cloud-only without inventing a
settings system: a later per-agent preference is a new key, not a new table.
`owner_user_id` is the column name on purpose: it is in `PRINCIPAL_KEYS`, so
the deletion sweep covers it.

The table lives in the canonical root database (`DB_FILENAME`), like
`account_timezone`, not in a new store file: nothing about its lifecycle or
locking calls for a separate database.

*Alternatives rejected:* a column on `owner_notify_settings` (it couples
notifications to layout) and a new `.owner_ui_prefs.db` (one more root file and
registry entry, for no isolation benefit).

### D2. Keys, agents and values are a closed, small set

The server accepts only known `pref_key`s (`chat_cloud`), only `agent_id ==
"main"` (the one agent with a chat cloud today), and the two viewport classes.
The value is at most 2 KiB of JSON, and for `chat_cloud` it is validated to the
same shape the page parses (`v == 1`, `mode` open or bubble, finite numbers).
Anything else is a 400 with a reason. So an owner holds at most two rows (one
per viewport class) of under 2 KiB each. That is a closed key space, not a cap
on anything the owner builds.

Opening `agent_id` to custom agents comes with the multi-agent chat cloud. That
change must decide either to resolve the id against the owner's own agents, or
to attribute the rows to the account's storage. An open string here would let
one owner write unbounded rows that no storage accounting sees.

### D3. Routes through the owner door, owner from identity only

- `GET /app/ui-prefs?agent=<id>&viewport=<class>` returns
  `{"prefs": {"chat_cloud": {...}}}` for the caller, or `{"prefs": {}}`.
- `POST /app/ui-prefs` with body `{agent, viewport, key, value}` upserts the
  caller's row and returns `{"saved": true}`.
- No body or query field names an owner. A different owner's row is
  unreachable: every statement binds `owner_user_id` from the authenticated
  identity. `agent` is restricted to `main`, as in D2. Other addressed agents
  retain their existing device-local placement without calling these routes.

### D4. The page: server first, local fallback, write both

The page lays the cloud out from the local copy at once, then reads the server
record:
- **A record:** it is applied (once, and only before the owner's first gesture
  on this page) and cached into `localStorage`, so the next offline load has it.
- **A successful but empty read** (`{"prefs": {}}`, the state for every owner
  just after deploy): the local placement stays. If one exists, it is posted to
  the server, which migrates the owner's existing placement instead of replacing
  it with the default.
- **A failed read:** the local copy stays.

On each placement the page writes `localStorage` immediately and then posts the
server record, so the local copy stays valid if the post fails. The page never
blocks on the server.

Reads, writes and pointer callbacks capture the login epoch, owner, home,
preference key and a lifecycle generation. Account/home transitions invalidate
that generation, including leaving and returning to the same home. Reads and
writes recheck after token refresh, before sending a request; reads also recheck
before painting or caching. The record remains account-scoped: a home change is
a request fence, not a new storage dimension. Writes are serialized so repeated
gestures cannot complete in reverse order. Only a successful empty read migrates
the local record; a malformed response leaves it alone.

A placement gesture keeps precedence for its owner/agent/viewport and login,
including when the same owner changes home or switches viewport and returns.
A saved bubble received while the composer is active is deferred until typing
focus leaves. The draft and selection stay in place; a new gesture or lifecycle
change cancels deferred hydration.

### D5. Deletion and accounting

The canonical root database is already registered for storage accounting, and
the rows are platform-owned layout state (at most two rows of under 2 KiB per
owner, by D2). Account deletion covers the table through the schema-derived
plan. A test deletes an account and asserts the rows are gone and another
owner's rows remain.

## Risks / Trade-offs

- **Two devices placing the cloud at once:** last write wins at the next load.
  Acceptable for a layout preference.
- **A late server answer moving the cloud under the owner's hand:** D4 applies the
  server value only before the first owner gesture of the page.
- **Store growth:** at most two rows per owner, each under 2 KiB (D2).

## Migration Plan

Additive: a new table and two routes. Existing `localStorage` copies keep
working. The first load after deploy finds an empty server record and migrates
the local placement up (D4).
Rollback deletes the routes and leaves the page on its local copy.

## Open Questions

None. Per-viewport keys, owner-door routes and deletion through the existing
sweep follow established patterns.

# Tasks: owner-ui-prefs

Design approval gates task 1 onward. Depends on the chat cloud (#4277) being on main.

## 1. Store

- [ ] 1.1 `tinyassets/storage/owner_ui_prefs.py`, modelled on `account_timezone.py`: the `owner_ui_prefs` table (D1) in the canonical root database; `read_prefs(owner, agent, viewport)` and `write_pref(owner, agent, viewport, key, value)`, with key, agent (`main`), viewport, size and shape validation (D2).
- [ ] 1.2 Tests: round trip; owner isolation; refusals (unknown key, over 2 KiB, malformed `chat_cloud`), each leaving the stored value unchanged.

## 2. Routes

- [ ] 2.1 `GET`/`POST /app/ui-prefs` in `tinyassets/onboarding/__init__.py`, identity-gated, owner from identity only (D3). Rebuild the plugin mirror.
- [ ] 2.2 Tests: no identity gives 401; a body naming another owner changes nothing of theirs; the route list test includes the new path.

## 3. Account deletion

- [ ] 3.1 Test: deleting an account removes its rows and leaves another owner's rows (covered by the schema-derived sweep; no code expected).

## 4. The page

- [ ] 4.1 `app.html` chat cloud (D4): local layout first; a server record is applied before the first gesture and cached locally; an empty read keeps the local placement and migrates it up; a failed read keeps local; every placement writes both.
- [ ] 4.2 Tests: Node controller (a record wins and is cached; an empty read keeps and migrates local; a failed read uses local; a late answer is ignored after a gesture) and one real-browser reload test that starts with a server record and no local copy.
- [ ] 4.3 Update the `onboarding-web-app` chat-cloud requirement's persistence clause. Sync this change's spec and archive on land.

## 5. Verify

- [ ] 5.1 Linux oracle on the touched suites plus `tests/test_app_*.py`, a cross-family refute, and a live check: place the cloud on the desktop app, then open the web app at the same width and see the same placement.

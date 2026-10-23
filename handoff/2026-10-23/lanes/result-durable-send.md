Opened [draft PR #5237](https://github.com/TinyAssets/TinyAssets/pull/5237).

Head: `b42ee5bec2016c903b0b9bb55438f401007cfae4`

- Unified durable box intake; removed steering, destructive claims, and batch resend paths.
- Added independent browser recovery records, reply identities, and the CI `send-path` guard.
- Linux oracle: 84 passed; final focused rerun: 22 passed; final browser rerun: 1 passed.
- Ruff, JS syntax, OpenSpec validation, and commit hooks passed.

Production verification remains undone as requested. Crash-uncertain execution is retained without replay; legacy unidentified drafts require an explicit choice.
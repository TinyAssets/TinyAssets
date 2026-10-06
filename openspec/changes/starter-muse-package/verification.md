# K3 package verification

2026-10-06, feat/starter-muse-package.

Linux oracle command (MSYS_NO_PATHCONV=1):

```
python scripts/linux_oracle.py -- -q tests/test_starter_muse_package.py tests/test_starter_muse_browser.py tests/test_starter_instructions.py tests/test_converse_turn_cost.py --basetemp /tmp/b
```

Result: **37 passed**, no skips, 8.64 seconds in pytest. Python 3.11.17,
bubblewrap 0.12.0, uid 1001. Includes real Chromium in the shipped app_ui
frame/sandbox, all five views, pagination, refresh, text-only rendering, explicit
send, missing-file error; actual notify owner storage through a ta CLI local
transport adapter for goals, monitors and reminders; semantic changes, dial,
quiet first baseline, retry after failed notify, acknowledgement, cancelled and
stale watches, due-time parsing and unchanged-check deduplication.

Ruff passes on the changed Python source and tests. Plugin mirror rebuilt with
`python packaging/claude-plugin/build_plugin.py`; import probe passes.
Resident AGENTS/hooks and static prompt budgets are unchanged. The two old
bundle/index size assertions increased from 7/5 to 22/11 to require the expanded
content; no assertions removed, weakened or skipped.

Earlier attempts: oracle snapshot failed while a test was being added; rerun
with the tree frozen. Notify fixture initially set its data directory after
creating its account; fixed ordering, then all three real-notify modes passed.

Limits: this materializes the content publisher into a clean fixture, not a
production fresh account. Workflows are editable scheduled-agent templates;
tests execute their deterministic helper, not a connected model creating an
automation. No production installation, direct ta skill discovery, deployed SHA
or real-user pass is claimed. See the delivery prerequisite concern and tasks.

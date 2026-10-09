# Pre-existing tool-surface cleanup

Claude's review of PR #4563 approved all three K2 fixes and noted two non-blocking
follow-ups outside the fix lane. Disposition: AGREE.

- `tinyassets/engine_mcp_server.py` and `tinyassets/api/prompts.py` still contain
  "You have WebFetch and WebSearch" wording. Served models have neither native
  handle. A comment in `tinyassets/universe_intelligence.py` also mentions the old
  WebFetch inventory. Retire or correct this wording.
- `tinyassets/agent_loop/owner_reads.py` is unused by production after removal of
  direct history/activity handles. Delete it and retire its standalone tests,
  updating the continuity test and loop documentation that still refer to it.

Delete this concern when the wording and unused helper are retired.

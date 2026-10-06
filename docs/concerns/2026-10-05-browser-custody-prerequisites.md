---
severity: high
title: Browser custody lacks the D5 and MCP lifecycle prerequisites
filed: '2026-10-05'
summary: L12 cannot enable credentialed browser fallback until D5 supplies a restricted owner-cell browser and the MCP lane supplies its metadata and custody lifecycle.
---

The L12 assessment is recorded in
`openspec/changes/browser-login-custody/delivery.md`. D73's offline sandboxed
preview is implemented, but is not a persistent browser with live view,
accessibility snapshots, takeover and Stop. `universe-agent-harness` task 2.6
and the prerequisite `connect-anything-ladder` tasks remain unfinished.

Browser custody stays unavailable as required by its design's release gate.
Resolve after D5's real broker passes custody acceptance and the MCP metadata,
protected entry and coordinator lifecycle are available, then resume L12 tasks
1.1-1.8. A mocked broker or preview sandbox receipt cannot close this concern.

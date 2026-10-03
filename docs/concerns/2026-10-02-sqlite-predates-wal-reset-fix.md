---
severity: P2
title: Production SQLite 3.46.1 predates the 3.51.3 WAL-reset corruption fix
filed: '2026-10-02'
summary: the daemon image links SQLite 3.46.1; the checkpoint/write race Tailscale hit 19 times in 6 months is fixed only in 3.51.3, and our shape (WAL + several connections) is the one it needs
---

# Production SQLite 3.46.1 predates the 3.51.3 WAL-reset corruption fix

**Filed:** 2026-10-02
**Verified:** 2026-10-02 on production:
`docker exec tinyassets-daemon python -c "import sqlite3; print(sqlite3.sqlite_version)"`
printed `3.46.1` (Python 3.11.15).
**Severity:** P2. The race is rare, but a corruption would hit the platform
databases (`.tinyassets.db`, `.runs.db`, `.langgraph_runs.db`). The risk grows
if continuous replication (Litestream) is added, because it checkpoints
aggressively.

## Source (verbatim)

Tailscale, "SQLite WAL-reset bug" (2026-08-12),
https://tailscale.com/blog/sqlite-wal-reset-bug:

> if a write occurs at a specific time during a checkpoint, the checkpointing
> process gets confused—it thinks some of the pages have been copied from the
> WAL into the main database file, but they haven't.

The fix is in SQLite **3.51.3**. 3.52.0 also carried it but was withdrawn for
an unrelated issue. See also https://antithesis.com/blog/2026/wal-reset-bug/.

## What would resolve it

Ship an image whose Python links SQLite ≥3.51.3: a newer base image, or a
bundled `pysqlite3` build. Assert the version at startup (fail loudly below
it), and do this before adding Litestream. Then delete this file.

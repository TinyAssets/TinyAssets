---
severity: P3
title: Two flag-gated producers cache a scan on a directory's mtime, which misses in-place edits and same-tick additions
filed: '2026-10-01'
summary: '`producers/goal_pool.py` and `producers/node_bid.py` reuse a parsed YAML scan while the directory''s float `st_mtime` is unchanged. A directory''s mtime does not move when a file inside it is edited in place, and an entry added in the same coarse kernel tick as the scan looks unchanged too, so a changed pool or bid file can be ignored until some later add or delete. Both producers are OFF by default (`TINYASSETS_GOAL_POOL`, `TINYASSETS_PAID_MARKET`).'
---

# Directory-mtime caches miss edits

**Filed:** 2026-10-01. Found by grepping for mtime-keyed caches after #4224 fixed the same-tick flaw in `conversation_attention`.

## Where

- `tinyassets/producers/goal_pool.py`, `GoalPoolProducer._mtime_cache` (around line 300). It keys on `goal_dir.stat().st_mtime`.
- `tinyassets/producers/node_bid.py`, `NodeBidProducer._mtime` (around line 125). It keys on `bids_root.stat().st_mtime`.

## Why it is wrong

- A directory's mtime changes when entries are created, removed or renamed, not when an existing file's contents change. An in-place edit of a pool or bid YAML is invisible to the cache. An edit by atomic replace does change the directory's mtime, unless it lands in the same tick as the cached scan.
- `st_mtime` is a float, so it is less precise than `st_mtime_ns`. The kernel also stamps mtime from a coarse clock, so a file added within the scan's tick looks unchanged. This is the same flaw #4224 fixed.

## Not fixed, because

Both producers are flag-gated and OFF by default. The honest fix is to key the cache on every file's `(name, size, mtime_ns)`, cached only once settled, as #4224 does. That changes what "unchanged" means for each producer. A one-line racy-clean patch alone would leave the in-place-edit hole while looking fixed.

## Not affected

`tinyassets/providers/public_model_lists.py` keys an `lru_cache` on `(size, mtime_ns)` of a repo data file. That file changes only when a PR lands, which means a fresh container and an empty cache, and its docstring already reasons about the residual risk.

## Separate residual: credential reads trust (size, mtime_ns)

`tinyassets/credential_vault.py` `_read_credential_material` (around line 1074) stats a credential file before and after `read_bytes()`. It refuses when `(st_dev, st_ino, st_size, st_mtime_ns)` changed.

Our own writers replace the file atomically (`Path.replace`, around line 628), so the inode changes and the check catches any rewrite. The residual is a provider CLI rewriting its OWN credential file in place: same inode, same size, and inside one coarse mtime tick, during our read. That could pass a torn read.

Closing it means a second read and a byte-for-byte compare. The cost is one more read of a small file. It is auth code, so it needs cross-family review. Not changed here.

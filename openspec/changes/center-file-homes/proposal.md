## Why
C6 moves owner content into boxes, but pause, worker heartbeat, auto-ship ledger and soul presence currently cross host/box homes. This slice fixes the deploy-blocking mismatches and preserves the requested center identity.

## What Changes
- Define host and moved names once in boxes/layout.py; derive inventory classification from them.
- Route pause, heartbeat and ledger IO through link-free host helpers anchored on the caller's base.
- Route soul presence and reads through the broker-admitted owner's box, refusing linked/missing stubs before binding.
- Preserve requested center names in API and first-contact selection.
- Keep unbound engine discovery/refusal calls out of extension hooks: the stricter name validator otherwise rejects the empty center before the normal refusal response. This four-line reference dependency adds no content route.
- Add focused behavior, link-refusal, large-ledger and structural tests.

## Impact
Only PR-A on conv/c6-center-file-homes. File browser, automation brain, packages, soul_versions, config/wiki/canon/output, notes, read_page, queue_policy and status/activity readers stay in PR #5167. No migration or deployment changes.

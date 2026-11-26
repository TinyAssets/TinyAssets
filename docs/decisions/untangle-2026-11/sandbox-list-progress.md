# Completion of a sandbox listing

The sandbox Worker collects the owner's running and paused sandboxes before erasure. The deploy inventory uses the same E2B reader without an owner filter. A partial inventory cannot establish which sandboxes remain after a template move. Both callers already await the shared reader before acting on the result.

- [x] Reproduce a repeated page on main `d662e63e` with a local fetch fixture. For both an immediate repeat and a two-token cycle the reader requested a page it had already requested. It accepted the fixture's eventual final page. With the correction the shared reader reports an upstream error before repeating the request. The third case verifies that distinct opaque tokens advance through empty pages. Two tests failed and one passed on main; all three passed on the candidate. No arbitrary maximum page count was added.
- [x] On `9031d5ce`, 166 sandbox Worker and guard tests and 385 deploy tests passed (one deploy size case excluded), with the full platform typecheck on Node 22. The ordinary two-page inventory case passed independently. Fourteen local histories per Node 22 and 24 exercised both the owner-filtered Worker reader and the unfiltered administrative reader, including 128 advancing pages and three ways to repeat a token.
- [x] Rehearse with #5857 at `aa40cc0a` and the retention tests at `511c9391`. The full platform typecheck passed. The sandbox suite passed 171 cases with the same four move failures seen before this change. All three new pagination regressions passed.
- [ ] #5857 still needs a durable record of the verified destination and the old sandbox's pending retirement. See the [capacity and restart evidence](https://github.com/TinyAssets/TinyAssets/commit/c9ce0d5999747dc5ababbe82928da5aff342331c).

## One measurement per named sandbox

- [x] A running or paused sandbox can appear on two distinct pages. The
  [provider's team list](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/orchestrator/team_list_snapshot.go)
  describes this behavior. The tagged ID reader now returns each matching
  ID once in first-seen order. The pause probe also treats explicitly
  repeated IDs as one request. The administrative reader still returns
  every metadata observation for its route check.
- [x] On 72bb0ecb a two-page fixture repeated one ID. The actual probe
  resumed that sandbox twice during discovery and when its ID was supplied
  twice. Cleanup reported two kills for the same ID. In separate offline
  processes on Node 22 and 24, the corrected probe issued one resume or
  one delete in each case and still reported the synthetic command failure.
  All 168 ordinary sandbox tests and full Node 24 platform types passed.
- [x] Recheck the planned 14- and 30-day measurements. In five synthetic
  CLI runs on each Node version, discovery reported success with the scratch
  sandbox alone. Naming both original IDs correctly failed for the missing
  small sandbox and passed for the pair. Naming just the scratch sandbox
  passed for the single-sandbox measurement.
- [ ] Coordinator: use the recorded scratch sandbox ID alone for the
  14-day check and both original IDs from the start record for the 30-day
  check. Record individual outcomes before cleanup. Neither duration was
  measured by these offline runs.

The start report now identifies the scratch-only and original-pair checks.
A check identifies its requested or discovered IDs before resuming any, and
a successful result names the sandboxes that passed. Two offline start runs
(Node 22 and 24) generated both rows and the original pair. All ten offline
check runs retained their expected exit and row count. Both the standalone
and combined candidates passed all 22 listing and pause-probe guards.

On the preceding 344072b7 source, the combined sandbox rehearsal on main
d662e63e included #5857 aa40cc0a, slot wake and waiting-owner changes,
creation and expiry stages, late template reservation 65bd6de3 and cached
provider count 73909350. Full platform types passed. The first suite launch
stopped before collection because workspace dependencies were missing. With
the lockfile-matching packages present, 204 passed and 15 failed. All 11
previous failures remained. The four additional failures were the competing
starts, ordinary start and optional move racing a new reservation, and
successive starts receiving the same cached count. Both listing regressions
passed. No real pause duration was tested.

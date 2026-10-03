## 1. Contract (this change)

- [x] 1.1 Proposal, design and spec delta for I14.
- [x] 1.2 Cross-family review of the design: three rounds (ADAPT, ADAPT,
      ADAPT), cap reached; verdicts and the post-cap corrections are logged in
      `design.md` Appendix R.

## 2. Build (S6, after review)

- [ ] 2.1 ONE frame codec module that both the broker and `boxhostd` import
      (D2): control frames, byte frames, stream ids; property tests on
      framing.
- [ ] 2.2 Broker process (launched with `platform_secrets.child_env()`): socket,
      distinct-uid role map, supervised
      restart; fail closed where peer credentials are unavailable.
- [ ] 2.3 Per-stream authorization through the `resolve_exact_scoped_proxy`
      checks; box-channel principal derivation.
- [ ] 2.4 Streaming pinned HTTPS reader (capped collected request bodies): redirects and the OAuth retry
      before `HEAD`; credit-gated reads; idle and absolute deadlines.
- [ ] 2.5 Incremental secret scan with hold-back; split-secret tests.
- [ ] 2.6 Durable, namespaced `op_id` state machine (reserved, may_have_sent before the first write, terminal), `STATUS`, ULID expiry.
- [ ] 2.7 Fence: read/write lock around every write, barrier-minted token, monotonic persistence, reload on start.
- [ ] 2.8 `ScopedConnectionProxy.request` as a one-stream wrapper; delete the
      spawned worker; every existing caller's tests unchanged.
- [ ] 2.9 Async client for the thin loop (S7 task 2.3).
- [ ] 2.10 Measurements (design § Measurement owed); spec sync and archive.

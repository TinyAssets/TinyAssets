# Completion of a sandbox listing

The sandbox Worker collects the owner's running and paused sandboxes before erasure. The deploy inventory uses the same E2B reader without an owner filter. A partial inventory cannot establish which sandboxes remain after a template move. Both callers already await the shared reader before acting on the result.

- [x] Reproduce a repeated page on main `d662e63e` with a local fetch fixture. For both an immediate repeat and a two-token cycle the reader requested a page it had already requested. It accepted the fixture's eventual final page. With the correction the shared reader reports an upstream error before repeating the request. The third case verifies that distinct opaque tokens advance through empty pages. Two tests failed and one passed on main; all three passed on the candidate. No arbitrary maximum page count was added.
- [ ] Verify the two callers and the paged-list controls alongside the sandbox work on #5857. Establish the verified destination and durable cleanup separately on that PR.

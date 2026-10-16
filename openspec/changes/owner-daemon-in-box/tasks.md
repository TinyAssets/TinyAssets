# Tasks: owner-daemon-in-box

Every slice:
- is reviewed against the research shape check (design.md) before it is
  built, and again by one adversarial reviewer;
- deletes the path it replaces as handlers move; slice 1 retains shared paths for slice 2;
- is validated with CI's own selection (`affected_tests.py --pr`, chunked,
  `linux_oracle.py`) before push.

Nothing deploys until slice 7.

- [x] 0. Founder approves (2026-10-16: "Build what the research told you to build").
- [x] 1. The owner daemon uses relay admission and an owner data root,
      serving the existing ASGI app on a unix socket with local OwnerBoxd
      file tools and local bash/ta. Linux acceptance: app read, MCP read,
      chat/file write, pause/resume tick and no non-loopback connections.
      Host provisioning and the gVisor lifecycle remain slice 4.
      Validation counts and the existing main-baseline failures: `main-audit.md`.
- [x] 2. The router forwards authenticated requests to the box socket, with
      a per-box secret and streaming. Delete the shared-daemon owner handlers
      as each moves. Test: owner B's identity can never reach A's socket;
      an unknown owner gets 404.
      Slice 2 Linux results and local commits: `main-audit.md`.
- [x] 3. Broker socket into each box: model calls, connections, plan tier
      and commons reads. Test: the box holds no secret; egress outside the
      broker fails.
      D3 decisions and pre-cutover integration limits: `design.md` and
      `main-audit.md`; chunked Linux results and local commits: `main-audit.md`.
- [ ] 3c. Connection lifecycle at the shared door and broker, provider-agnostic
      (founder 2026-10-16: "support all connection types ... let users
      connect any provider, even ones we didn't think of").
      - What moves: tap-to-connect, OAuth authorize and callback (public and
        confidential clients, PKCE), API-key deposit, token refresh,
        disconnect and revoke, all run at the router/broker.
      - Credentials live only in broker custody. The box sees grants and
        credential-free source descriptors.
      - Providers stay data (dialect JSON plus source definitions): no
        provider-specific code.
      - Tests: for every auth type that exists today, connect, then a model
        call from inside a box succeeds, then refresh, then revoke.
- [ ] 4. boxhostd persistent per-owner sandbox lifecycle: wake, idle sleep,
      crash restart, memory cap, awake cap.
- [ ] 5. The waker and the next-wake index. Test: a scheduled automation in
      a sleeping box runs with no request.
- [ ] 6. Migration and census, with the shared root DB split by owner, the
      synthetic rehearsal, and the `--archive` rehearsal on the production
      copy (run by the lead).
- [ ] 6b. Per-box deploy roll and the canary-box acceptance (D11).
- [ ] 7. Report the list of unported, dropped features to the founder; then the cutover deploy (lead), with deployed sha asserted and one naive-user
      pass per surface: app, MCP and a scheduled run.

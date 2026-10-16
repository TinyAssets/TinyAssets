# Tasks: owner-daemon-in-box

Every slice:
- is reviewed against the research shape check (design.md) before it is
  built, and again by one adversarial reviewer;
- deletes the path it replaces;
- is validated with CI's own selection (`affected_tests.py --pr`, chunked,
  `linux_oracle.py`) before push.

Nothing deploys until slice 7.

- [ ] 0. Founder approves this design.
- [ ] 1. The owner daemon runs in a box on the LocalBoxProvider and gVisor
      with an owner data root, serving its ASGI app on a unix socket. Test:
      the app, an MCP read and a chat turn all work for one owner, with no
      network.
- [ ] 2. The router forwards authenticated requests to the box socket, with
      a per-box secret and streaming. Delete the shared-daemon owner handlers
      as each moves. Test: owner B's identity can never reach A's socket;
      an unknown owner gets 404.
- [ ] 3. Broker socket into each box: model calls, connections, plan tier
      and commons reads. Test: the box holds no secret; egress outside the
      broker fails.
- [ ] 4. boxhostd persistent per-owner sandbox lifecycle: wake, idle sleep,
      crash restart, memory cap, awake cap.
- [ ] 5. The waker and the next-wake index. Test: a scheduled automation in
      a sleeping box runs with no request.
- [ ] 6. Migration and census, with the shared root DB split by owner, the
      synthetic rehearsal, and the `--archive` rehearsal on the production
      copy (run by the lead).
- [ ] 7. Cutover deploy (lead), with deployed sha asserted and one naive-user
      pass per surface: app, MCP and a scheduled run.

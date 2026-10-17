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
      Folded in, from the 2026-10-16 compatibility audit:
      - DELETE the provider-CLI stack: `subscription_cli`,
        `claude_provider`, `external_agents` CLI launch, native discovery and
        launch, the CLI paths in `credential_vault`, and the CLI-credential
        deposit action in `api/llm_deposit.py`. Delete it; never leave it
        refusing.
      - Stripe and plan writes go to the shared plan store. A missing plan
        fails loudly; it never reads as `free`.
      - [x] Hook create and revoke publish to the router map (slice 2).
      3c-1 complete locally: CLI stack removed; shared tests use HTTP.
      Linux PR selection: 1,191 files in eight completed chunks, 23,463 passed,
      99 existing skips, zero failures. Required path/store guards: 61 passed,
      one historical-merge skip in the oracle snapshot. Ruff: 234 changed Python
      files pass. Retirement inventory and full logs are in the build scratchpad.
      No deployment in this slice; 3c-2, 3c-3 and task 6 remain separate work.
      Build order: 3c-1 deletes the CLI stack and provider-specific code;
      3c-2 moves the flows to the door with broker custody, refresh and
      RFC 7009 revoke; 3c-3 adds the plan store. Decisions: design.md D3c.
      - [x] 3c-1: CLI stack and provider-specific code deleted.
      - [x] 3c-2 admission: creating a center inside a box (universe create →
        `admit_center`) registers it in the broker's admission map through a
        bound-channel op, with the owner taken from the bind. Policy rules,
        effectors, source display and `ta_capabilities` reach the broker
        only through the channel.
      - [x] 3c-2a: broker-only custody and refresh, secret-bearing HTTP_CONNECT
        commit/rotation, broker DISCONNECT erasure, custody metadata and locks.
        Linux process proof covers six schemes; box-call refresh covers public
        and registered clients. Validation and deletions: `main-audit.md`.
      - [x] 3c-2 lifecycle: door flows and center selection, secret-free box
        connect/rotate actions, ledger request_ref completion and RFC 7009 revoke.
        All nine auth variants connect, call through a separate box process and
        fail after disconnect. Secret-bearing browser submissions use the door;
        keyless connects use the box channel. Validation and deleted tests are
        recorded in `main-audit.md` (3c-2d).
      3c-2 admission validation: 48 focused Linux tests passed; PR selection
      1,192 files in eight completed chunks, 23,506 passed and 99 existing skips.
      The 34-file heavy run exposed stale model-source fixtures/assertions;
      after repair, its full rerun passed all 1,357 tests. The two repaired files
      and their consumers passed 145 focused tests. Ruff passed on changed Python
      files. Logs and the exact remaining-work inventory: scratchpad `3c2-*`.
      - [x] 3c-3: `/app/billing/status|checkout|cancel|webhook` key plans
        by owner in `.broker/plans.db` through fenced `BILLING_PLAN`, never
        reading a home or `.subscription_state.db` at the router. Billing
        status omits private seat/storage usage and retains plan limits.
        Admission writes explicit free; `BOX_SHARED` raises `PlanMissing`
        for missing rows. `BOX_PUBLISH` copies definitions into `.published/`
        with authorship from the relay bind. Proof and deletions: `main-audit.md`.
- [x] 4. boxhostd persistent per-owner sandbox lifecycle: wake, idle sleep,
      crash restart, memory cap, awake cap.
      - Stopping a box on retirement also closes that box's open broker channels.
      - Retiring a center or deleting an account revokes the per-box secret and
        stops the box first; a stale forward then fails at ingress (D2).
- [ ] 5. The waker and the next-wake index. Test: a scheduled automation in
      a sleeping box runs with no request.
- [ ] 6. Migration and census, with the shared root DB split by owner, the
      synthetic rehearsal, and the `--archive` rehearsal on the production
      copy (run by the lead).
      - The migration changes the owner of `.platform/credential-vaults/` to the broker uid.
      - Copy credential ownership rows and deletion tombstones into
        `.broker/credential-custody.db`; validate every carried-over HTTP slot
        has its owner. No runtime reads of the old metadata remain.
      - Plans: copy each tier once from `.subscription_state.db` into
        `.broker/plans.db`. An unattributable paid tier stops the cutover.
        Never default to `free`. HTTP connections carry over in place
        (D3c).
      - It is ONE migration, from production's actual layout (owner-split,
        `fa29c95`) straight to the final shape.
      - DELETE the P2b intermediate move code in `box_layout_migration`
        (copying CLI credentials into host custody, copying CLI agent
        homes, the second activity-conversion pass for owner-box
        volumes).
      - Data is copied into the clean shape once (owner trees, owner rows,
        shared plan, hook and key stores). No runtime path reads the old
        layout afterwards.
- [ ] 6a. Delete every dual path at cutover (founder rule, 2026-10-16: never
      weaken the architecture for migration; reconnecting, signing in again
      or re-creating are acceptable user costs).
      - Delete each `owner_runtime.current() is None` shared branch listed in
        the 2026-10-16 audit, the `box_tools`/`start_exec` bridge and the
        per-call box backend (`frontend`, `production_files`,
        `file_worker`, `exec_worker`, `executions`, `gvisor_files`).
      - Delete the pre-C6 compatibility code: the `PROGRAM.md` premise
        mirror, first-contact's fail-open on old databases, the legacy
        launcher branch in `box_daemon_entry`, the consent move on every
        start, unused `owner_layout`, account deletion's legacy-bytes
        refusal, and copying `.premigration` files.
      - Delete the one-shot migrations once they have run:
        `box_layout_migration`, `activity_shape_migration`,
        `agent_file_migration` (and its deploy wiring), this change's
        migration, and the deploy script's old-layout branches.
      - Add a guard test that fails if a shared-daemon fork reappears
        outside the router entry.
- [ ] 6b. Per-box deploy roll and the canary-box acceptance (D11).
- [ ] 7. Report the list of unported, dropped features to the founder; then the cutover deploy (lead), with deployed sha asserted and one naive-user
      pass per surface: app, MCP and a scheduled run.

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
- [x] 5. The waker and the next-wake index. Test: a scheduled automation in
      a sleeping box runs with no request.
      Bound NEXT_WAKE, broker timestamp index and 30-second shared waker;
      24-hour floor and broker plan suspension. As built: design.md D4.
- [ ] 6. Migration and census, with the shared root DB split by owner, the
      synthetic rehearsal, and the `--archive` rehearsal on the production
      copy (run by the lead).
      Priority follow-up: reviewed parent-key attribution, masked file/loss
      reports, legacy-vault drops and opt-in paired restore after serving
      writes are implemented. Reason-coded row drops/refusals, masked file kinds
      and per-account loss summaries are implemented. Lead census, real-mount
      rehearsal and CI remain;
      task 6 stays unchecked until those acceptance steps pass.
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
      - [x] Delete each owner-runtime shared-work fork in the audited
        LEGACY_SHARED / UNREACHABLE_ROLE inventory, including owner_policy;
        retain only the 29 justified ROLE_DISPATCH boundaries.
      - [x] Delete the `box_tools`/`start_exec` bridge and per-call box backend
        (`frontend`, `production_files`, `file_worker`, `exec_worker`,
        `executions`, `gvisor_files`).
      - [x] Delete the `PROGRAM.md` premise mirror and implicit legacy loop,
        first-contact's fail-open on old databases, the consent move on every
        start, unused `owner_layout`, and account deletion's legacy-bytes refusal.
      - [ ] Delete the legacy launcher branch with the per-call backend;
        remove `.premigration` copying in slice 6's migration work.
      - Delete the one-shot migrations once they have run:
        `box_layout_migration`, `activity_shape_migration`,
        `agent_file_migration` (and its deploy wiring), this change's
        migration, and the deploy script's old-layout branches.
      - [x] Add D7's repository-wide alias-aware runtime-mode scan with the
        exact file/function/reason ROLE_DISPATCH allowlist and tmp_path module
        mutation self-tests; retain router import/direct-file-access checks.
      Part 1d completes the remaining 21 classified forks and D7. All 29
      role-boundary exceptions and reasons are executable and documented in
      `main-audit.md`. Part 1e deletes the exec bridge, per-call backend and
      legacy launcher and ports owner callers to local OwnerBoxd tools.
      Part 2 removes protected `.premigration` copying, one-shot migrations
      and deploy old-layout branches after slice 6.
- [x] 6b. Image-at-wake deployment and public canary acceptance (C12/D11).
      The per-box rollout is deleted. Publish once, drain through ordinary sleep,
      and wake on demand; the public canary completes sleep before acceptance.
      Root synthetic process proofs cover active-turn preservation and retryable
      admission. Deployment remains held; live acceptance is lead work.
- [ ] 6c. Return to the researched shape (design.md "Target shape" and
      "Corrections", 2026-10-19). Each lane names the shared code it deletes and
      gets one review round scoped to the router/map, egress proxy and waker.
      - [ ] Step 0: spec reviewed against research.md by the lead and one reviewer.
      - [ ] Step 1: the per-owner volume proof described in design.md "Step 1 proof"
            (deployment kernel, outer filesystem and runsc; six boxes, interruption,
            growth and exhaustion; capacity and timing recorded).
      - [ ] Lane a: C1–C4, volumes, per-owner snapshots, separate platform DR,
            and cutover rollback as the old system's restore; the migration writes
            each owner into their own volume.
      - [ ] Lane b: C5–C6, thin router and the deletion job, with admission as one map record.
      - [x] Lane c: C7–C9, one public-egress check with scoped credentials, action policy in
            the box, and a two-column wake index.
      - [ ] Real-data census and rehearsal on both copies; CI green.
- [ ] 7. Report the list of unported, dropped features to the founder; then the cutover deploy (lead), with deployed sha asserted and one naive-user
      pass per surface: app, MCP and a scheduled run.

## 8. After the cutover (live 2026-10-22): close the gap between live and the shape

The cutover is live, but fixes went live as hotfixes and some drifted from the
target shape. Every item names the rule (T1-T10, design.md) it serves. An item
that would add a check to shared code is reshaped or dropped: the router,
broker and waker get no new business logic.

- [ ] 8.1 Subscription CLI logins in broker custody, leased into a per-run tmpfs,
      refresh written back, nothing persistent in the box (T6; ADR-022 P2b;
      subscription credentials stay in broker custody, #5227, ADR-012). Branch `conv/source-custody`.
      Existing logins move into custody on first use.
- [ ] 8.2 Address discovery runs in the owner's box through its egress path; the
      broker keeps only the OAuth code exchange, client registration and deposit.
      Delete broker discovery and its time-budget guard (T1, T2).
- [ ] 8.3 Delete the runtime "dead member" pruning (source_connect,
      connection_uses, model_access_requests). The lead runs a one-shot repair of
      assignment members whose grants the migration dropped; the repair is
      deleted with the other one-shot migrations (6a part 2).
- [ ] 8.4 Subscription CLIs ship once in the shared box image, built only from
      committed per-record lockfiles (T4; no per-owner installs).
- [ ] 8.5 Deploy `conv/source-records` (with #5210, `conv/source-custody` and the
      lanes below) after one review scoped to the router, broker and waker. Assert
      the deployed sha, run the 8.3 repair, then empty the hotfix ledger
      (`/var/lib/tinyassets-deploy/hotfixes/`): every live hotfix is in the
      deployed sha or deleted. Live-only diagnostics are deleted, not folded.
- [ ] 8.6 The deploy carries its own config: the broker's outbound-HTTP flag, the
      box's public URL and the liveness directory's group mode come from the
      repo's compose, env templates or migration, never from host edits.
- [ ] 8.7 A box that cannot serve gives the router a 503 with Retry-After, never a
      500; the router only maps and forwards (T1). Branch `conv/router-503-schema-drift`.
- [ ] 8.8 Pending sign-ins never hold a box awake; cancelled login processes exit
      before the box reports idle (T7, T9; in the box).
- [ ] 8.9 Schema drift is handled once, by the store: an ephemeral table with an
      old layout is rebuilt, a durable one fails loudly. The per-table special
      cases are deleted. Branch `conv/router-503-schema-drift`.
- [ ] 8.10 A turn always ends with a reply or a clear stop: the last attempt is a
      tools-off reply, and a model that fails before reaching its provider moves
      the turn to the next candidate (T8, in the box). Branch `conv/turn-reply-budget`.
- [ ] 8.11 One durable send (shape-convergence C11, registry S24): the page's
      self-reload never drops a send or repeats "Still waiting to be sent" notices.
- [ ] 8.12 Sign-in stays in the user's context: the same tab on the web, an in-app
      sheet on phones, the system browser in the matching profile on desktop with
      return to the app.
- [ ] 8.13 Fold the remaining open PRs into `conv/source-records`: #5219's last
      round (no preferred provider; `_first_power_preset`), #5199 (router
      canary credentials). #5213 (app update prompts) rebases onto it.
- [ ] 8.14 Redo #4949 (broker credentials under a KMS key hierarchy) on the
      post-C6 broker custody, after 8.1 (registry S28).
- [ ] 8.15 Naive-user pass per surface (app, MCP, a scheduled run) on both test
      accounts, through the agent, after 8.5 (task 7).

### Next, once C6 closes

The founder-approved primitives (docs/architecture.md, Shape registry N rows)
that wait only on C6: N4 live takeover view, N5 owner egress gate, N6 undo by
snapshot, N8 optional PC link, N10 owner-only policy. N1 (inbound door) is
unblocked now. Specs first: N1 on `spec/n1-inbound-door`, N5 and N6 on
`spec/n5-n6-egress-undo`. N2, N3, N7 and N9 wait on the C3 and C11 tails.

Status of shape convergence lives in the Shape registry, not in the
`shape-convergence` checkboxes, which lag what has merged.

# Tasks — verified founder recognition on chat surfaces

## 1. Identity key (LANDED)

- [x] 1.1 `event_of` exposes `actor_team_id` — the sender's own workspace, from
      `user_team`/`source_team`, falling back to delivery team only when Slack
      supplies neither. Landed `eac5bbb9`.
- [x] 1.2 Tests for the reviewer's Slack Connect counterexample (foreign
      `U_COLLIDE` on our delivery team). Mutation-verified.

## 2. Evidence type

- [x] 2.1 `SocketModeAppEvent` in `app_event_ingress.py`, its own
      `_SOCKET_MODE_SEAL`. Carries `team_id` (delivery) AND `actor_team_id`
      (the sender's own workspace) because Slack ids are unique only within a
      workspace.
- [x] 2.2 **Found while implementing:** neither `app_conversation_authority.issue`
      nor `app_reply_authority.authorize` checked the seal at all — both
      delegated to `mapping.resolve`, so `_external_key`'s
      `is_authenticated_app_event` was the SOLE gate on signed Ed25519 custody
      grants. Widening it for founder recognition would silently have let
      socket events mint thread grants. Both now assert the request seal
      themselves; `is_admissible_principal_event` is the deliberately weaker
      identity-only predicate.

## 3. Durable replay admission

- [x] 3.1 `SlackSocketModeBoundary` composes identity with the existing
      `AppEventAdmissionStore`. Required adding `event_id` to `event_of`'s
      normalisation (payload wins; absent means absent) — it is the ledger key,
      so an inner value must not be able to stand in for it.
- [x] 3.2 Tested with a fresh boundary AND store object (a real restart), plus
      the resolver-level test that a redelivery mints no second grant.

## 4. The grant

- [x] 4.1 Sealed `FounderGrant` in `tinyassets/founder_grant.py`; the
      constructor refuses without the recognizer's seal.
- [x] 4.2 `FounderRecognizer.recognize` re-derives per call and returns `None`
      for every failure, never distinguishing why.
- [x] 4.3 **Decided, after two wrong answers: no cardinality rule at all.**
      "Exactly one admin on the universe" locked the real founder out the
      moment they added a co-admin. "Exactly one admin whose founder *home* is
      this universe" then broke multi-universe — `founder_home` holds one row
      per subject, so on a user's second and third universes the claimant set
      is empty and no grant could ever mint. Ownership is per-universe (the
      admin ACL row) and needs no uniqueness tiebreak; a non-owner stays
      structurally excluded, which is the property that matters.
- [x] 4.4 Each failure mode tested independently: stranger, Connect guest with
      a colliding id, revoked admin, second admin, rotated binding, deleted
      universe, unsealed evidence.

## 5. Wire it (fail-closed)

- [x] 5.1 `converse` takes `founder_grant`; passing both it and `tier` raises.
      A forged or wrong-universe grant is downgraded to the floor, not refused
      — an attacker must not be able to tell "rejected" from "unknown".
- [x] 5.2 `converse_as_external_sender` is the surface entry point and has **no
      `tier` parameter**, so a transport cannot claim authority even by
      mistake. `build_resolver` mints the grant; the handler only carries it.
- [x] 5.3 `SLACK_SENDER_TIER` deleted.
- [x] 5.4 12 guards mutation-verified (each disabled in turn; the named test
      must go red). Two findings from the probe itself: the Connect test was
      **vacuous** — it passed with the guard disabled because the lookup had
      already missed, so it now asserts the key derivation directly and a
      second test covers the guard; and the reply-path guard had no failing
      test at all.

## 6. Gates

- [ ] 6.1 Live proof: founder teaches Tiny through Slack, `origin.md` changes;
      a non-founder in the same channel teaches nothing and cannot read
      `founder.md`.
      **Unblocked in code, still needs a signed-in browser.** The setup surface
      now exists (`write_graph target=chat_surface`), so a founder mapping CAN
      be created; the remaining gap is that the Slack round-trip needs the
      host's logged-in session.
      Historical note — this used to read:  There is no way for a user
      to say "this Slack account is me". `AppPrincipalMappingService.provision`
      needs a trusted-setup resolver and has no user-facing caller, so no
      founder mapping exists in production — `u-tiny` has no ACL rows at all.
      This is exactly the surface the host described: *editing your custom
      agent, list the founder accounts for the platforms it should recognise
      you from.* Until it exists only the non-founder half is live-provable.
- [x] 6.2 Cross-family review returned **reject** with two findings that
      survived; both are now closed or reported:
      - **Seal defeated by `dataclasses.replace`.** `_seal` was a dataclass
        *field*, so `replace` passed it back to the constructor: a legitimate
        grant became one for another universe, and — worse, on the
        *pre-existing* `AuthenticatedAppEvent` — one admitted event could be
        rewritten to another sender and keep its seal. Fixed on all three
        sealed types by taking the seal off the field list.
      - **Cardinality locked out the real founder.** "Exactly one admin" would
        revoke recognition the moment the founder adds a co-admin. The first
        replacement (unique founder *home*) was itself wrong — see 4.3 — and
        the rule is now gone entirely in favour of per-universe ownership.
      Still open, both **outside this change's diff**:
      - `interlocutor.resolve_interlocutor_tier` returns **T2/FOUNDER for any
        `write` ACL holder**, so on the MCP path a collaborator reads
        `founder.md` and commits learning. Pre-existing; needs its own lane.
      - The seal is an importable module global, so arbitrary in-process code
        can mint a grant. That is the honest limit of in-process capability
        sealing — such code can equally call `converse(tier="T2")` — so the
        seal defends against a transport passing the wrong thing, not against
        code execution. Documented rather than overclaimed.
- [ ] 6.3 State the account-principal-not-human-presence invariant in the
      capability spec, not just this proposal.

## 7. Multi-universe (host 2026-08-05: users keep several)

Users have work / personal / hobby universes, and billing is total storage
across all of them. That made two shipped assumptions category errors.

- [x] 7.1 Ownership is per-universe. Dropped the `founder_home == universe`
      check: `founder_home` holds ONE row per subject, so it made a user the
      founder of exactly one of their universes and a stranger on the rest.
      It is a first-contact default, not an ownership predicate.
- [x] 7.2 Channel routing — `app_channel_bindings`, separate from the principal
      mapping because identity and routing are different questions. Conflating
      them is what capped a workspace at one universe. One rule: most specific
      wins (channel > workspace > the socket's host universe).
- [x] 7.3 Recognition follows the routing. Owning the socket's host universe
      says nothing about the one a channel binding pointed a message at, so
      ownership is re-derived against the ROUTED universe.
- [x] 7.4 A routed message reads the ROUTED universe's directory. Reading the
      socket host's would answer about one universe while grounded in another.
- [x] 7.5 11 routing guards mutation-verified.

## 8. Setup surface (reachability)

- [x] 8.1 `write_graph target=chat_surface` — connect_account / bind_channel /
      unbind_channel; `read_graph target=chat_surface` for resolved routing.
      An ACTION on an existing handle: hard rule 11 pins the live catalog, and
      a test asserts the canonical seven are unchanged.
- [x] 8.2 Authority derived, never asserted. Ownership is checked FIRST (the
      binding lookup ran first and refused strangers with
      "no_unique_agent_binding", which reads like a fixable setup problem
      rather than a universe that is not theirs). `payload_json` is filtered to
      an allowlist, so `subject_id` cannot reach a handler that derives
      authority.
- [x] 8.3 Every write answers with the RESOLVED routing, not the row written —
      a forgotten channel binding is exactly what makes the default surprising.
- [x] 8.4 8 setup guards mutation-verified.

## 9. Incident

- [x] 9.1 **Production outage, self-inflicted, 2026-08-06.** Deploying the
      pre-merge overlay with `docker compose -p tinyassets -f compose.slack.yml`
      converged the main project down to that one service and destroyed the
      daemon, tunnel, logs and four workers; `tinyassets.io/mcp` served 502.
      Recovered via `systemctl start tinyassets-daemon`; canary green with
      `--assert-handles`. Fixed by running the overlay in its own project
      (`-p tinyassets-slack`) with external volume/network, PROVEN by re-running
      the exact outage command and watching the main stack survive. Postmortem:
      `2026-08-06-partial-compose-overlay-outage.md (archived in git)`. Landing #2348
      removes the second compose file — but NOT the fencing; see 10.1.

- [x] 9.2 **Addendum — the overlay also FENCED production deploys, so the 24/7
      claim for it is retracted.** A container on `tinyassets-data` outside the
      main compose project is recorded as an "extra production-volume consumer"
      and the fence refuses to deploy
      (`retire_cheat_loop_deploy_fence.py:1619`). Two deploy runs failed until
      an operator dispatched `retire_extra_consumer=tinyassets-slack-agent`,
      which deletes it — so the agent silently stopped twice, with nothing in
      its own logs. A service a routine deploy is designed to delete is not
      continuously available. Integration is now proven in an EPHEMERAL
      container (prod image, temp data dir, no volume mount) instead. The
      durable fix is 10.1 below, NOT landing #2348 — that claim was wrong.

## 10. The agent must not mount the production volume

`current: 2026-08-06`. Cross-family reviewed (Codex: `confirm`, chose B over an
allowlist and over folding the pump into the daemon).

- [ ] 10.1 **Landing #2348 does not make the agent survive deploys, and the
      earlier claim that it would was wrong.** The fence keys on which
      containers MOUNT `tinyassets-data`, not on which compose project owns
      them, and `slack-agent` mounts it. Evidence it is structural rather than a
      missing allowlist entry:
      - ~18 call sites consume `Host.volume_container_names()`. Lines 474–477,
        1690/1710–1713, 2915/2953–2965, 3089/3095–3098, 3146–3147 require the
        consumer set to be EXACTLY the canonical five; 1758–1759, 2899–2900,
        3016–3017, 3075–3076 require it to be EMPTY during removal.
      - The kill path is wider than the equality checks: `restart=no` on every
        consumer (2268), stop (2297–2300), remove (2602–2614). The filter is
        `docker ps -a`, so STOPPING the agent does not clear it — which is why
        it was deleted six times between 03:36 and 04:25 UTC.
      - Docker labels are self-asserted, so a "verified label" allowlist is
        forgeable by the rogue writer the fence exists to catch.
- [x] 10.2a Server-side delivery exists: `tinyassets/api/app_ingress.py`
      `deliver_app_event()` does routing, replay admission, founder recognition,
      `converse` AND the reply post. Four of the five couplings now live here;
      only the socket-level app token stays with the transport. 12 tests, 7
      guards mutation-verified (each disabled in turn, named test must go red).
      Three findings from building it:
      - **The daemon must POST the reply**, not just compose it. The bot token
        is resolved from `(universe_dir, connection_id)` under the data dir, so
        a transport that posts is a transport that must mount the volume. That
        alone would have defeated the whole move.
      - **`fallback_universe_id` is gone.** The transport passed its own
        configured universe, which made "which brain answers" a caller-supplied
        value — the same defect class as a transport passing its own `tier`.
        Routing is now server-authoritative and fails closed; the cost is that
        an unbound installation is silent until `bind_channel` runs.
      - **The connection id is server-side too** (`DEFAULT_SLACK_CONNECTION`).
        It is not authority, but it selects a credential.
      The probe also caught a real defect rather than a test gap: recognition
      was failing closed on EVERY delivery because the admission envelope
      requires `event.type` and the first draft omitted it, so the replay guard
      was never reached. Two guards read as "vacuous" until that was fixed.
- [x] 10.2b Authenticated wire: `tinyassets/api/app_ingress_http.py`. HMAC over
      `purpose:timestamp:sha256(body)`, 300s skew window, `compare_digest`,
      field allowlist, 128 KiB cap. 18 tests; 9/10 guards mutation-verified
      individually and the 10th proven load-bearing as a pair (see below).
      - **Separate port, not a path on the public app.** Production runs the
        tunnel in TOKEN mode, so its ingress rules live in the Cloudflare
        dashboard, not this repo. Probing the live host shows every non-`/mcp`
        path 404ing, but a 404 from the app and a 404 from a tunnel that
        refuses to forward are indistinguishable from outside — so path
        isolation is unproven, and an internal route does not go on an app
        whose public exposure cannot be read from the repo.
      - **The key guards were tested vacuously at first, and the fix is a
        pattern worth keeping.** Asserting 401 while signing with the GOOD key
        proves nothing about a missing-key guard: the request fails on
        signature mismatch either way. The real attack signs with the key the
        deployment falls back to — `b""` — which the attacker knows. Tests now
        sign with the weak/empty key, i.e. they exercise the ACCEPT direction.
      - Removing the presence guard alone still 401s (an empty key trips the
        length guard), so it reads MISSED under one-at-a-time probing. Disabling
        BOTH together turns the test red, which is the honest statement: the
        pair is load-bearing, neither is individually necessary.
      - The timestamp is inside the signed message, so a captured request
        cannot be re-dated; every auth failure returns an identical 401 body so
        a caller cannot probe which check it failed.
- [x] 10.2b-ii Served + client. `serve_in_background()` starts the ingress on
      its own port from `universe_server.main()`; `app_ingress_client` signs and
      posts. 11 tests, including a ROUND TRIP — the client's own signature is
      fed to `handle_request` and must verify, so the two halves cannot drift.
      - **No key means no open port**, not a port that refuses everything: a
        port that is not listening cannot be probed or later misrouted.
      - The port must NEVER get a compose `ports:` entry. The daemon publishes
        `127.0.0.1:8001` and the tunnel's dashboard rule resolves to that
        loopback-published port, so publishing this one would put an internal
        route exactly where the public one already reaches.
      - A bad port value is refused, not defaulted — defaulting opens a
        listener somewhere unintended.
      - Missing key fails at BUILD time in the agent, so a misconfigured
        transport dies at startup instead of silently dropping every message.
      - `test_universe_server_five_handles` still passes: the ingress adds no
        MCP handle, so hard rule 11 is untouched.
- [ ] 10.2c **Blocks dropping the volume mount.** The agent still needs the
      Slack APP-level token to open the socket, and today it reads that from
      the vault ON the volume. Removing `tinyassets-data:/data` before this
      exists does not free the agent, it just breaks it differently. Deliver
      that one token over the same authenticated channel.
- [ ] 10.2d Honest note for the compose change when it lands: the service
      comment currently says it "holds no ambient credential to leak", because
      credentials came from each universe's own vault on the volume. After this
      it holds ONE — the ingress key. That is strictly less than mounting every
      universe's vault, but it is not nothing, and a compromised transport can
      still ASSERT an identity (including the founder's). That is inherent to
      any transport terminating the provider connection; what it can no longer
      do is read every universe's data. Say so rather than reusing the old
      sentence.
- [ ] 10.2c Deliver the socket-level app token to the agent over that same
      channel, so it holds one short-scoped credential and no volume.
- [ ] 10.3 Keep the agent a separate container. Folding the pump into the daemon
      was rejected: it would surrender the agent's own `mem_limit`/`pids_limit`
      and let a Slack defect degrade the MCP service.
- [ ] 10.4 Regression proof: with the agent running, a production deploy must
      complete without `--retire-extra-consumer`, and the agent must still be
      running afterwards. That is the actual 24/7 claim; nothing weaker counts.

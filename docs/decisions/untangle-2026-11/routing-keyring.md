# routing-keyring as built (2026-11-22)

`untangle/routing-keyring` is item 2 of the deploy-rollout series (`deploy-rollout.md`, fact 9,
#5782): `ROUTING_MAC_KEY`, one key, becomes `ROUTING_KEYRING`, a keyring. The research came
first (Fact 14), on `origin/main` at `c21c166d0`.

## Research

1. **The keyrings there are, and the one format.** `GATEWAY_KEYRING`, `FILES_KEYRING` and
   `OPERATOR_KEYRING` are each a ring in the envelope's form (`contract/src/envelope.ts`
   `parseRing`): `{"active":"k2","keys":{"k1":"<base64 of 32 bytes>",...}}`, at most 64 kids and
   5 KiB, the active kid among them; anything less is no ring, refused loudly by each reader.
   - **The current key is the ring's `active`.** `sealBlob` seals under it and writes its kid
     into the blob (`v1.<kid>.<nonce>.<ct>`); `signOperator` MACs under it and names it in the
     message's `kid`.
   - **Verification reads the kid the value names, and only that kid.** `openBlob` and
     `macHolds` look the kid up in the ring: absent, the value is refused. Nothing tries every
     key.
   - **The kit's tools** (`deploy.ts`): `keyring-new` (k1), `keyring-add` (the kid after the
     highest `k<n>`, made active at once, the old kept), `keyring-retire` (the active kid alone,
     written beside the old file). A held keyring is rotated only by `--rotate` with a ring of two
     kids or more (`secretPlan`), and its old kids leave only by mode `retire`.
   - **`retire` (r7, #5629; r8, #5636)** holds a swept keyring (`platform.json` `keyrings.rotated`)
     to its rotation record: the stored ring is the record's ring less every kid but the active
     one (by key hash), the latest pass swept every owner to `left` 0, and for `FILES_KEYRING`
     the off-account copy's versions under the kids have expired. Then `wrangler secret put` on
     each holder, with words `retire-<keyring>-<kids>`. `OPERATOR_KEYRING` has no retire: a MAC
     key strands nothing.
2. **Every producer and verifier of a routing value** (grep of `ROUTING_MAC_KEY` and
   `routing-mac` over `platform/`):
   - **Egress and sync tokens:** minted by the owner (`launch.ts` `openLease`), lease-lived
     (`LEASE_LIVED`, no expiry of their own); verified by the doors (`egress.ts` `sandboxToken`,
     `syncBound`). The egress token is in the sandbox's E2B network transform, the sync token in
     the sandbox's root-only `lease.json`.
   - **The socket ticket:** minted by the owner (`relay.ts`), 30 s (`SOCKET_TICKET_S`);
     verified by the doors (`binder.ts` `bindTicket`).
   - **The sealed route:** sealed by the owner at every start and sign-in (`launch.ts`,
     `signins.ts`), set by the sandbox Worker only when it creates a sandbox (`control.ts`
     `#ensure`, `ta_route`), opened by the doors on every E2B lifecycle event (`e2b-hooks.ts`).
     A route that does not open is answered `unrouted` and the event's billing is lost.
   - Tests and the egress memory leg mint too (`gateway/memory/leg-workers.ts`).
3. **The longest a routing value lives.**
   - E2B keeps a paused sandbox indefinitely and deletes it only on a kill
     (`e2b-resume-measurement.md`; ADR-034). E2B's API (`e2b-dev/infra` `spec/openapi.yml`,
     fetched 2026-11-22) has no call that changes a sandbox's metadata after its create (only
     its network, `PUT /sandboxes/{id}/network`). So **a route lives as long as its sandbox**,
     unbounded, and no sweep can reseal it.
   - A lease's tokens live while the lease is renewed (`files.ts` `leaseRenew`), which has no
     cap: unbounded too. But they live only in the sandbox that lease runs in, and every lease is
     opened by a start or sign-in that seals a route in the same request, from the same
     object (one version, one ring): a sandbox created there carries that route; one resumed or
     found running carries an older one; a joining run never creates a sandbox (`#ensure`
     answers `lost`). So **every live token was minted no earlier than its sandbox's route was
     sealed**: once no sandbox E2B holds carries a route under a kid, no live token is under it.
     A rollback to a version before the ring's rotation would break this, and mode `rollback`
     never takes one: a run that set a Worker's secrets is forward-only (`deploy-rollout.md`).
   - A socket ticket lives 30 s.
   - So the bound `retire` holds a routing kid to is **every route E2B holds, running or
     paused, opens under the ring left**: a live read the kit can make with E2B's key (`GET
     /v2/sandboxes?state=running,paused`, metadata included), exact, with no time bound and no
     owner op.
4. **How the secret is set.** `scripts/platform_setup.py secrets` makes each `platform.json`
   `made` secret into the `production` environment, only where absent, in memory, on stdin
   (`make_mac` made the single key). The kit sets each config's `secrets.required` from its
   inputs (`platform-deploy.yml`'s apply job `env`) on a Worker lacking it, or named by
   `--rotate`; a secret two Workers require (`SHARED`) on both or neither. The import window
   does not read the routing key. Cloudflare never shows a held secret, and no workflow token
   can write a GitHub environment secret.
5. **The order two Workers take a new key in.** The kit deploys callees first (`WORKERS`):
   the owner, which mints, before the doors, which verify. A ring with a new kid made active in
   one deploy has the owner minting under it before the doors hold it; under A2b's splits, for
   the whole stage. So the new kid must reach both rings before it is made active: two deploys.
   A single-holder ring (`GATEWAY_KEYRING`, `FILES_KEYRING`) has no such gap.

## The shape, checked against the intent

- **One home for the format:** the routing ring is an envelope ring (`parseRing`), made and
  grown by the kit's keyring tools. A token names its kid (`<owner>.<expires>.<secret>.<kid>.
  <mac>`, the kid inside the MAC), as an operator message does, so the doors look the key up by
  it. The route is an envelope blob (`sealBlob`/`openBlob` under its own domain), replacing the
  module's own AES-GCM copy of the envelope's scheme.
- **Mint and seal under the active kid; verify and open under any kid the ring holds.**
- **Retire waits on what is live, read live:** `platform.json` `keyrings.sandboxed` names the
  keyrings whose values live in sandboxes; `retire` of one lists E2B's sandboxes and stops while
  any route does not open under the stored ring (`routesHeld`). Opening, not reading the kid,
  also proves the stored key is the one that sealed them.
- **No founder-free migration exists that is not an alias** (weighed again in review, asked
  for a path with no founder step). The kit's input check could build a one-kid ring from
  `ROUTING_MAC_KEY` (32 bytes) when `production` lacks `ROUTING_KEYRING`, in about 20 lines, but:
  - **It keeps no live value working.** The old token is kid-less under `ta-sandbox-token-v2`
    and the old route is AES-GCM under HMAC(key, its domain), with no HKDF and no kid in its
    additional data. Keeping either needs `verifyToken`/`openRoute` to take the kid-less
    format: a downgrade path. So it saves only the founder's one command.
  - **It leaves two names for one secret, then a silent split.** The workflow would pass both
    names. The kit sets a held secret only when `--rotate` names it, so once `ROUTING_KEYRING`
    is in `production` (the setup makes any `made` secret it lacks, on any later run) it differs
    from the ring both Workers hold, with no word of it. The next `--rotate` of it then breaks
    every live value, past the cutover too. Or the alias stays for good.

  The kit cannot write a GitHub secret, nor read a held one. Pre-cutover nothing public
  reaches the doors, so the one break of live values is now, not later (Direction 5). So the
  name changes and the founder runs one command (below).

## As built

### The problems found

- One key for values that outlive every deploy: rotating it refuses every running sandbox's
  egress, sync and lifecycle events.
- `routing-mac.ts` kept its own AES-256-GCM sealing beside the envelope's (a copy of logic), and
  its own key grammar (`KEY_MIN_BYTES`, base64url) beside the rings'.
- `tokenShaped` had no caller (dead code).
- The sandbox Worker's list of an owner's sandboxes held E2B's paging; the kit had none.
- Found merging #5782: `--rotate` took a name no Worker holds and did nothing (a swallowed
  input), so `rotate: ROUTING_MAC_KEY` after this rename, or any typo, deployed with no secret
  set and no word of it; #5782's own rollback test rotated the old name so.

### The fix

1. **`contract/src/routing-mac.ts`** reads `ROUTING_KEYRING` as a ring. `mintToken` MACs under
   the active kid and writes it in the token; `verifyToken` refuses a kid the ring lacks and
   checks the MAC under that kid's key (domain `ta-sandbox-token-v3`). `sealRoute` and
   `openRoute` are the envelope's `sealBlob` and `openBlob` under the route's domain, async;
   `routeShaped` is the sandbox Worker's shape check (`parseBlob` within the longest route,
   `blobMax` of a 512-code-point owner and a 64-hex authority). `tokenShaped` and
   `KEY_MIN_BYTES` are gone.
2. **The doors and the owner** read `ROUTING_KEYRING` (configs, generated env, guards' rules);
   the owner awaits its seals, the doors their opens.
3. **The kit.** `platform.json` `made` makes `ROUTING_KEYRING` a `keyring` (the `mac` kind and
   `make_mac` are gone with their one secret); `keyrings.sandboxed` lists it, read as `SANDBOXED`
   (`facts.ts`), and `RETIRES` is what `retire` takes. `retire` of a sandboxed keyring needs the
   stored ring to be one kid (`keyring-retire`'s) and every route E2B lists to open under it;
   its words name the kid kept, `retire-ROUTING_KEYRING-but-<kid>`, since the kit cannot know
   the deployed ring's kids. Mode `rotation` refuses it by name. `keyring-stage` adds a kid
   without making it active and `keyring-activate` makes the newest active, for the two-deploy
   rotation (research 5). The E2B list with its paging is the transport's (`sandboxes`), read by
   the sandbox Worker's erasure and the kit. `parse` refuses a `--rotate` name no Worker holds
   (`SECRETS`), before any step.
4. **Docs:** the checklist's rotation and retirement steps for `ROUTING_KEYRING`; the workflow's
   notes and dispatch descriptions; the doors spec's token format.
5. **Review:** the route's E2B metadata key is one export, `ROUTE_METADATA` (`routing-mac.ts`).
   The sandbox Worker writes it and the doors and `routesHeld` read it. The kit had its own
   copy of `"ta_route"`: renamed at the writer, `retire` would have seen every sandbox as
   carrying no route and passed. The doors' app harness sets the ring on the owner too, and the
   setup's shape test checks that `ROUTING_KEYRING` is made as a keyring.

### Behaviour changed on purpose

- **Every routing value made before this deploy stops working**: a running or paused sandbox's
  egress and sync tokens (until its lease lapses and a new one opens) and its sealed route (its
  lifecycle events are answered `unrouted`, their billing uncounted, until the sandbox is
  killed). Pre-cutover only probe and canary owners' sandboxes can exist; kill them after the
  deploy if their billing matters.
- A deploy stops at the apply job's input check until `production` holds `ROUTING_KEYRING`.
- `retire` reads E2B's key for every keyring (its row's inputs), used only for a sandboxed one.

### Left, each with its reason

- **The two-deploy rotation is the founder's order, not the kit's check:** the kit never sees a
  held secret, so it cannot tell a staged ring from an activated one; the checklist and the kit's
  notes say it. A record of the ring each run set (kid to key hash) would let the kit check it,
  and the checklist's residual of a rotated ring that dropped the deployed key, for every
  keyring: owed below. No cheaper check exists now (review): the rollout record holds the
  names of the secrets a run set, not their kids. A skipped stage is loud and recoverable: the
  owner's new tokens are refused and its new routes go unrouted only until the doors take the
  ring in the same run.
- **A routing kid can stay in the ring for good.** Keeping one costs a ring entry; retiring it
  waits until every sandbox created before the activation is killed, by the owner's erasure or
  by hand. No sweep exists to bring a route under a new kid (research 3).
- **The old `ROUTING_MAC_KEY` copies stay where they are** until the founder deletes them: the
  `production` secret (nothing reads it now) and the doors' and owner's Worker secrets
  (configs no longer require it; a `wrangler secret delete` deploys at once, outside the kit).
- **The made keyrings have no copy to rotate from.** The setup makes every keyring in memory and
  keeps none, so `keyring-add`/`keyring-stage` "on your copy of the stored ring" has no input for
  any of the four. A founder question (keep an offline copy, against the setup's stdin-only rule,
  or make the rings with `keyring-new`), not an owed build.

## Founder step

Before the next `dark`: `python scripts/platform_setup.py secrets` (it sets `ROUTING_KEYRING`
in `production`, every other secret already set). Then, optionally, delete the `production`
secret `ROUTING_MAC_KEY`, and the Worker secret `ROUTING_MAC_KEY` on `tinyassets-doors` and
`tinyassets-owner`. Names only; no value is shown or needed.

## Owed

- [ ] `untangle/keyring-record`: the kit records each keyring it sets (its kids and their keys'
  hashes) in the control namespace, and refuses a `--rotate` whose ring drops a recorded kid, or
  activates, on a keyring two Workers hold, a kid the last-set ring did not hold.
- [ ] live: after the founder's step, the next `dark` sets `ROUTING_KEYRING` on the doors and the
  owner (its plan names both), and the canary passes; a later `retire --keyring ROUTING_KEYRING`
  apply job lists E2B's sandboxes and names its words without changing anything.

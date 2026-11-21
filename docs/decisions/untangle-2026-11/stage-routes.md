# stage-routes as built (2026-11-21)

The live stage's PR 2 (`stage.md`'s table; d3 §H; `stage_research.md` §6 PR 2): mode `routes`
serves the doors at whatever host `identity.json` names, the zone or one name under it. No
stage mode, flag or branch: each change is general to the host, and none is deleted at the
cutover but with mode `routes` itself at the hold's end (`cutover.json`).

1. **The host's DNS record.** A Worker route on a subdomain takes nothing without a proxied
   record (Cloudflare's routes page). `routePlan` reads the host's address records with the
   zone's routes (`placedBefore`); right after the route record is kept and before anything
   is deployed, `hostRecord` makes `AAAA <host> 100::`, proxied (`Cloudflare.makeDns`, the one
   new call), and writes it into the route record's new `host` field; `handback` deletes
   exactly it, by id, type and content, after the routes and the egress domain's record, and
   its read-back names it if it is left. Any other address record on the host stops `routes`
   before any change, the plan job too. On the zone nothing is read or made: its record is
   the site's (`held`).
   - **Departure, better:** a placeholder (`AAAA 100::`) the host already holds is left and
     recorded as held, so its handback deletes none, rather than stopping the run as "a
     record the kit did not make". The research's own fallback needs it (§8, §9 step 5: if
     the token's DNS write is refused, the founder adds that record and the run is
     repeated), and so does any later `routes` run while the stage stands (the earlier
     run's record, whose handback deletes it). It is the egress domain's rule: the doors'
     already is kept as it is. A re-run attempt that made the record before writing it in
     records the placeholder it finds, which the route record proves was not there before.
2. **E2B's hook by its name.** `heldHook` finds `tinyassets-lifecycle` by name, and
   `lifecycleHook` sets its URL to the identity's when it differs ("its URL (… before)
   set"), so a host change moves the one hook instead of registering a second. The canary
   holds an enabled hook to the doors' URL, which matching by URL used to do.
3. **The canary's two sides.** The doors are read at their host, the old stack at the zone,
   one document while the host is the zone. The doors' authorization server is their own
   origin (`doors/src/config.ts` `ISSUER = ORIGIN`, which `doors/guards/handles.test.ts` now
   holds), so `scripts/canary_handles.json`'s `new.authorization_server`, a copy of it that
   only guards read once the canary compares with `ORIGIN`, is deleted (with its assert in
   `tests/test_handles_canary.py`). **Departure from `stage.md` item 2**, which kept the copy
   as "the issuer the apex gets": on the stage the doors name the stage host, so the copy
   would be read by nothing. A host under the zone that the doors do not serve has no DNS
   record and answers nothing: the side read falls to the zone's document then, so after the
   stage's handback the cron's canary reads the platform dark instead of paging until PR 4.
   On the zone that read and the next are one, so an unreachable door stays exit 2.
4. **Already done before this PR** (the prompt's list, found so): `facts.ts` reads `ZONE`,
   the host, `ROUTES`, the callback and the hook URL from the identity (PR 1), and mode
   `probe`, `PROBE_ROUTES`, the workflow's `probe` choice and its checklist section went in
   `b24-manifest` (#5703). `platform-deploy.yml` is unchanged.
5. **The tests.** `deploy.test.ts` runs the kit as the cutover's: it mocks the identity's
   `host` to the zone, so flipping `host` (PRs 3 and 4) changes no case; the old proxy's
   table and every route string are written on the host. A nested `at a host under the
   zone` loads the kit afresh over an identity naming `stage.<zone>` (vitest `doMock`, no
   product parameter): the record made, kept and handed back; a placeholder held before
   left; a foreign record stopping `routes` and its plan job; a refused make; a re-run
   recording the record its killed first attempt made; the hook's URL moving. The canary's
   fake serves the doors at the host and the old stack at the zone.
6. **The checklist.** The stage's section (§7.1's steps) with the first sign-in's pass and
   fail signs, which were mode `probe`'s (`PROBE_SIGNS` before #5703); the DNS scope, the
   host record in `routes` and `handback`, the hook by name and the canary's sides.

**Proof.** Every plan the deploy harness reports, dumped on `main` and here (7,493 lines),
is identical but for the new scenarios' 270 added lines. The deploy guards, the canary's
tests and the handles guard pass with `host` the zone and, uncommitted, with
`stage.tinyassets.io`.

## Owed

- [ ] PR 3, `untangle/stage-host`: `host` is `stage.tinyassets.io`; then live: the stage's
  `routes` run, the token's first DNS write (the host's record).
- [ ] PR 4, `untangle/stage-end`: `host` is the zone again, and the checklist's stage
  section goes with the stage.

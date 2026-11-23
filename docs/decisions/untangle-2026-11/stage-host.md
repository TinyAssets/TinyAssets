# stage-host as built (2026-11-21)

The live stage's PR 3 (`stage.md`'s table; d3 §H; `stage_research.md` §6 PR 3 and §5.4):
`identity.json`'s `host` is `stage.tinyassets.io` until the stage ends. Nothing serves it until
the founder's `routes` run: the deploy is dispatch-only, so merging changes nothing live.

1. **The one value.** `host` is `stage.tinyassets.io`; `zone` stays `tinyassets.io`, and the
   egress host stays `egress.tinyassets.io` (`contract/egress.json`). The plugins, regenerated
   by `npm run generate`, change only their server URL (`chatgpt/mcp.json`, `claude/.mcp.json`);
   their home page and legal links are the site's, on the zone. `plugins/SUBMIT.md`'s first
   step says the host must be the zone again before a submission.
2. **Three copies of the doors' origin, found by the flip.**
   - `contract/apps.json`'s Muse Code step spelled `https://tinyassets.io/mcp`, the one place
     PR 1's `{mcp}` missed: it writes `{mcp}`, and `contract/guards/apps.test.ts` holds the
     raw file to spell no `<zone>/mcp`, which also catches the stage host's.
   - `doors/test/control.test.ts`' owner-surface helper (from #5741, merged after PR 1) sent
     `Origin: https://tinyassets.io`: it sends `ORIGIN`.
   - `doors/test/binder.test.ts`' two known answers hash the issuer, which is the doors' origin,
     by design (a stage client's ids are not production's): re-derived for the stage host and
     checked against an independent sha256 of the derivation's string. PR 4 puts the zone's
     back (`2b9b9126...`, `c6cf8165...`).
3. **The manifest** (`stage_research.md` §5.4). Phase 1 (`routes`) gains the stage's end, done
   before the window opens and taken out by `untangle/stage-end` when done: the stage's routes
   (the owners' erasure first, with §4.3's proofs, then `handback` of its route record, then
   `tinyassets-oauth` emptied and the control record closed), the host's DNS record, and the
   edits `untangle/stage-end` makes: `identity.json` less `stage.`, the two plugin files by
   `npm run generate`, and the checklist's stage section. Each names `stage.tinyassets.io` in a
   file that holds it only while the stage stands, so the manifest's guard fails a host flip
   that leaves them. The hold phase gains the stage's WorkOS redirect URI (a new live kind,
   `redirect`: the kit registers one and never deletes it), named in `tasks.md` Step 16; the
   route record entry already covers every route record, the stage's among them.
   - **Departure, better:** the plan put the checklist's stage section with PR 4 only
     (`stage-routes.md`). It is also a phase-1 edit here, so the manifest knows it goes before
     the window and PR 4 cannot leave it.
4. **`tasks.md`.** Step 8 opens in the checklist's order (`routes`, `handback`'s plan, the
   first sign-in with the gate closed, the gate opened to the two stage accounts alone, their
   keys) and every step signs in with a stage account, §8 too (already so). Its last item ends
   with the OAuth namespace emptied, the control record closed and `host` the zone again, the
   manifest's phase 1; Step 12 says those entries are done before the window; Step 16 names
   the stage's redirect URI.

**Proof.** `shippedPair`'s inputs, run before and after the flip: the same pair tag,
`tinyassets-sandbox:s85528cdd2cd74efa-r902c61908c03cb34-p30616a26de49b794` (the runner and
probes bundles are host-free since PR 1). After merging main (#5743 moved the runner), either
host again gives one tag, `tinyassets-sandbox:s85528cdd2cd74efa-rebbbb7c006973760-pa8968fa95f19ddff`.
The deploy harness's `routes` plan at the stage host
(the `at a host under the zone` kit, over the checked-in identity, dumped once, not committed):
the route record, one DNS record (`AAAA stage.tinyassets.io (100::)`, proxied, before any
deploy), the WorkOS redirect for the stage callback, the eight identity routes added on
`stage.tinyassets.io`, the egress custom domain, the hook moved to the stage host and enabled;
its only route calls are eight `POST`s, and the old proxy's five routes and the site's are the
same before and after. The deploy test, which pins the identity's host to the zone, passes
whole with the stage host checked in.

## Owed

- [ ] PR 4, `untangle/stage-end`: `host` is the zone again, the plugins and the binder's two
  known answers with it; the manifest's phase-1 stage entries taken out, done; the checklist's
  stage section gone, with the DNS scope's note that the stage's `routes` is the token's first DNS
  write (which by then has run).
- [ ] live: the stage's `routes` run (plan, then apply), the token's first DNS write (the
  host's record), on the founder's go.
- [ ] live: the founder's stage sign-in per the checklist's live stage, step 3 (the pass is
  `…/app/signed-in?error=temporarily_unavailable`), then steps 4 and 5.

# stage as built (2026-11-21)

The live stage's four PRs as cut today, and the first of them, `untangle/stage-zone`, as built.

Step 5's item "the live stage's four PRs, after B13, B14's canary and `control` (d3 §H)", re-cut at `origin/main` `88e74e1cd` against the design of d3 §H (`untangle-2026-11-remaining-build.md` §H, from `stage_research.md` of 2026-11-11), §F1, and Step 8's list, which is what the stage must run. The founder's direction of 2026-11-21 governs: the stage is the critical path, nothing is built before the cutover that the cutover deletes, and the comments and tests passes come after the cutover.

1. **The three things the stage waits on have merged.** B13: X4, the routes of d3 §J1 (#5574), and B13's sign-in values in the contract (#5610). B14's canary: X5 (#5583). The operator path's `control` mode: R3 (#5579). Mode `probe` is already deleted (`b24-manifest.md` item 5), so PR 2 no longer carries that.
2. **None of the four exists.** `identity.json` has no `zone`, no branch or PR is named for the stage, and `codex/untangle-stage-egress-order` (2026-11-18, unmerged, no PR) only moves Step 7's live egress checks onto the stage in `tasks.md`. It touches none of the four PRs' files but `tasks.md`.
3. **Since the research, the tree moved.** The redirect registrations file is gone, the URLs the doors and the kit share are `contract/src/identity.ts`'s (`ORIGIN`, `MCP_RESOURCE`, ...), and most doors tests already read them. What still conflates the zone with the host:
   - `contract/src/egress.ts` `ZONE` is `identity.host`;
   - `deploy/facts.ts` `ROUTES` puts the doors' routes on `ZONE`, which is the host's job;
   - `doors/src/pages.ts`' CSP names `wss://${ZONE}`, the app's own socket, which is on the host: on the stage the app's socket would be refused;
   - `plugins/build.mjs` takes the home page and the legal pages, which are the site's, from the doors' origin;
   - about 50 test lines spell the doors' origin as `https://tinyassets.io` (doors `api-keys`, `app-pages`, `app.e2e.mjs`, `brain`, `control`, `hardening`, `oauth`, `profiles`, `session`; owner `file-readers`, `search-cpu`). Each fails once `host` is the stage's.
4. **The stage as designed would not run production's pair (found while building PR 1).** d3 §H rests on "only `host` varies, so the runner and probes pair is the same bytes on the stage and in production". But `contract/src/egress.ts` imports `identity.json` for `EGRESS_HOST`, and the sandbox's bundler inlines the JSON whole: the runner and the probes each carry the doors' `host`, routes and paths. So PR 3 would have moved the pair tag, and the stage would have tested a pair production never runs. The fix is in PR 1: the egress host's one home is its own data file, `contract/egress.json`, which `egress.ts` reads as `EGRESS_HOST`; it leaves `identity.json`, and the kit reads `EGRESS_HOST`. A literal in `egress.ts` would also have kept the pair still, but a live name belongs in data. Measured: the bundles change once in PR 1, then are the same bytes with `host` the zone or the stage's.

## The four PRs

| PR | Branch | Files | Blocked by | Live? |
|---|---|---|---|---|
| 1. The identity names its zone and its host; the egress host leaves it | `untangle/stage-zone` | `contract/{identity,egress}.json`, `contract/src/{identity,egress,wake-records}.ts`, `doors/src/{authorize,pages,config}.ts` and three comments, `deploy/{facts,deploy,canary}.ts`, `deploy/guards/{deploy,canary}.test.ts` and one `checklist.md` line (the imports, `ROUTES` on the host, `EGRESS_DOMAIN` read as the contract's `EGRESS_HOST`), `plugins/build.mjs`, `contract/{apps.json,src/apps.ts}` (the MCP door as `{mcp}`), `doors/guards/identity.test.ts`, `sandbox/guards/template.test.ts` (no identity in the pair), the doors' and owner's tests that spell the doors' origin | nothing | no: every value is the same while `host` is the zone; the sandbox pair moves once |
| 2. `routes` serves the doors at the identity's host | `untangle/stage-routes` | `deploy/{deploy,cloudflare,canary}.ts`, `deploy/guards/{deploy,canary}.test.ts`, `deploy/checklist.md` | PR 1 (it needs `zone` and `HOST`) | no: release-critical, proved by the harness |
| 3. The doors' host is `stage.tinyassets.io` | `untangle/stage-host` | `contract/identity.json` `host`, `plugins/{chatgpt,claude}/` regenerated, `platform/cutover.json` (the stage's end in phase 1, its redirect and route record in the hold phase), `tasks.md` (§8 and Step 8 on the stage) | PRs 1 and 2; Step 7's dark redeploy is not needed first | **changes routes when run**: merged dark (the deploy is dispatch-only), routed only by a `routes` run on the founder's go |
| 4. The doors' host is `tinyassets.io` | `untangle/stage-end` | `contract/identity.json` `host`, the plugins regenerated, the manifest's phase-1 entry done | the stage's end (each stage owner's erasure, then `handback`) | merged before the window opens |

PR 1 is built now. PRs 1 and 2 are sequential, not parallel: PR 2's host-under-the-zone case reads PR 1's `zone` and `HOST`, and both edit `deploy/{facts,deploy,canary}.ts` and their tests. PRs 3 and 4 are one value each and wait on the order above. So no second builder can start on this lane until PR 1 merges.

## Each PR, checked against d3 §H

1. **PR 1: the zone and the host, each read where it is meant.** `identity.json` gains `zone`; `contract/src/identity.ts` exports `ZONE` and `HOST` beside `ORIGIN`, and `ZONE` leaves `egress.ts`, which keeps only the egress wire and reads `EGRESS_HOST` from `egress.json` (item 4). The kit's `EGRESS_DOMAIN`, a second name for it, goes. Readers of the zone: the own-hosts rule (`authorize.ts` `ours`), the callback bar (`wake-records.ts`), the kit's zone lookup and route record. Readers of the host: `ORIGIN` and all built on it, `ROUTES`, and the CSP's socket. The plugins' server URL is the host's; their home page and legal links are the site's. The identity guard holds `host` to the zone or one label under it (Universal SSL covers no deeper name), and `EGRESS_HOST` to one label under it, so the zone written in `egress.json` is held to the identity's. Tests that spell the doors' origin read `ORIGIN`; tests of the zone rule keep the zone's spelling, since the zone never varies. `contract/apps.json` spelled the MCP door's URL eight times (links, steps, code, the chat-act sentences); it now writes `{mcp}`, which `apps.ts` fills from `MCP_RESOURCE` (encoded in a link), so the steps name the doors' own door on the stage and in production alike, and the site's `/connect` page built from them at the cutover (§9) does too. Left as they are: the `/connect` page's own address, in `apps.json` and the starter's skills (the site's, on the zone), and the shells' and site's `/app` links (production's, outside `platform/`).
   - **Proof:** nothing a Worker serves changes while `host` is the zone: the touched tests pass, the plugins regenerate byte for byte, and `checkedApps()` is the same JSON. The runner and probes bundles change once (they lose the inlined identity), which joins Step 6's owed pair check and pin PR; Step 6's paid template build has not run, so it costs nothing extra. Then, once and not committed, with `host` set to `stage.tinyassets.io`: the bundles' shas are unchanged, and the doors' (33 files), owner's, gateway's and contract's tests that read the identity pass but two, each owed below: the handles record's issuer (PR 2) and the binder's known answers (PR 3). The owner app's browser test passes whole on the stage host (22 cases), its socket under the CSP included. The deploy harness fails 11 cases, each the old proxy's apex handover or handback, which PR 2 rewrites relative to the zone.
2. **PR 2: `routes` at a host under the zone.** As d3 §H's "the kit learns three general things", less `probe`, which is gone:
   - when the host is not the zone and holds no DNS record, `routes` makes the proxied `AAAA 100::` before anything is deployed, keeps it in the route record, and `handback` deletes exactly it; a record there that the kit did not make stops the run. This is a precondition of any route on a subdomain (Cloudflare's routes page), not a stage mode, and the cutover deletes none of it;
   - E2B's lifecycle hook is found by its name and its URL moved to the identity's when it differs (today it is matched by URL, so a host change would register a second);
   - the canary's old side is the zone's `/mcp`, its doors side the host's; `scripts/canary_handles.json`'s new side names the issuer the apex gets at the cutover, so `doors/guards/handles.test.ts`, which holds it equal to the doors' `ISSUER`, says which of the two it means;
   - the old proxy's route table in the deploy tests is written relative to the zone, with scenarios for a host under the zone (record made, kept, handed back; a foreign record stops it) and for the hook's URL moving;
   - `checklist.md`: the stage's section (the steps of `stage_research.md` §7.1).
   - **Proof:** with `host` the zone every scenario's plan is unchanged; with the stage host the plan adds the routes, one record, the egress domain, and touches no apex route.
3. **PR 3** is the one that changes routes once the founder dispatches `routes`. It also re-derives `doors/test/binder.test.ts`' two known answers, which hash the issuer by design (a stage client's ids are not production's), and PR 4 puts them back. Its proof: `shippedPair` gives the same pair tag before and after (item 4 makes it so).
4. **PR 4** is d3 §H's "the cutover adds one line".

## Against the founder's direction

- **Nothing built here is deleted by the cutover.** PR 1 is the identity's permanent shape. PR 2's three changes are general to any host the identity names, and they stay: after PR 4 the record step finds the zone's own record and does nothing, and `handback` stays until the hold's end as it already does. No stage mode, flag or branch exists in product code.
- **No tests pass rides PR 1.** It rewrites only the test lines that spell the doors' origin, because PR 3 breaks each of them; nothing else in those files is touched.

## Owed

- [x] PR 2, `untangle/stage-kit`: `routes` makes and hands back the host's DNS record, finds E2B's hook by name and moves its URL, and the canary reads the old side at the zone and the doors at the host, with the handles record's issuer guard to match (item 2 above).
- [ ] PR 3, `untangle/stage-host`: `host` is `stage.tinyassets.io`, the plugins and the binder's two known answers regenerated, the stage's end on the manifest; then live: the `routes` run that opens the stage, on the founder's go.
- [ ] PR 4, `untangle/stage-end`: `host` is `tinyassets.io` again, the plugins and the known answers with it, once the stage ends; merged before the window opens.
- [ ] live: Step 6's pair check and pin PR take the runner and probes bundles this PR moved (they no longer inline `identity.json`); no template build has run since, so it is the check Step 6 already owes.
- [ ] live: the owner app's socket on the stage host under the CSP's `wss://` host, first seen at the stage's first signed-in page.

## Founder steps (from `stage_research.md` §9, still current)

1. Approve the `routes` run that opens the stage and the `handback` run that closes it (`production` environment).
2. Make two stage accounts at `https://stage.tinyassets.io/app` with addresses that are not his main one; each ends at `…/app/signed-in?error=temporarily_unavailable`. If sign-up is closed, create them in WorkOS → Production → Users.
3. Send their two WorkOS user ids, which go into the control record's canary list through mode `control`.
4. Sign in as each, mint one API key each, and add `https://stage.tinyassets.io/mcp` as "TinyAssets stage" in Claude and ChatGPT.
5. Only if the kit's DNS write is refused: add `AAAA stage → 100::`, proxied, in Cloudflare's DNS for `tinyassets.io`.
6. At the end: each stage account's own erasure in the owner app; remove "TinyAssets stage" from both apps.
7. At the hold's end: delete the stage's redirect URI in WorkOS.

# deploy-readback (2026-11-22)

`untangle/deploy-readback` fixes three defects of the deploy package seen live on 2026-11-22 on
the stage (`stage-routes.md`, `deploy-zero.md`). The research came first (Fact 14).

## Research

Sources: Cloudflare's docs from their source repo (`cloudflare/cloudflare-docs`, `production`,
fetched 2026-11-22; the pages have moved to `workers/versions-and-deployments/`:
`index.mdx`, `version-skew.mdx`, `version-overrides.mdx`, `gradual-deployments/index.mdx`,
`rollbacks.mdx`), Cloudflare's API schema (`cloudflare/api-schemas` `openapi.json`, fetched
2026-11-22), wrangler's source at its pinned tag (`cloudflare/workers-sdk` `wrangler@4.154.0`:
`packages/wrangler/src/versions/deploy.ts`, `versions/api.ts`), runs 45059834265,
45065434026 and 45065970031 (their logs and the coordinator's account of their plans), and
Node 22.22's fetch, probed against a local server. `[I]` marks inference.

1. **How the kit deploys and reads back.** For each held Worker the kit uploads a version, then
   runs `wrangler versions deploy <new>@0 <before>@100 --yes` and `wrangler versions deploy
   <new>@100 --yes`; a first or whole deploy is `wrangler deploy`; a rollback's is the REST
   `POST .../deployments` (`Cloudflare.deploy`). After each, it read the Worker's active
   deployment once (`GET .../workers/scripts/{s}/deployments`, the first entry, "the latest
   deployment actively serving traffic", the schema's words) and stopped on any difference.
   Wrangler's `versions deploy` calls `createDeployment`, which answers the new deployment's
   id, then syncs the config's settings and prints `Deployed ...`; it never reads the listing
   back.
2. **What Cloudflare documents of consistency.** Only the edge: a deployment change "takes up
   to a few seconds to reach every location", and "a fixed wait or a check at one location
   does not prove that every location includes B" (`version-skew.mdx`,
   `version-overrides.mdx`). Nothing is said of the deployments listing's read-after-write.
   Run 45065434026's apply stopped with "tinyassets-gateway's deployment read back as
   df832b1e at 0% and 6688c8ed at 100%, not df832b1e at 100%", and the very next run's plan
   read the gateway serving df832b1e at 100%: the deployment had landed, and the one read
   came before the listing showed it. About 15 runs before had read each back at once.
   [I] The listing is eventually consistent for some interval after a deployment is made.
3. **The route record and its handback.** `routes` keeps `deploy:routes:<run id>` in the
   control namespace before its first change: the zone's routes as read, the handover's
   changes, the egress domain's Worker, the host's DNS record and the hook, as they stood. A
   re-run attempt kept its first attempt's record. Any other run wrote its own, of the state
   it found. A handback reads the record `--record` names, holds the zone to it, and puts
   back exactly what it says was there before. So after the stage's first `routes` run
   (45059834265: `AAAA stage.tinyassets.io`, the doors' eight routes, the egress custom
   domain, the hook enabled), runs 45065434026 and 45065970031 each wrote a record whose
   "before" was the stage already live: 13 routes with the doors' on the host, no changes,
   the egress domain the doors', the placeholder held, the hook enabled. A handback naming
   either changes nothing and leaves the stage up. The plan said to hand back with "--record
   <this run's id>", and added only a note when the doors held their routes already. The
   control namespace now holds those three records for the stage host.
4. **The canary's navigation.** The doors take `GET /authorize` only as a browser's top-level
   navigation (`contract/src/navigation.ts` `NAVIGATE`: `Sec-Fetch-Mode: navigate`,
   `Sec-Fetch-Dest: document`), and #5797's canary sends those headers through `fetch`.
   Node 22's fetch (undici) sets `Sec-Fetch-Mode` to the request's mode whatever the request
   gives. A local server receives `cors` and `document`. Of every header the canary or the kit
   sends (`Sec-Fetch-Mode`, `Sec-Fetch-Dest`, `Sec-Fetch-Site`, `Connection: close`, the
   version override, `Authorization`, `Accept`, `Content-Type`, `Origin`, `User-Agent`),
   only `Sec-Fetch-Mode` arrives changed. Fetch also adds `Accept: */*`,
   `Accept-Language: *`, `User-Agent: node` and `Accept-Encoding` where none is given, and
   `Sec-Fetch-Mode: cors` to every request. So the rerun 45065970031's smoke failed "the
   sign-in: ... /authorize answered 403". The canary's tests passed because their fake
   `world.fetch` kept every header. The doors' own tests build `Request`s, which keep it, and
   `doors/test/app.e2e.mjs` sends only `Sec-Fetch-Site`, which fetch keeps.

## The shape, checked against the intent

- **Read Cloudflare's state until it says so, never act twice.** One read-back
  (`readBack`) serves every deployment the kit makes or reads back: each stage of each
  Worker, a whole or first deploy, and each Worker a rollback takes back. It re-reads the
  listing until it shows exactly the wanted versions and shares in order, every 2 s for up to
  60 s (`READ_BACK_EVERY_MS`, `READ_BACK_MS`, beside it with the run that showed the lag).
  Past the bound it stops loudly, naming the last read-back and the bound. It never deploys
  again.
- **A handover has one record, the state before its first run.** No special case for
  "doors already routed": the record belongs to the host's handover, not to a run. While the
  control namespace holds a route record of the host (`hostRecords`, oldest first by `at`),
  every `routes` run keeps the oldest as it is and writes none: a re-run attempt its first
  attempt's, a later dispatch the record of the run that began the handover. Its plan names
  that id. A handback must name the oldest, and naming a later one stops it before any
  change, naming the oldest. Once it reads back as its record has it, it ends the handover:
  every record of the host is deleted, the later ones first and the one it names last, each
  copied into its job summary first. So the next `routes` records the host afresh.
  Considered and not taken:
  - composing records in the handback, which keeps the many-records shape and needs an order
    rule anyway;
  - keying the record by host, which needs a one-time copy of the live record to a new key;
  - a "handed back" mark in each record, which needs an optional field for the three live
    records written before it.
- **A request fetch cannot send goes by a client that sends it.** The canary's world gains
  `request` (`verbatim`, `node:http(s)`), which sends each header as given, with fetch's own
  defaults beside them where the request gives none. Cloudflare's checks in front of the doors
  therefore meet it as they meet the canary's fetches, all but `Accept-Encoding`, since it
  decodes no body. `send`, the canary's one bounded request, takes `request` for any request
  setting a header fetch overwrites (`OVERWRITTEN`, today `Sec-Fetch-Mode`, with its reason).
  No caller chooses. The smoke's override and its version hold wrap both clients. The fake
  world now behaves as Node does (its `fetch` overwrites `Sec-Fetch-Mode` with `cors`), and a
  test holds real fetch and `verbatim` against one local server: fetch overwrites, `verbatim`
  keeps, and the rest of the headers match.

## The problems found

- `workers` (the step's own `readBack`) and `rollbackAsk` each read a deployment back once,
  two copies of one compare and message, neither allowing for the listing's lag.
- `keepRecord` wrote a record of whatever state a run found, so a second `routes` run on a
  live handover recorded the handover as its own before-state. The plan told the founder to
  hand back "this run's id", with a note when the doors were routed already, which is a special
  case that described the trap instead of removing it.
- `hostRecord`'s rule that a bare record adopts a placeholder held only for a re-run attempt.
  The checklist told the founder to re-run, never dispatch anew, after a refused DNS make,
  because a new dispatch would record the placeholder as held.
- A handback left its record in place, so nothing told a later `routes` run that the
  handover had ended.
- The canary sent a navigation through `fetch`, which cannot send one. The canary's fake fetch
  kept headers that real fetch overwrites, so no test could see it.
- `alarm.ts`'s `World` and the canary's had the same shape. They now differ (the canary's
  gains `request`), and the canary's test types the alarm's hub with the alarm's own type.

## The fix

1. `deploy.ts` `readBack`, `READ_BACK_MS`, `READ_BACK_EVERY_MS`. `workers` (`landed`) and
   `rollbackAsk` both use it.
2. `deploy.ts` `hostRecords`, `heldLine`. `keepRecord` keeps the host's oldest record or
   writes this run's. `recordRead` holds `--record` to the oldest and plans the deletes
   (`ending`). `recordsEnded` (after `handedBack`) copies each record into the job summary,
   then deletes it. `hostRecord`'s and the record's words now say "the handover's".
3. `canary.ts` `verbatim`, `OVERWRITTEN`, `FETCH_DEFAULTS`, `World.request`. `send` picks the
   client, and `smoked` holds both clients.
4. Tests:
   - `deploy.test.ts`: the fake gains `lagging` (the listing answers the deployment before for
     N reads) and KV's bulk delete. A dark run, held and fresh, and a rollback read back
     through the lag with the same deployments and plan. A deployment that never lands stops
     after 60 s of re-reads, naming the last. A later run keeps the first run's record and
     writes none. The live stage's state, rebuilt (a first record and two later ones, plus
     another host's), names the oldest, refuses the later two, and its handback ends all three
     of the host's and leaves the other's.
   - `canary.test.ts`: the fake's fetch overwrites `Sec-Fetch-Mode`, and `verbatim` is checked
     against a local server.
5. `checklist.md`: the read-back's re-reads; one record a handover; handback names the
   first and ends the records; a new dispatch is fine after a refused DNS make. The workflow's
   header comment says which id a handback takes.

### Behaviour changed on purpose

- A deployment's read-back re-reads for up to a minute before it stops. Its stop message adds
  "60 s after it was made".
- A `routes` run on a host whose handover has a record keeps that record and writes none. Its
  plan line is "keep the route record deploy:routes:<id>, run <id>'s (attempt <n>, <at>), the
  state before the first routes run on <host>, as it is, and write none: hand back with
  --mode handback --record <id>". The "<DOORS> holds ... already" note is gone.
- A handback naming a record kept after the host's oldest stops before any change, naming
  the oldest. A handback that reads back deletes every route record of the host, so a second
  handback run stops with "there is no route record". A killed one is still run again.
- The canary's sign-in start (`GET /authorize`) goes by `node:http(s)`, with `Sec-Fetch-Mode:
  navigate` as sent. Every other request still goes by fetch.

## The live stage: which record a handback names

The control namespace holds three route records of `stage.tinyassets.io`: 45059834265 (the
first routes run, the state before the stage), 45065434026 and 45065970031 (each of the stage
live). Name **`--record 45059834265`**. No record needs a one-time step:

- the handback holds the zone to that record, puts back what it says, and reads back;
- it then deletes 45065970031, 45065434026 and 45059834265, in that order;
- a handback naming either later record stops before any change, naming 45059834265;
- a `routes` run before the handback keeps 45059834265 and writes none.

Its plan job (read-only) shows the record's lines and the three deletes before the apply job
is approved.

Run 45065970031's smoke failed with the doors' new version at 0% beside their version
before. The doors stay split until a `rollback --record 45065970031` (its rollout record) or a
new deploy. The handback does not touch the Workers.

## Left, each with its reason

- **The listing's lag is bounded at a minute,** an inference from one live sighting (gone by
  the next run's plan, minutes later), not a documented figure. Past it the run stops, naming
  the read-back, and the run's rollback or a re-run follows as for any stop.
- **The route record keeps no URL of the hook before.** A handback disables the hook and
  leaves it at the host's URL (the stage's, here). A disabled hook sends nothing, and
  `stage-end` moves the URL with `host`. This predates this PR and is not the race.
- **A `routes` run whose kept record names a DNS record it made, gone since,** plans "make the
  DNS record" and makes none (`hostRecord` writes only into a bare record). A re-run attempt
  could already reach this, which needs a hand deleting the stage's placeholder mid-handover.
  It predates this PR.
- **`FETCH_DEFAULTS` copies Node's fetch's defaults.** The verbatim test holds them to real
  fetch on the Node the tests run, which is the workflow's 22, so a Node that changes them
  fails that test.
- **`hostRecords` lists the namespace's route-record keys** each `routes` and handback run (a
  few small keys) and trusts KV as the route record always has. The namespace is writable by
  the doors, owner and sandbox, so a planted older record of the host would be the one named.
  The plan names its run and time, which the founder holds to the job summary, as before.

## Owed

- [ ] live: the next `routes` plan on the stage says "keep the route record
  deploy:routes:45059834265, run 45059834265's ..., and write none
  (deploy:routes:45065434026, deploy:routes:45065970031, kept while it stood, ...)"; its
  doors smoke at 0% passes the sign-in (`/authorize` redirects to WorkOS); each read-back
  line is "<Worker> serves ..., read back".
- [ ] live: the stage's handback, `--record 45059834265`, reads back and deletes the three
  records; the canary reads the platform dark.

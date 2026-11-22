# stage-canary as built (2026-11-22)

The stage's first `routes` (`platform-deploy.yml` run 45059834265, `stage.tinyassets.io`)
applied every change, then its canary (`--side doors --unkeyed`) failed two checks. This PR
fixes both, from the code and a read-only look at the stage.

## The problems found

- **The sign-in (exit 5).** `doors/src/authorize.ts` `authorize` answers a `GET` that is not a
  browser's top-level navigation (`navigation()`: `Sec-Fetch-Mode: navigate`,
  `Sec-Fetch-Dest: document`) with `403`. `deploy/canary.ts` `signIn` sent `firstPartyLink()`
  with neither header (#5703; never run against live doors before). Three copies of "what a
  navigation is" stood apart: the doors' predicate, the doors' tests' `NAVIGATE`
  (`doors/test/support.ts`), and the canary's request, which had none. `connect.ts` read the
  predicate from `authorize.ts`, a module it shares nothing else with.
- **Other browser-only paths.** Only `GET /authorize` and the connect callback require a
  navigation (`navigation()` has no other caller). The canary reaches `/authorize` alone;
  `/app`, the client document and the egress host take any request. The kit's
  `authorizeCheck` fetches WorkOS's authorize, not the doors', and it passed in the same run.
- **The egress host (exit 2).** `customDomain` runs `wrangler triggers deploy` and moves on.
  Wrangler's custom-domain publish (`workers-sdk` `deploy-helpers/src/triggers/publish-routes.ts`)
  PUTs `/workers/scripts/<name>/domains/records` and waits on nothing. The canary ran about
  two seconds after the attach (19:19:05 to 19:19:07Z) and found the host unreachable. The
  host now serves its own certificate (CN `tinyassets.io`, SAN `egress.tinyassets.io`, Google
  Trust Services WE1). Its validity runs to 2027-02-20T19:21:21Z, ninety days from about
  19:21Z, about two minutes after the attach. The stage host serves the zone's Universal
  wildcard instead. Cloudflare's Workers domain object names the certificate it issues
  (`cert_id`, "the TLS certificate issued for the domain", API reference) but carries no
  status. The certificate's status is a certificate pack's
  (`/zones/<zone>/ssl/certificate_packs`, SSL and Certificates Read). Community threads
  describe Workers custom domains' Advanced packs stuck in validation, read through that
  listing.
- **An unreachable line hid its cause.** `canary.ts` `send` printed Node's `fetch failed`
  alone. Its `cause` says whether DNS, the connection or TLS failed, and the line dropped it.

## The fix

1. **One definition of a navigation** (`contract/src/navigation.ts`): `NAVIGATE`, the headers
   a browser's top-level navigation sends, and `navigation(request)` built from them. The
   doors' `/authorize` and connect callback read the predicate; the canary's sign-in and the
   doors' tests send `NAVIGATE`. The doors' copies are deleted.
2. **The kit waits on Cloudflare's report of the certificate** (`deploy.ts` `customDomain`,
   `certified`; `cloudflare.ts` `domains` now returns `cert`, and `certificatePacks`). After
   the attach, and after the DNS record is written into the route record, the kit reads the
   doors' domain's `cert_id` and the status of the pack that is it or holds it. It reads them
   every 15 s until `active`, up to 10 minutes. Past that it stops, naming the last status
   read, before the hook or the canary. The plan job lists the packs too, so a token without
   SSL and Certificates Read stops the run before any change. The canary is not retried.
3. **The canary names a failed request's cause** (`send`): `fetch failed (getaddrinfo
   ENOTFOUND …)`.
4. **Docs.** `deploy/checklist.md`: the token's scopes (SSL and Certificates Read is owed) and
   the `routes` paragraph. `.claude/skills/infra-ops/references/cloudflare.md`: the kit's
   scopes. Both had said no run had written DNS with the token, which the stage's run did.

## Tests

- `deploy/guards/canary.test.ts`: the fake doors refuse a non-navigation `/authorize`
  through the contract's `navigation`, so every doors case proves the canary sends one. The
  unreachable case names its cause.
- `deploy/guards/deploy.test.ts`: the fake zone serves certificate packs whose egress status
  turns `active` at a given time. Three cases: the wait (nine reads 15 s apart, then the
  canary), the stop after ten minutes naming `pending_validation` (no hook, no canary), and a
  refused listing stopping the plan job (no change). `ROUTES_HELD` carries the new lines.
- Mutations, each caught: the canary sends no `NAVIGATE` (9 cases); the cause dropped (1);
  no wait (3); the wait taking any status (2); no plan-job listing (1); the shared predicate
  taking either header (`doors/test/hardening.test.ts`, 3).

## Left, and why

- **DNS propagation is not reported.** No Cloudflare API reports when a new record answers
  everywhere. The certificate is issued after the record, about two minutes on the stage, and
  the wait is expected to outlast propagation. If a run
  shows the certificate active and the canary still names a DNS cause, that is the next
  research.
- **The token's SSL and Certificates Read** is the founder's to add (dashboard, My Profile,
  API Tokens). Until then the plan job stops with nothing changed, naming the 403.

## Owed

- [ ] live: the stage's next `routes` run (after the token holds SSL and Certificates Read)
  shows the certificate line `active` and the canary's sign-in and egress checks passing.

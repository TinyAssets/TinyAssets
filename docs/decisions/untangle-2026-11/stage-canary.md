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
  two seconds after the attach (19:19:05 to 19:19:07Z) and found the host unreachable.
  - The host now serves its own certificate: CN `tinyassets.io`, SAN `egress.tinyassets.io`,
    Google Trust Services WE1. Its validity runs to 2027-02-20T19:21:21Z, ninety days from
    about 19:21Z, about two minutes after the attach.
  - The stage host serves the zone's Universal wildcard instead.
- **An unreachable line hid its cause.** `canary.ts` `send` printed Node's `fetch failed`
  alone. Its `cause` says whether DNS, the connection or TLS failed, and the line dropped it.

## Can the host's own answer be read reliably from the runner?

Yes, if the wait asks for what the canary checks. The run's line dropped the cause, so whether
DNS or TLS failed is not known, and an HTTPS answer alone is not the doors': before the Worker
serves the host, Cloudflare can answer with its own page (a `522`, a challenge). So the wait
takes only the egress door's refusal, the `404` naming its code (`egressRefusal`, which the
canary's egress check reads too), and anything else is a failure it names and waits past
(review, 2026-11-22). undici keeps an answered socket for the next request, so each attempt
sends `Connection: close`: a fresh connection, its name looked up again (Node caches no
lookup; the runner's resolver may, a cached miss ending in the loud stop and a rerun).

## Weighed and not built

- **Waiting on Cloudflare's certificate status.** The Workers domain names its certificate
  (`cert_id`) but carries no status. The status is a certificate pack's
  (`/zones/<zone>/ssl/certificate_packs`), which needs a token scope the token is not
  recorded to hold (SSL and Certificates Read): a founder ask before any `routes` could run.
  It would also not cover DNS propagation. Built first, then replaced at the coordinator's
  review (2026-11-22). Only `routes` runs `customDomain`, so `dark` was never at risk from
  it.
- **Retrying the canary.** It would hide a host that never answers.

## The fix

1. **One definition of a navigation** (`contract/src/navigation.ts`): `NAVIGATE`, the headers
   a browser's top-level navigation sends, and `navigation(request)` built from them. The
   doors' `/authorize` and connect callback read the predicate; the canary's sign-in and the
   doors' tests send `NAVIGATE`. The doors' copies are deleted.
2. **The kit waits on the egress host's own answer** (`deploy.ts` `customDomain`,
   `answering`).
   - After the attach, and after the DNS record is written into the route record, the kit
     asks `https://<egress host>/` with fetch's own certificate check.
   - Only the egress door's refusal is an answer (`egressRefusal`, shared with the canary).
     Each attempt is a fresh connection (`Connection: close`), its name resolved again.
   - A rerun on an attached domain: the attach keeps a domain already the same Worker's (as
     the test fake models; wrangler refuses only another Worker's), the record step is
     skipped (`domain.service` is the doors'), and the first ask answers.
   - It asks every 15 s, each request bounded at 30 s, for up to 10 minutes. Past that it
     stops, naming the last failure's cause, before the hook or the canary.
   - The plan job reads nothing new, and the canary is not retried.
3. **A failed request names its cause** (`deploy.ts` `fetchFailure`, which the canary's `send`
   uses too): `fetch failed (getaddrinfo ENOTFOUND …)`.
4. **Docs.** `deploy/checklist.md`: the `routes` paragraph names the wait. It and
   `.claude/skills/infra-ops/references/cloudflare.md` had said no run had written DNS with
   the token, which the stage's run did.

## Tests

- `deploy/guards/canary.test.ts`: the fake doors refuse a non-navigation `/authorize`
  through the contract's `navigation`, so every doors case proves the canary sends one. The
  unreachable case names its cause.
- `deploy/guards/deploy.test.ts`: the fake egress host fails its TLS until it is the doors'
  custom domain and a given time has passed, then Cloudflare's `522` for a given time, then the
  door's `404`. The cases:
  - The wait: asked only after the attach, through TLS failures and the `522`, nine asks 15 s
    apart, each with `Connection: close`, then the hook and the canary.
  - The stop: after ten minutes of TLS failures or of `522`, naming the cause, with no hook
    and no canary.
  - `egressRefusal` takes the door's refusal alone: not a `404` without a code, an HTML `404`
    or a `522`.
  - `ROUTES_HELD` carries the new lines.
- Mutations, each caught:
  - The canary sends no `NAVIGATE`: 8 cases.
  - `fetchFailure` drops the cause: 2 cases.
  - No wait: 3 cases.
  - The wait taking a TLS failure as an answer: 2 cases.
  - The shared predicate taking either header: 3 cases in `doors/test/hardening.test.ts`.
  - Review: the wait taking any status: 2 cases; no `Connection: close`: 1; `egressRefusal`
    ignoring the code: 1.

## Owed

- [ ] live: the stage's next `routes` run shows the egress door's refusal line, and the
  canary's sign-in and egress checks passing.

# kill-switch-cloudflare as built (2026-11-23)

`untangle/kill-switch-cloudflare` builds slice B1 of the deploy-safety research (founder
answer 14, `founder-answers.md`): the edge kill, two WAF custom rules on the zone that stop the
doors at Cloudflare's edge, beside the control record's `outside=off`, which works only while
the Workers and KV run. It takes the item `control-row.md` owed. The founder added Zone WAF
Edit on `tinyassets.io` to the deploy token (`CLOUDFLARE_API_TOKEN`) on 2026-11-23.

## Research first (Fact 14), read on main 480c5ce24

All of it read-only, from Cloudflare's docs (each page's Markdown, fetched 2026-11-23) and this
tree. No call reached Cloudflare's API.

### Cloudflare's Rulesets API for zone custom rules

- **Where they live.** A zone's custom rules are the rules of its entry point ruleset for the
  phase `http_request_firewall_custom`; a phase has at most one entry point per zone
  (https://developers.cloudflare.com/waf/custom-rules/create-api/,
  https://developers.cloudflare.com/ruleset-engine/about/phases/).
- **Reading.** `GET /zones/{zone_id}/rulesets` lists every phase's rulesets, each with its
  `kind` and `phase` and no rules; `GET /zones/{zone_id}/rulesets/{ruleset_id}` returns one
  with its rules (https://developers.cloudflare.com/ruleset-engine/rulesets-api/view/). The
  entry point also reads by phase (`.../rulesets/phases/{phase}/entrypoint`), which answers 404
  when there is none; the docs name no error code for that 404, so the kit lists instead: a
  listing that answered and holds no entry point is the one "none" (the kit's rule, never a
  failure read as absent).
- **Making.** With no entry point, `POST /zones/{zone_id}/rulesets` with `kind: "zone"`, the
  phase, a `name` and the `rules`
  (https://developers.cloudflare.com/ruleset-engine/rulesets-api/create/). With one,
  `POST /zones/{zone_id}/rulesets/{ruleset_id}/rules` adds one rule, last unless a `position`
  (`before: ""` is first) says otherwise
  (https://developers.cloudflare.com/ruleset-engine/rulesets-api/add-rule/). `PUT .../entrypoint`
  replaces every rule, so it would drop the founder's own; the kit never uses it
  (https://developers.cloudflare.com/ruleset-engine/basic-operations/add-rule-phase-rulesets/).
- **Turning on and off.** `PATCH /zones/{zone_id}/rulesets/{ruleset_id}/rules/{rule_id}`: "You
  must include all the rule fields that you want to be part of the new rule definition, even
  if you are not changing their values"; it answers the whole ruleset
  (https://developers.cloudflare.com/ruleset-engine/rulesets-api/update-rule/). So each write
  sends the rule whole, and a flip and a mend are one call.
- **A rule's fields.** `ref` is "a user-defined external identifier that must be unique for
  each rule in a ruleset" (the rule's id by default), `enabled` a boolean, `description` up to
  4,096 bytes, `expression`, `action` (https://developers.cloudflare.com/ruleset-engine/rulesets-api/json-object/;
  the API reference https://developers.cloudflare.com/api/resources/rulesets/subresources/rules/methods/create/
  lists `ref` and `enabled` as optional on create). The kit finds its rules by `ref`.
- **Scopes.** Listing and reading take Zone WAF Read or Write; making, adding and updating take
  Zone WAF Write (each page's "Required API token permissions";
  https://developers.cloudflare.com/fundamentals/api/reference/permissions/ names the token
  scope Zone WAF Edit/Write).
- **The plan's limits.** Free: 5 custom rules, `matches` (regex) only on Business and above, a
  custom block response only on Pro and above, the default 403 otherwise
  (https://developers.cloudflare.com/waf/custom-rules/,
  https://developers.cloudflare.com/ruleset-engine/rules-language/operators/,
  https://developers.cloudflare.com/waf/custom-rules/create-dashboard/). No custom body: Pro
  would be new spending (Fact 4).
- **The expression's parts.** `starts_with(field, "...")` and `lower(...)` carry no plan limit
  (https://developers.cloudflare.com/ruleset-engine/rules-language/functions/).
  `http.request.uri.path` is the path without the query
  (https://developers.cloudflare.com/ruleset-engine/rules-language/fields/reference/http.request.uri.path/).
  `http.host` is "the `Host` header from the original client request"
  (https://developers.cloudflare.com/ruleset-engine/rules-language/fields/reference/http.host/),
  so the kit compares it in lower case: the doors take a host in any case (`new URL`).
  URL normalization runs before both WAF custom rules and Workers
  (https://developers.cloudflare.com/rules/normalization/), so a rule and a route see one path.
- **Order.** Custom rules run in the request phases before Workers see a request
  (https://developers.cloudflare.com/ruleset-engine/reference/phases-list/); a Worker on a
  custom domain "is treated as an origin"
  (https://developers.cloudflare.com/workers/configuration/routing/custom-domains/). That a
  rule blocks a route's Worker before it runs is inference, checked live (owed below). Custom
  rules run in order, and a Block stops the rest (https://developers.cloudflare.com/waf/custom-rules/),
  so the kit adds its rules first.
- **Same-zone subrequests skip custom rules**
  (https://developers.cloudflare.com/waf/feature-interoperability/). No Worker of ours fetches
  its own zone's hosts: callers reach the doors from outside, sandboxes reach the egress host
  from E2B.

### How the kit calls Cloudflare

- `deploy/cloudflare.ts` is the one client: every call checked by `#ok` (status 200 and
  `success`), every listing read to its last page (`#rows`), a failure never read as absent.
  The fakes are `deploy/guards/deploy.test.ts`'s `world`: one function answering each path, the
  account's state changing as the kit changes it, each call an event.
- A mode is a row of steps (`MODES`); each step says its plan lines before its change, and the
  apply job is held to the plan job's lines (`holding`). Reads that stop a deploy sit in
  `READS`, before any change.

### What the kill blocks, and what it must not

- **The platform's paths.** `contract/identity.json` `routes` on its `host` (the stage's today,
  the zone at the cutover) and `contract/egress.json`'s host. Rendering from `host` keeps the
  rules off the old stack: before the cutover the host is the stage's, which the old stack
  never serves.
- **Outside, not the owner.** The control record's `outside` gate closes `/mcp` and `/api`, its
  `inbound` gate `/hooks/inbound/` (`doors/src/doors.ts`). The research's list also blocked
  `/authorize`, `/oauth/*` and the OAuth metadata, but those carry the owner's own sign-in and
  the first-party app's token refresh: blocking them signs the owner out of `/app` within a
  token's life. `/hooks/e2b/` is E2B's lifecycle hook, which ends the sandboxes' billing. So
  `outside` blocks the three outside doors and leaves the rest up; `all` blocks every route
  and the egress host.
- **What the kit itself needs.** Its own paths never cross the zone: the Cloudflare API, the
  operator channel (Queues and R2 over the API) and KV over the API. Its deploy's smoke and the
  canary do reach the doors, so a deploy stops before any change while a rule is on, rather
  than failing at the smoke with a version split at 0%.
- **The old stack's serving.** On the zone before the cutover, the doors' paths are the old
  proxy's. Mode `edge` turns a rule on only while the doors hold every route of theirs on
  their host (`routedNow`, the same read `dark` makes); turning both off is always allowed.

### How it is turned on, and where it is made

- **On:** mode `edge` (`--kill outside|all|none`, one workflow input), or the rule's switch on
  the zone's Security rules page, which needs neither GitHub nor a host (Direction 8).
- **Made:** by `dark` and `routes` (`routes` runs every step of `dark`), off, beside the control
  record, so the rules exist from the go-ahead deploy on and the cutover needs no step of its
  own. Mode `edge` makes one the zone lacks too, so an emergency never waits on a deploy.

### How the rules are held to the tree

- One source: `facts.ts` `edgeRules` renders both from `identity.json` and `egress.json`, split
  by `platform.json` `edge_kill`, which names every route once, in `outside` or `left`, and
  throws otherwise, so a route added to the identity names which side it is, as `handed.json`
  makes each shape name what it hands (#5808).
- The live rules are held to it on every run: a deploy sets one that differs back to the
  tree's (the cutover's change of host among them) and reads it back; mode `edge` writes each
  rule whole and reads both back.
- The tests hold the wire: the expressions the zone's host renders, literally.

## The untangle list

- Fix: one rendering of the edge kill from the identity's routes, with the outside split as
  data beside the kit's other facts.
  - `deploy/checklist.md` named the outside paths by hand, a list beside `identity.json`'s.
  - The research's B1 stopped a deploy on a drifted expression, which the cutover's change of
    host would cause by design: a deploy mends it instead, off, said in its plan.
- Fix: the deploy harness's `CHANGE` pattern counted no `PATCH` to Cloudflare as a change; the
  rules' updates are the first, so it does now.
- Fix: a deploy reads the rules again where it keeps them off (`edgeOff` in `edgeKept`), never
  a copy from its first read, so a rule turned on during a long deploy stops it there and is
  never turned off.
- Left, a decided keep: `edge_kill.outside` restates which paths the doors' router
  (`doors/src/doors.ts`) sends to the outside gates. Holding one to the other would need the
  doors to export a path table for the kit; the split's reason is written beside it, and the
  router's paths are `identity.json`'s.
- Left, a decided keep: the rule that a kill turns on only while the doors are routed is a
  requirement (the old stack serves those paths on the zone), not a special case.
- Fix (review): `edgeKept` was after the Workers, so a zone at its plan's count of custom
  rules refused the first rule the kit lacked after every storage, secret and Worker change.
  It is the run's first Cloudflare change now (after WorkOS's, which precedes them all); the
  kit reads no plan limit, so the stop is Cloudflare's refusal, at most one rule made, off,
  before it.
- Special cases added: none beyond those; `enabled` absent reads as on, as Cloudflare runs it.

## Behaviour changed on purpose

- `dark` and `routes` now read the zone's custom rules before any change, and stop while an
  edge kill rule is on. They make the two rules off when the zone lacks them, and need Zone
  WAF Edit; without it their plan job stops at the listing with nothing changed.
- The workflow takes mode `edge` and its input `kill`.

## Left, each with its reason

- A rule the founder places before ours with Skip would skip it. The kit adds its rules
  first; it does not move them back if he reorders.
- A rule he made by hand from the old checklist line, under another ref, sits beside the
  kit's; the kit never touches a rule that is not its own.
- A blocked caller sees Cloudflare's 403 page, not JSON: a body of our own is Pro's.
- An open connection is not cut by a Block.

## Owed

- [ ] live: on the stage host, after its `routes`, mode `edge` with `kill` `outside` makes
  `/mcp` answer Cloudflare's 403 while `/app` serves, the dashboard's switch shows it on, and
  `kill` `none` brings `/mcp` back; the first `dark` with Zone WAF Edit makes both rules off.

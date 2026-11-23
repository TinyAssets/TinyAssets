# handed-deploy as built (2026-11-23)

`process/handed-deploy` takes the two items `binding-compat.md` owed (#5808): the kit's plan
holds the run against the versions the stage serves, not only the base branch; and every
other strict cross-Worker parser is data under the same rule, which runs both ways.

## Research first (Fact 14), read on main ecaccf90c

- **What the plan knows of the serving versions.** `rollout` reads each Worker's one serving
  version (`Cloudflare.serving`, a split stops it) and that version's tag, which the kit writes
  as the commit's first 12 digits (`versionFlags`, read back by `taggedAs`). Every platform
  deploy since the kit is tagged: the stage's six Workers serve ecaccf90c's versions (run
  45110158056, plan, pair and apply all green). A Worker the account lacks is `before: null`:
  no version of it serves, so none of it talks.
- **How the jobs check out code.** `platform-deploy.yml`'s plan and apply jobs ran
  `actions/checkout@v4` bare: one commit, no history. The CI legs already read earlier commits
  with `fetch-depth: 0` and `filter: blob:none`, each blob fetched as `git show` reads it
  (`ci/history.ts` `fileAt`, which fails loudly on a commit the checkout lacks, never "no
  file"); the job's token is `contents: read`. The GitHub contents API would need the token in
  the plan job, which holds only the Cloudflare token: git is the one way, read-only.
- **A version a secret change makes** (review). `retire` sets a keyring with `wrangler secret
  put`, and a secret edited in the dashboard does the same: Cloudflare makes a version and
  deploys it at once. Wrangler 4.154.0's source (its published package, read in a scratch
  folder): `secret put` sends only the secret (`PUT .../scripts/<name>/secrets`), no
  annotations, and the API refuses it unless the latest version is deployed
  (`throwIfLatestVersionNotDeployed`); `versions secret put` instead PATCHes the latest version
  with its own `--tag` and `--message` and deploys nothing. Wrangler's version listing reads
  `workers/triggered_by` (`secret` shown as "Secret Change"), so Cloudflare notes how a version
  was made. Whether it copies the tag onto a secret change is not in wrangler's source, and the
  deploy logs show no such version: every run's serving versions since run 45038596195 are the
  versions the run before deployed, each tagged. So the kit cannot count on a tag there.
- **The apply job makes the plan again before any change** (`approved`), so a stop in
  `rollout`'s plan stops the apply job before its namespaces, secrets or deploys too.
- **The strict cross-Worker parsers, by the deploy order** (codemode, files, gateway, sandbox,
  owner, doors). A parser is strict where it refuses a key it does not list or requires one.
  - Requests, the parser deploying first: the owner's from the doors (the binding's 13 shapes,
    already data; the loose doors' `egressRequest`, `reserveRequest`, `settleRequest`, the four
    connection doors' `fields`, `billedEvent`, the admission's admit and change, `tierName`);
    the gateway's from the doors (`doorbellInput`, `authorizationGrant`) and from the owner
    (`ringRequest`, and `metering`, the owner's reserve answer the doors pass on).
  - Answers, the sender deploying first, parsed by its caller's version before: the gateway's
    to the owner (`spendOutcome`'s two kinds, through the doors; `#keepResealed`); the
    sandbox's to the owner (`started`, `networkOutcome`, `meterRead` and its events); the
    owner's refusal facts to the doors (`refused.ts`, `subscription_limit`).
  - A copy: `ledger.ts` `outsideOrigin` and `registrant` re-check the binding's origin and
    source with their own literal lists.
  - Not the deploy order's: what an outside client builds and the doors pass on (the app's
    inbound, grant and policy bodies, MCP tool arguments: the tool table's and the clients');
    the runner's posts (`reports.ts`: its frames, from a sandbox that outlives deploys, the
    runner protocol's); stored shapes (sealed blobs' plaintexts, the source record inside them,
    KV values, R2 roots, queue messages, which wait across versions both ways: `forward`'s);
    and value checks that list no keys (the sandbox's start and sign-in checks).

## The design

- **One file of shapes, each with its two Workers** (`contract/handed.json`, renamed from
  `bindings.json`): `from` builds it, `to` parses it; `required`, `optional`, `dropped` as
  #5808 had them, and `always` (handed every time, taken as if optional), the step a field
  takes between optional and required in either direction, without which a request field
  could never become required and an answer field never go. Every parser reads its lists
  there (`taken`, `keyed(value, ...taken(name))`); `takes` is the one statement of what a
  parser takes, which the hold reads too.
- **One hold, both ways** (`deploy/handed.ts` `handedProblems`): for each shape, the later of
  its two Workers is the one whose version before talks to the earlier one's new version.
  Where the parser deploys first, it takes every field the sender's version before hands and
  requires none it does not always hand, and a shape that version hands may not go (but for
  the cutover's). Where the sender deploys first, the parser's version before takes every
  field it hands and requires none it does not always hand; a shape the parser's version
  before does not list is a new answer, which no caller before it reads. The door table is
  the doors' requests to the owner, held as #5808 held it.
- **Two callers of the one hold**: the guard (`deploy/guards/handed.test.ts`), the base branch
  as every Worker's version before; and the kit's plan (`deploy.ts` `servedHold`, in `rollout`,
  before the rollout record), each Worker's serving commit by its tag, read from git
  (`Io.fileAt`, `ci/history.ts` `fileAt`). A serving version whose tag names no commit, or a
  commit the checkout lacks, stops the run: what it hands is unknown. No version serving is the
  one "no constraint". The plan's line names each commit read, so the apply job's plan is held
  to it too.
- **A secret change's version is read as the version it was made from** (`codeOf`): a serving
  version whose tag names no commit, which Cloudflare notes as `triggered_by: secret`, runs the
  code of the version its deployments listing served alone at 100% just before it first did
  (a secret change is made from the latest version, refused unless it is deployed, and deploys at
  once), and so on back to a tagged one. Anything else untagged stops the run, naming how out: the
  commit it should run uploaded as a new version tagged as the kit tags (`versions upload`
  keeps the latest version's secrets) and deployed at 100%; an older version deployed back is
  refused once a secret has changed since (10220, `deploy-rollout.md`), so that is no way out
  after a secret change in a split (found in review). Not `versions secret put` in `retire`: it
  patches the latest version, not the serving one, and still leaves a hand edit in the dashboard
  a dead end.
- **The shapes file at an earlier commit** is `handed.json`, or at a commit before it
  `bindings.json`'s shapes, each the doors' to the owner, as its `about` said: the stage serves
  ecaccf90c, which has only that. A commit with neither throws.

## The problems, with their fixes

- **The plan holds the run to what the serving versions hand** (`servedHold`, the jobs'
  history).
  - The guard compared the PR's base with its head, the stage serving an older commit, so a
    contract step (a `dropped` entry taken out) passed whether or not the serving doors still
    sent it, and its wait was a decision log's.
- **Every strict cross-Worker field list is data, held both ways** (`handed.json`, `taken`).
  - 25 literal key lists (24 shapes) in the loose doors' modules, the gateway's inputs and the answers'
    parsers, which no guard could read at an earlier commit.
  - The rule ran one way only (the owner from the doors); an answer's parser deploys after its
    sender, so its rule runs the other way.
  - No step let a field become required, or an answer field go, under the rule (`always`).
- **The one rule in one place** (`deploy/handed.ts`, `takes`): the guard held its comparison,
  the plan needed it, and what a parser takes was written in the parser and again in the guard.
- **A copy of the binding's lists** (`ledger.ts`'s origin and source) reads the binding's
  shapes.
- **The guard's dead branch** (a base before the shapes) goes: main holds them.

## Behaviour changed on purpose

- A deploy plan stops before any change when the run's Workers refuse what a serving version
  hands or a serving version refuses what they hand, naming each shape and field; or when a
  serving version's code is unknown (untagged, and no secret change of a tagged version) or its
  commit is not in this checkout.
- `tierName` holds its request to a plain object, as every other parser does (`keyed`).

## Left, each with its reason

- **An answer shape new to the tree** is unheld against a parser's version before that lists
  none: it is a new method's answer (which no caller before reads) or one from before the
  shapes were data; a new kind of an existing answer is a new shape, and the same.
- **What clients build** (the app's bodies, MCP arguments), **the runner's posts** and **stored
  shapes** are not the deploy order's: the tool table's, the runner protocol's, `forward`'s.
- **Types are not held to the data**, as in #5808: a JSON module's strings widen. A sender
  whose literal gains a field the data lacks is refused by its parser's own tests.
- **The rollout's other rules still read a secret change's version by its own tag**
  (`atOnce`, `forwardOnly`, the rollout record's `before`): untagged, it goes to 100% at once
  and a rollback stops at it, as before this PR; the record's tag is held to Cloudflare's own,
  so `codeOf` resolves for the hold alone.
- **The sandbox's input checks** list no keys (an unknown key is ignored); a newly required
  field there is the same skew, with no list to hold.

## Owed

- [x] Once the stage serves a commit with `handed.json` (this PR's or later), `sideAt`'s
  reading of a commit's `bindings.json` goes (live: the stage serving this PR's commit). Done
  (`process/handed-fallback`, `handed-fallback.md`): run 45119864429 deployed d315aaa61.

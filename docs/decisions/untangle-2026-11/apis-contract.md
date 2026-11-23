# apis-contract: every source is sealed and handed in the `apis` spelling (2026-11-23)

`untangle/apis-contract`, owed by `openrouter-messages.md` (#5837): the contract half of its
expand-then-contract (its design step 5). Directions 2 and 5: one spelling of a sealed
upstream, its scope and its models, and no band-aid kept for a release no longer served.

## Research (Fact 14), before the build

### What the expand kept, and for whom

#5837 moved an upstream from one `api` and `base_url` to `apis: [{ api, base_url }]`, and the
scope from one `api`, `base_url`, `meter` to `apis: [{ api, base_url, meter }]`. For the
releases before it, it kept:

| kept | where | for |
| --- | --- | --- |
| a one-API source sealed in the old spelling (`sealedForm`, through `sealable`, on every seal, reseal and kid-retiring open) | `gateway/src/credential.ts`, `keyring.ts` | the gateway before #5837, on a rollback past it, opening what this one sealed |
| a one-API setup check's answer handed in the old spelling (`handed`: `OneApiScope`, its models naming no API) | `gateway/src/forward.ts`, `contract/src/gateway.ts` `Verified` | the owner before #5837, which the new gateway answers while the rollout is between them |
| a seal input in the old spelling, read as one row | `contract/src/egress-scope.ts` `modelUpstream` (`source`, `keySource`, `keyExchangeSource`) | the doors before #5837, which hand the source record's values |
| a blob in the old spelling, read as one row | the same `modelUpstream`, through `upstream` (`credential`) | every blob sealed before #5837, and every one-API blob sealed since (`sealedForm`) |
| a scope in the old spelling, read as one row | `egressScope` | the owner's stored scopes (`connections.egress`), and a handed one from the gateway before |
| a model record naming no API, read as its scope's one API | `ModelRecord.api` optional, `modelApi` | the owner's stored commit records (`connections.models`) and a declared endpoint's own |

### What the stage serves, and what a rollback can reach

- The stage serves main 8c80b02c8 on all six Workers (deploy run 45281806227, the
  coordinator's report), which holds #5837, #5844 and later. No Worker serves a release
  that writes, hands or needs the old spelling, so the writers' reason is gone.
- The deploy kit's `rollback` takes a run's Workers back to the versions they served just
  before that run, and refuses once anything was deployed since (`deploy.ts`, "deployed since
  run"). So this PR's run rolls back to 8c80b02c8, whose gateway opens the `apis` spelling and
  whose owner takes it (`modelUpstream`, `egressScope`, `checkedRecord` with `api`), and no
  kit rollback reaches a release before #5837 once this one deploys.
- During the rollout (gateway, sandbox, owner, doors) this gateway answers the owner
  8c80b02c8, which takes the `apis` spelling and models naming their API: #5837's own
  several-API answer already reaches it so (`forward.test.ts`, "hands a source of several
  APIs"). A rollback takes the owner back first, so this release's owner never meets the gateway
  before it.
- The doors 8c80b02c8 hand only the `apis` spelling: a source record's keys are
  `sources/src/check.ts` `GATEWAY_KEYS` (`provider`, `apis`, ...), every record moved to them
  in #5837, and the doors hand `sealedSource`'s check of a record, the gateway's own `source`.
  Nothing else seals: the doors' `connect.ts` and `oauth.ts` are the two `seal` and `exchange`
  callers, and a service's key has no API.

### The deploy hold

- `contract/handed.json` lists no inner shape of any of these: `connection_commit` holds
  `models` and `egress` as fields, their insides checked in code (`checkedRecord`,
  `egressScope`), and no shape lists the seal input (the doors hand it to `seal` and
  `exchange` as `record`) or the blob's plaintext. `contract/doors.json` lists none either.
  So `deploy/guards/handed.test.ts` holds none of this, and no `dropped`, `droppedTypes` or
  `handed: false` entry is owed: the step changes what is written, not a field any listed
  shape hands.
- What held the old spelling instead is the keyring guard (`gateway/guards/keyring-main.test.ts`),
  whose "today's blobs open on main's code" opens a blob sealed now with the credential grammar
  of `ci/keyring-vectors.json`'s commit (e115a123), from before the envelope, and so before
  #5837. That guard is there for the envelope (the scheme, the ring, the binding, the
  refusals); the plaintext's grammar of a commit no Worker serves is not its to hold. It
  keeps holding that main's code opens today's envelope, and today's code reads what it
  opened.

### What cannot go yet, and why

- **Blobs in the old spelling** are every stage connection sealed before #5837 and every
  one-API one sealed since. Only a reseal rewrites a blob (a refresh, an endpoint change, or a
  kid-retiring sweep), and today's reseal writes the old spelling (`sealable`). Once this PR
  deploys, a reseal writes the `apis` spelling, so one keyring rotation and its sweep leaves no
  blob in the old spelling: a live act, owed below.
- **Stored scopes in the old spelling** are every connection's `connections.egress` committed
  before this deploys. Nothing rewrites a committed scope (`listing-records.md`: the scope and
  blob are never rewritten by a read), and the owner's export carries it (`export.ts`,
  `connections` `egress`), so a restore of an older bundle brings it back. Dropping
  `egressScope`'s old reader needs a migration that rewrites the stored scopes and a restore
  that rewrites (or refuses) an older bundle's: owed below.
- **Model records naming no API**: the lane's `models` read replaces a connection's records
  with the gateway's, each naming its API, at its first pass (#5844), but the commit's records
  (`connections.models`) are kept and read until that first read, and exported. A declared
  endpoint's own records are the owner's, sealed without `api`. So `ModelRecord.api`'s absence
  stays with the scope's reader.

## Design

1. **The gateway seals every source as it is.** `sealedForm` and `sealable` go; `seal`,
   `reseal` and `open` check a credential with `credential` alone, so every seal, reseal and
   kid-retiring open from this deploy writes the `apis` spelling.
2. **The gateway hands every setup check as it is.** `handed` goes; `verify` answers
   `modelInfos` (each naming its API) and the learned scope. `OneApiScope` goes from the
   contract, and `Verified.egress` is an `EgressScope`.
3. **The seal input is read in the `apis` spelling alone; the old one is the blob opener's.**
   `modelUpstream` (`source`, `keySource`, `keyExchangeSource`) reads `apis` only, and
   `upstream`, which opens a blob, reads a blob of the old spelling as its one row: the one
   place left that a blob sealed before is read, dropped with `egressScope`'s old reader.
4. **The keyring guard holds the envelope, not an old grammar.** Today's blobs open on main's
   keyring (`openValue`), and the value is today's credential.
5. **The gateway spec** says `verify` answers every source in the one spelling.

## Problems found (untangle step 2)

- `gateway/src/credential.ts` `sealedForm`, `sealable`: a writer for a gateway no Worker
  serves and no kit rollback reaches. Removed.
- `gateway/src/forward.ts` `handed`: a writer for an owner no Worker serves. Removed, with
  `contract/src/egress-scope.ts` `OneApiScope` and `contract/src/gateway.ts`'s union.
- `contract/src/egress-scope.ts` `modelUpstream`: the seal input took the old spelling for
  doors no Worker serves, through the same parse that opens old blobs, so the input's
  tolerance lived as long as the blobs'. Fixed: the old spelling is read where a blob is
  opened (`upstream`) only.
- `gateway/guards/keyring-main.test.ts`: held today's plaintext to a pre-#5837 grammar, so
  it would have refused the contract. Fixed: it holds the envelope.
- Tests that sealed in the old spelling (`credential.test.ts`'s declared endpoint,
  `entry.test.ts`'s Vercel source) and the one that held `handed`'s answer
  (`forward.test.ts`): now the `apis` spelling, and the seal input's old spelling is
  refused.
- `openspec` gateway spec's `verify` requirement named the one-API spelling. Updated.

## Left, with reasons

- **The old spelling's two readers** (`upstream`'s blob, `egressScope`'s stored scope) and
  `ModelRecord.api`'s absence, for the reasons above, owed below.
- **A rollback by hand past #5837** (the dashboard's Deployments) would put a gateway that
  cannot open an `apis`-spelled blob before every blob sealed or resealed since; the
  checklist's rules for one (`deploy/checklist.md`) hold migration counts and secrets, not a
  plaintext's grammar. It fails loudly (`credential_refused` on every such open), and the fix
  is to roll forward. That was already so for #5837's several-API blobs.

## Owed

- [ ] live: once this deploys, rotate the gateway keyring and let the owner's sweep reseal
  every connection's blob, so none is left in the old spelling (the sweep's receipt says
  how many it resealed and that none failed).
- [ ] untangle/apis-readers: once that sweep is done, an owner migration rewrites every stored
  scope and commit model record in the `apis` spelling (each record naming its API, a
  declared endpoint's its one API), a restore does the same to an older bundle, and the old
  spelling's readers (`upstream`'s blob, `egressScope`) and `ModelRecord.api`'s absence
  (`modelApi`'s one-API fallback) go.

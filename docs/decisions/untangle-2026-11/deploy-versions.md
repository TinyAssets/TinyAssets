# deploy-versions as built (2026-11-21)

`untangle/deploy-versions` builds slice A1 of the deploy-safety research (§1.3): the kit's part
of gradual deploys that changes nothing at 100%. The rollout itself is A2.

## The problems found

- `deploy/deploy.ts` `setSecrets` set each secret with `wrangler secret put`, before the code.
  Cloudflare: "`wrangler secret put` creates a new version of the Worker and deploys it
  immediately" (`workers/configuration/secrets`). So every secret a run set was one more 100%
  deploy of the code already live, and under a gradual rollout it would skip the split.
- Nothing named a version: no tag or message on an upload, no `version_metadata` binding, and
  no answer said which version served it. A rollback could not tell a version that migrated
  an object's schema from one that did not.
- `lockRefusal` ran the locks' command line, which prints each lock's digests, and kept only
  whether it refused: the counts a version's tag needs were read and dropped.

## The fix

1. **Secrets ride the version** (`workers`, `secretsFile`). The secrets step only plans what
   is set (`setting`, now only what the run sets: an optional secret it is not given is out of
   it). Each Worker's upload, `wrangler versions upload`, or a first or `--migrate` deploy,
   `wrangler deploy`, takes `--secrets-file` with exactly that Worker's names from `setting`,
   as JSON (wrangler expands `$VAR` in a dotenv file), mode 0600, in the run's scratch, which
   `perform` removes at the run's end, a throw's included. A Worker the plan sets nothing on
   gets no flag; a secret the file omits is kept from the version before (Cloudflare's docs).
   The `secret put` call in the deploy rows is deleted.
2. **Each version is tagged** (`versionFlags`): `--tag` is the commit's first 12 digits and, on
   a Worker with migration locks, `-m` and each lock's count in its path's order (`-m3`, the
   sandbox's `-m2.1`); `--message` is the whole commit. The counts are the locks' own, from the
   command line `locks` already runs (`lockCounts`, which replaces `lockRefusal` and throws its
   refusal), carried as `migrations`. The commit is the checkout's (`Io.commit`,
   `git rev-parse HEAD`), as the tree's image is `Io.treeImage`.
3. **The doors name the serving version.** `doors/wrangler.jsonc` binds `CF_VERSION_METADATA`,
   and the doors' entry sets `x-tinyassets-version` to its id on every answer (`versioned`; the
   socket's 101 included, its WebSocket carried). The test kit (`ci/workers.ts`) carries the
   binding as `wrangler dev` does: a fresh id, no tag.

## Departures from the research

- **The binding is in the doors' config alone**, not all six. The research's only reader is the
  doors' header (A2's 0% smoke, §1.2 step 3); A2 and B2 read the other Workers' versions from
  the deployments API and analytics. A binding no code reads is surplus by the untangle rule;
  a Worker gains it with the first code that reads it.
- **Flags rechecked** on wrangler 4.154.0's `--help` and Cloudflare's docs (`cloudflare-docs`
  at `a1ac58e`, `workers/configuration/secrets.mdx`, `workers/wrangler/commands/workers.mdx`):
  `--secrets-file`, `--tag` and `--message` exist on both `versions upload` and `deploy`. The
  research did not note that wrangler expands variables in a dotenv secrets file and lets
  process environment values override the file's; the file is JSON, and wrangler's environment
  holds the same inputs the file is written from.

## Behaviour changed on purpose

- A run that sets secrets makes no deploy of the old code: each secret goes live with its
  Worker's new version.
- A rotated shared secret (`ROUTING_MAC_KEY`) reaches the owner with its version and the doors
  with theirs, the next Worker in the order; before, the two `secret put`s did it, both before
  any code.
- Plan lines read `set the secret <name> on <Worker> with its version`.
- Every doors answer carries `x-tinyassets-version`.

## Left, each with its reason

- Mode `retire` (`retireAsk`) and the checklist's `IMPORT_KEY` step still use
  `wrangler secret put`: each sets one secret on the code already live and deploys nothing new,
  which at 100% is the same code. How they behave during a split, and whether they become
  `versions secret put` plus a deploy, is the rollout's (A2) to settle.
- No documented length limit for a tag or a message was found in the docs tree; the tag stays
  short (at most 12 digits, `-m` and the counts).

## Owed

- [ ] A2, the rollout: the rollout record, the stages, the soak and health check, the reverse
  rollback and the forward-only refusal that reads each version's `-m` counts, mode `rollback`,
  the doors' 0% smoke reading `x-tinyassets-version`; and mode `retire`'s and the import
  window's `secret put` during a split (`untangle/deploy-rollout`).
- [ ] live: the next `dark` run shows each Worker's version tagged with the commit and its
  locks' counts, its message the commit, a secret set by it present after it, and a doors
  answer carrying `x-tinyassets-version` equal to the deployed version's id.

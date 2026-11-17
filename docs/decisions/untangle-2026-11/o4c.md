# O4c as built (2026-11-17)

Lane O's owed line X1, B10's literal guard over every folder (`o4b.md` item 6), and B22's sweep doorbell half (O5; d3 §I).

## The plan (research, before building)

**What the guard finds.** B10's rule is "no `tinyassets-*` storage, queue or Worker literal in any file outside the configs and `deploy/platform.json`". A pattern over `tinyassets-*` alone finds names that are none of the three: the export format, HKDF labels (`tinyassets-api-key-client`, `tinyassets-mcp-family`), the kit's record kinds (`tinyassets-routes`, `tinyassets-rotation`), the old proxy's Worker, temp folders. Each would need an exception of its own.
- **Chosen:** the guard's names are the facts' own: `deploy/facts.ts` `NAMES` (each Worker's script), `RETIRED`, `BUCKETS`, `QUEUES` (dead-letter queues included) and the KV `NAMESPACES`' titles, which it reads from the six configs and `platform.json`. Nothing restates them. A name counts where no letter, digit, `-` or `_` touches it, so `tinyassets-files-prod` is not `tinyassets-files`, and the E2B template `tinyassets-sandbox:s…` is `tinyassets-sandbox`.
- **Where:** every text file git holds under `platform/` and `.github/`, comments and prose included: a comment or a checklist line that spells a live name is left behind by a rename just as code is, and it can point at the config instead. The records and specs (`docs/`, `openspec/`) are not read: a decision log is edited only to correct a line, and B26's check holds the specs' names to the tree. The tree-wide reader is `cutover.test.ts`'s `scopeFiles` today; it moves to the kit (`ci/kit/sources.ts` `treeFiles`, with its scope `TREE`), and both guards read it.
- **Its home:** `deploy/guards/literals.test.ts`, A3's facts guard extended as B10 says. The ci kit may not import the deploy kit (the layer table), and a workspace's guards may.

**How it states a permanent exception.** The options:
- *A fix value other than `B<n>` in `OWED`.* `ratchet` refuses it, and rightly: `OWED` is a debt that only shrinks, which a permanent entry is not.
- *Each workspace's own list in its `rules.ts`, as `MODULE_STATE` is.* Ten files each stating reasons of its own: the scattered allow-list the review rule refuses, and the same reason (a fixture, a generated file) said several times.
- *One list in the guard itself, each entry a finding and its reason.* **Chosen:** `KEPT` in `literals.test.ts`. Each entry is the finding as the guard names it (`<path>: <name> ×<count>`, the kernel guard's form), built by `kept(reason, path, counts)`, so a reason is written once for all of a file's names. It is held as exactly as `OWED`: a finding neither kept nor owed fails, a kept entry that no longer holds fails, and so does an entry both kept and owed. A change to a kept file's names is a change to the guard, read in its review.

What `OWED` holds stays a workspace's own (`ci/kit/owed.ts` `literals(fix, path, counts)`, beside `kernels`), under the fix that removes it.

**Each finding on this base, and what it gets:**
- *Kept* (four): `contract/src/queue-names.ts`, written by `npm run generate` from the configs and held equal by the generated guard; `cutover.json`, the manifest, which must name each live object it removes after the config that names it today has lost it, and whose every name `cutover.test.ts` holds to that file; `sandbox/template/image.ts` `NAME`, E2B's template name, an image input pinned by the template digest (`s6.md` item 2), in E2B's namespace and not the Worker's; `owner/guards/rules.ts` `RING_QUEUES`, the owner's own statement of the one queue a bound Worker may produce onto, which by design does not follow the producers' configs (the generated `QUEUES.RING` would follow a producer moved onto the erasure queue).
- *Fixed here:*
  - **The config rules' `name`** (six `rules.ts`): `configProblems` held each config's name to a copy of it. The generator already refuses a service binding that names no platform Worker (`ci/kit/types.ts` `target`), and `facts.ts` does the same for the deploy order, so the copy only made a rename two edits. `ConfigRules.name` goes, with the two probes of it (`codemode`, `gateway` "another name").
  - **`contract/services.json` keyed the queues' messages by queue name,** a copy of each producer's `queue`. It keys them by the producer's binding, as `QUEUES` does (`types.ts` reads `table.queues[binding]`). The manifest's edit entry names `IMPORTS`.
  - **The owner's and files' config probes** read the Workers' names from the configs (`platformConfigs`); a probe whose name the check ignores names none.
  - **`doors/test/app.e2e.mjs`** reads the doors' name from its config, as it reads the gateway's.
  - **Six comments** (`codemode/src/index.ts`, `contract/src/{codemode,gateway,sandbox}.ts`, `owner/src/sandbox.ts`, `sandbox/src/index.ts`, `codemode/test/codemode.test.ts`) point at the Worker's folder.
- *Owed (`B10`), in lane R8's files, which this PR stays out of:* `deploy/guards/deploy.test.ts` (108), `deploy/guards/offsite.test.ts` (3), `deploy/checklist.md` (18), and the comments in `contract/src/files.ts` and `files/src/index.ts`. The deploy workspace gets its `rules.ts` for them. Their branch is `untangle/x1b`, after R8 merges: the deploy kit's tests read `facts.ts`, and the checklist and the two comments point at the configs.

**B22's sweep doorbell half.** `d2` B22 has the owner's reseal sweep take the ledger's subscriptions and answer `left` the same way. d3 §I adds the gateway's deadline before a provider's headers, untested once its test-only parameter went, and `egress.ts:71`'s "5-minute" comment.
- R7 built the sweep (`r7-rotation.md` item 4): `gatewayReseal` folds `Connections.reseal` and `Doorbell.reseal`, which calls `GATEWAY.resealDoorbell`; `doorbell.test.ts` "a gateway key rotation's sweep of the doorbell" holds `left` 0, 1 and 2 for subscriptions.
- #5509 replaced the comment with `EGRESS_DEADLINE_S`.
- `gateway/test/forward.test.ts` "ends a call its provider never answers at the forward deadline, and not before" drives the deadline before any header on fake time.
- **Chosen:** prove the owner's half by a mutation (the sweep without the doorbell's half), and tick the line.

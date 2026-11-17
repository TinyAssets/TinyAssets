# O4c as built (2026-11-17)

Lane O's owed line X1, B10's literal guard over every folder (`o4b.md` item 6), and B22's sweep doorbell half (O5; d3 §I).

## The plan (research, before building)

**What the guard finds.** B10's rule is "no `tinyassets-*` storage, queue or Worker literal in any file outside the configs and `deploy/platform.json`". A pattern over `tinyassets-*` alone also finds names that are none of the three: the export format, HKDF labels (`tinyassets-api-key-client`, `tinyassets-mcp-family`), the kit's record kinds (`tinyassets-routes`, `tinyassets-rotation`), the old proxy's Worker and temp folders. Each of those would need an exception of its own.
- **Chosen:** the guard takes its names from the facts: `deploy/facts.ts` `NAMES` (each Worker's script), `RETIRED`, `BUCKETS`, `QUEUES` (dead-letter queues included) and the KV `NAMESPACES`' titles, which `facts.ts` reads from the six configs and `platform.json`. Nothing restates them.
- **What counts as a match:** a name counts where no letter, digit, `-` or `_` touches it. So `tinyassets-files-prod` does not count as `tinyassets-files`, and the E2B template `tinyassets-sandbox:s…` does count as `tinyassets-sandbox`.
- **Where:** every text file git holds under `platform/` and `.github/`, comments and prose included. A rename leaves a stale name behind in a comment or a checklist line just as it does in code, and either can point at the config instead.
  - The records and specs (`docs/`, `openspec/`) are not read. A decision log is edited only to correct a line, and B26's check holds the specs' names to the tree.
  - The tree-wide reader was `cutover.test.ts`'s `scopeFiles`. It moves to the kit as `ci/kit/sources.ts` `treeFiles`, with its scope as `TREE`, and both guards read it.
- **Its home:** `deploy/guards/literals.test.ts`, which extends A3's facts guard as B10 says. The layer table does not let the ci kit import the deploy kit, but a workspace's guards may.
- **The finding names no name.** It is `<path>: live names ×<count>` (`ci/kit/owed.ts` `literalBreak`). An entry that named the names would itself spell them, in a `rules.ts` or in the guard, and so be a finding.

**How it states a permanent exception.** Three options:
- *A fix value other than `B<n>` in `OWED`.* `ratchet` refuses this, and rightly: `OWED` is a debt that only shrinks, and a permanent entry never does.
- *Each workspace's own list in its `rules.ts`, as `MODULE_STATE` is.* That is ten files, each stating its own reasons: the scattered allow-list the review rule refuses. The same reason (a fixture, a generated file) would be written several times.
- *One list in the guard itself, each entry a finding and its reason.*

**Chosen:** the third, as `KEPT` in `literals.test.ts`.
- Each entry is a file, its count and its reason.
- It is held as exactly as `OWED`. A finding that is neither kept nor owed fails, and so does a kept entry that no longer holds or is kept twice. An entry that is both kept and owed leaves the owed one stale.
- Adding a name to a kept file is a change to the guard, and its review reads it.
- What `OWED` holds stays each workspace's own, under the fix that removes it (`literals(fix, counts)`, beside `kernels`).

## As built

1. **`deploy/guards/literals.test.ts`:** the guard over `treeFiles()`, its `KEPT`, and probes of the whole-name match (a longer name, a prefix and the template's `:` suffix; the homes; a workflow file counted as the ci kit's) and of `KEPT`'s own checks.
2. **Kept (four):**
   - `contract/src/queue-names.ts`, one a producer binding (its count read from its `QUEUES`, so a new queue is no edit here): written by `npm run generate` from the configs and held to them by the generated guard.
   - `cutover.json`, 5: the manifest must name each live object it removes even after the config that names it today has lost it. `cutover.test.ts` holds every one of those names to that config.
   - `sandbox/template/image.ts` `NAME`, 1: E2B's template name, an image input pinned by the template digest (`s6.md` item 2). It is E2B's name, not the Worker's.
   - `owner/guards/rules.ts` `RING_QUEUES`, 1: the owner's own statement of the one queue a bound Worker may produce onto. By design it follows no producer's config: the generated `QUEUES.RING` would follow a producer moved onto the erasure queue.
3. **The config rules' `name` is gone** (`ConfigRules`, six `rules.ts`). `configProblems` held each config's name to a copy of it. The generator already refuses a service binding that names no platform Worker (`ci/kit/types.ts` `target`), and `facts.ts` does the same for the deploy order. The copy only turned a rename into two edits. What the copies also held, that every config names its Worker and no two share a name, `facts.ts` `workerNames` holds now (review). The two probes of it go (`codemode` and `gateway`, "another name").
4. **`contract/services.json` keys each queue's message by its producers' binding,** the key `QUEUES` uses, and no longer by the queue's name, which was a copy of the producer's `queue`. `types.ts` reads `table.queues[binding]`, and the unsent check compares bindings. The generated files are byte-identical. The manifest's edit entry names `IMPORTS`.
5. **The tests read the names:**
   - the owner's and the files Worker's config probes, from `platformConfigs`;
   - `doors/test/app.e2e.mjs`, from the doors' config, as it already reads the gateway's;
   - `deploy/guards/offsite.test.ts`, `FILES_BUCKET` from `facts.ts`.
6. **Nine comments** point at the Worker's folder in place of its name: `codemode/src/index.ts`, `contract/src/{codemode,files,gateway,sandbox}.ts`, `files/src/index.ts`, `owner/src/sandbox.ts`, `sandbox/src/index.ts` and `codemode/test/codemode.test.ts`.

## B22's sweep doorbell half

`d2` B22 has the owner's reseal sweep take the ledger's subscriptions and answer `left` the same way. d3 §I adds two items: the gateway's deadline before a provider's headers, untested once its test-only parameter went, and `egress.ts:71`'s "5-minute" comment. All three were already built:
- **The sweep:** R7 built it (`r7-rotation.md` item 4). `gatewayReseal` combines `Connections.reseal` and `Doorbell.reseal`, which calls `GATEWAY.resealDoorbell`. `doorbell.test.ts` "a gateway key rotation's sweep of the doorbell" holds `left` at 0, 1 and 2 for subscriptions.
- **The comment:** #5509 replaced it with `EGRESS_DEADLINE_S`.
- **The deadline:** `gateway/test/forward.test.ts` "ends a call its provider never answers at the forward deadline, and not before" drives it on fake time, before any header.

The proof is a mutation: `gatewayReseal` without `o.doorbell.reseal()` fails all three of the sweep's tests. The line is ticked.

## What a caller sees

Nothing changes. Every `src` change is a comment, and the generated files are unchanged. The recorders, base `5ec1105a7` (main, merged in) against the head: `wire`, `tools`, `rpc` and `rv` the same.

## Mutations, each killed

- The whole-name match without its lookarounds: the probe and the tree test fail.
- `platform.json` dropped from the homes: both fail.
- A kept count off by one: the tree test fails.
- An owed count off by one: the tree test fails.
- `types.ts` back on queue-name keys: `npm run generate` throws.
- B22's mutation, above.

## Left

- **`untangle/x1b` (lane X, `tasks.md`):** `deploy/guards/deploy.test.ts` (73) and `deploy/checklist.md` (17), owed under `B10` in `deploy/guards/rules.ts`, which is new. The kit test pins the facts against literal lists (`BUCKETS`, `QUEUES`, the plan's lines). Reading them from `facts.ts` is a rewrite of that test's oracles, its own PR.

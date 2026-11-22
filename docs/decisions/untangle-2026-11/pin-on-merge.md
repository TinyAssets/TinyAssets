# pin-on-merge as built (2026-11-22)

The dark redeploy's plan (run 44940429741) refused: owner migrations 19 and 20 and
SandboxControl migration 4 had merged without pins, and #5753 pinned them. The deploy's
refusal was the first signal. This change makes the PR that adds a migration fail until it
pins it, so no deploy can meet an unpinned one.

## Research

- **How pins landed.** None of the 16 owner and 3 SandboxControl migrations added after each
  lock's first deploy was pinned by the PR that added it. Each was pinned by a later PR after
  its merge (#5511, #5519, #5537, #5541, #5550, #5559, #5589, #5608, #5613, #5618, #5623,
  #5634, #5637, #5675, #5683, #5753), each one a `tasks.md` line or an owed item, and three were
  forgotten. These pins followed `deploy/checklist.md`'s "pin only when you deploy", yet each
  was made at a merge, with no deploy.
- **What each check compares.** `holdLocks`, each Worker's migrations guard, compares the
  head's code with the head's lock, and only the pinned migrations. `ci/guards/locks.test.ts`
  compares the head's lock with the base's, so only pins already on `main` are frozen. `digests`,
  the deploy's check, wants every migration pinned. That left migrations past the pins passing
  every check until the deploy.
- **The `<commit>` argument.** `pin` reads it only to mark a first deploy. Every lock is marked,
  so a pin today records no commit, and nothing ties a pin to a merge or a deploy.
- **When a migration is frozen.** Any deploy from `main` may run any migration on `main`, and
  the repo records no list of which merged migrations a live object has run. So the merge is
  the last point a review can check. It is also where practice already froze them: each pin PR
  followed its merge. Fixing a merged migration that has not been deployed means adding a
  new migration, which is cheap.
- **Unmerged work is not frozen.** Since the growth guard freezes only the base's pins, the
  adding PR can still edit its migration and pin it again. `pin` refuses a changed pin because it
  cannot tell merged from unmerged, so the PR takes main's copy of the lock and pins again. The
  guard's message says this. Two PRs that each add migration N both append to the same lock
  array and schema list. The second's merge of `main` conflicts in both files and is resolved
  by renumbering and pinning again. Nothing passes silently, because CI reruns the guard on the
  merge.

## Choice

1. **The migrations guard requires every migration pinned once the lock marks its first deploy**
   (`lockProblems` in `ci/kit/locks.ts`). It is one rule, read by the guard (`holdLocks`), by
   `pin` (which checks only the migrations up to the pins, since adding the rest is its job), and
   by the deploy (`deployProblems`, now that rule plus the unmarked first deploy and the
   Worker with no lock). The deploy's refusal stays as the backstop, and it now names the exact
   problem in place of "pins N and ships M, or one differs".
   - Rejected: a deploy step that pins. The kit runs from `main` and cannot commit, and the
     deploy is the late signal this change removes.
   - Rejected: a separate CI guard over every Worker. It would be a second copy of the
     migrations guard's rule.
2. **Messages say "pinned", not "deployed".** A pin now means "on `main`". The lock's field
   keeps its name, `deployed`: the growth guard reads it from the base, so renaming it would
   need a two-step change for no behaviour.
3. **`deploy/checklist.md`'s three steps become one paragraph.** The PR that adds a migration
   pins it, and a new Worker's lock marks its first deploy by the same command before that
   deploy. The owner-object spec says the same and gains the scenario "A migration added
   unpinned".
4. **No skill or gates-page line.** The guard's failure names the command (`node ci/kit/locks.ts
   pin <worker> HEAD`), and it fails in the PR that needs it.

The test `fails the change that adds a migration to any Worker's lock unpinned` adds a
migration to each real lock and expects `holdLocks` to name that lock. No open PR adds a
migration (checked 2026-11-22), so nothing in flight starts failing.

---
name: untangle
description: Find and remove hidden links, needless links, surplus and special cases, so a change lands in one place and nothing breaks unnoticed. Use when building, refactoring or reviewing any change in this repo, and when untangling a whole area.
---

# untangle

Untangle the code a change touches, or a whole area, and finish the job.

## How this project uses it

- **Every builder** runs step 2 on the code it touches and its callers before
  building, 3 and 4 while building, 5 on its diff before pushing. It fixes
  problems in scope and adds none: no shim, alias, copy of a value, one-caller
  parameter, test-only hook, optional binding or special case. A stale test,
  guard or rule never blocks a fix: change or delete it in the same PR. It is
  the PR's steward (the steward skill).
- **Process first.** A fix to how we build (CI, gates, guards, skills, briefs,
  tooling, review), a cost every PR pays (`scripts/pr_times.py` times them) or
  rework that repeats goes ahead of product work, whose lanes keep running:
  the next free slot, its own PR or the process PR at hand, with Fact 17's
  retro-check now. A reviewer blocks deferring one. Next before the cutover:
  untangling, security and what code or a stored record builds on. The cutover
  rule never defers a process fix; any other fix waits past the cutover only
  when nothing builds on it, it works after the window closes, and its failure
  is rare, loud and recoverable with what exists: Fact 17's one exception,
  listed with its branch in `tasks.md`'s "After the cutover".
- **The PR body** is the one document. Its **Untangle check** says what was
  found, fixed and left (and why), and that the diff adds none of the four kinds.
  Decisions go in `docs/decisions/<log>/<pr>.md`, headed
  `# <PR> as built (<date>)`; a shared log is only corrected. Anything found
  and left, by builder or reviewer, is fixed in place or owed there: a `- [ ]`
  line naming the PR, branch or `live:` wait that fixes it, ticked when done
  (`review_checks.py --owed` lists them). The PR ticks its own `tasks.md`
  line, adding none. Before each push, `review_checks.py --untangle-check`
  on the body allows it; a body edit goes on the open PR, which CI reads.
- **Every reviewer**, whatever engine built the PR, follows `REVIEW.md`,
  which includes this skill: it reviews every head and never fixes.
- After each batch of merges, a step-5 search across everything merged and
  open looks for drift between PRs; unopened branches wait for it.
- CI is the one full run; a coordinator's prompt adds or drops no check this
  section names. Push a draft once it compiles, having run once only what the
  diff can break (the done-check: `docs/reference/executable-gates.md`), one
  mutation per declared change. Read only what applies.
- **Parallel by default:** every PR sharing no file with open work starts now,
  from `main`, never stacked; one blocked on some files starts now without
  them, naming its part 2.
- An agent runs git only in worktrees it made; a reviewer reads the PR as git
  objects from its own worktree of `origin/main` and never runs its code (the
  peer-agents skill's reviewer procedure). On a shared host, heavy runs (`test:root`,
  `test:sizes`, `test:memory`, the deploy harness) take the host's run lock;
  a timeout under load is rerun under it, never a finding. When done, delete
  only what you wrote outside the repo, and your `node_modules`.
- A change bringing a measured time or size (a watchdog, budget, quota)
  within about 20% of its ceiling lands with its fix, or a reviewer blocks.
  CI is about 2.7x slower than a local host: a test past a third of its budget
  locally is near its ceiling. A test writing a full page of objects, or
  growing with each migration, is a size test: tag it `large`.

## 1. Scope

Comments and Tests are both in scope, every time (step 4).

## 2. List every problem

Don't change anything yet.

Go through the code asking one thing: **should these pieces change together?**
Write down every place the code gets the answer wrong, as in the example
below. There are three ways to get it wrong.

**Hidden links:** pieces that must change together, where the code doesn't show it.

- Copies of logic or values: change one and the others must change too, but nothing says so.
- A module that reads a global or file set by another module: change that module and this one breaks, but nothing shows it (self-contained code).
- A feature split across folders by layer, or related code in different folders: one change needs all of them, but the layout hides that (domain-driven code, colocation).
- A call order or convention callers just have to know: change it and callers break, but nothing warns them (explicit over implicit).

**Needless links:** pieces tied together that change for different reasons.

- Callers that depend on internals: the internals change for their own reasons, and the callers break anyway (encapsulation).
- Stable code that imports something more volatile: the stable code has to change whenever the volatile code does (dependency direction).
- A general function with a parameter only one workflow understands: the function has to change whenever that workflow does (caller vocabulary).
- A shallow module: its interface changes whenever its internals do, so its callers change with it (deep modules).

**Surplus:** pieces with no reason of their own to change.

- Wrappers: they only change when the thing they wrap does (pass-through wrappers).
- Extension points and config for things that never vary: nothing will ever change them (speculative generality).
- Dead code: nothing uses it, so nothing will ever need it to change.

Also write down every **special case**: code that exists for one situation
instead of following from how the system works. Each one is a requirement the
design never accounted for, so use them to work out what the system needs to be.

- An `if` for one value
- A parameter for a specific caller
- A swallowed exception
- A retry or sleep
- A disabled test

A test's sleep is one too: a test waits on an event it caused or a record it reads, a time bound is a liveness bound only, never raised, "has not happened" is read from a record, never an elapsed window, and time that must pass is the fake clock's.

## 3. Redesign from first principles

Work out the fixes as if the system had known today's requirements from the
start. Look for the one fix behind several problems, which often looks nothing
like them. Don't fix special cases one by one.

Ask the same thing of every fix, even one that looks better in the code: pieces
that change together end up together, pieces that don't aren't tied, and
everything depends on what changes less. Write each fix above the problems it
solves, as in the example.

## 4. Fix everything on the list

Remove the special cases the redesign makes unnecessary, and keep the ones the
system still needs. Don't go looking for bugs separately. A structural fix may
fix some along the way, which is fine.

Comments:

- Comments that restate the implementation, like `// sort first so the binary search works`, only change when it does, so they have no reason of their own to change. Remove them, even if they're phrased as a why.
- Comments about something outside the code, like `// the proxy drops websockets, so we poll`, change when that does, and the code can't show it. Keep them.
- Comments that explain a workaround in the codebase, like `// wait 100ms because the cache isn't ready yet`, only change when the problem behind the workaround does. They go when the redesign removes that problem.

Tests, last, because they are your regression safety net. A test should fail on
a regression, and only then:

- Tests that repeat the implementation only change when it does, so they have no reason of their own to change. They check the same interpretation against itself, so remove them.
- Tests tied to internals break when the code changes but behavior doesn't, so they change for the wrong reason. Rewrite them to depend on behavior, which changes less.
- Tests that can't fail, whatever the code does, check nothing: remove them.

## 5. Check, and repeat until done

Have subagents search for all four kinds. **Do not stop until there is nothing worth fixing left.**

You're done when:

- **As few special cases are left in the system as possible.** How many remain is the best measure of how close you are.
- A typical change lands in one place.
- Every import is reasonable, and nothing imports anything more volatile than itself.
- Subagents have no issues left that are worth fixing.
- Intended behavior is the same as when you started, apart from bugs the refactor fixed. CI's recorders test this (`ci/record.ts`).

**Tips**

- **Start from a skeleton:** script away the function bodies, so the codebase fits in context.
- **Summarize with subagents:** so you can reason across the whole codebase at once.
- **Check the dependency graph often:** script it, and rerun it as you go.
- **Enter where leverage is highest.**
- **Keep a decision log:** it outlasts compaction.
- **Use Git as evidence:** for how often pieces change, and what changes together.

## Decision log example

The list of problems, each under the fix that removes it:

```md
- Make the general `Dialog` give focus back to whatever had it before it opened, so the wait and the flag can both go.
  - `ui/LogoutDialog.tsx:30` waits 100ms after closing and hopes the message input can take focus by then.
  - `ui/SettingsDialog.tsx:61` sets a global `skipRefocus` flag to stop the logout dialog it opens from sending focus to the message input.
- Define a `PaymentProvider` interface that Stripe and PayPal both implement, so checkout and refunds no longer know which one they're using.
  - `checkout/submit.ts:9` depends directly on `payments/stripe.ts`.
  - `refunds/refund.ts:22` wraps every Stripe call in `if (order.provider === "paypal")`.
- Make every user action a command with a name, a handler and an undo, so the toolbar, menu, shortcuts, palette and undo history all run from one list.
  - `ui/Toolbar.tsx:40` and `ui/Menu.tsx:22` call the same actions, each with its own check for when they're disabled.
  - `shortcuts/keymap.ts:18` has a switch with one case per action, separate from the menu's list.
  - `ui/CommandPalette.tsx:55` keeps its own list of actions, and it's missing the newer ones.
  - `history/undo.ts:30` has a special case for every action that can be undone.
  - Every action handler has to call `track()` by hand, and some don't.
  - `ui/Toolbar.test.tsx` and `ui/Menu.test.tsx` test the same actions separately.
```

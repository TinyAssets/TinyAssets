# strongest-ranking: the ranking is shipped data an owner file overrides (2026-11-21)

`untangle/strongest-ranking`. Founder call 4 (`founder-answers.md`): "'Strongest' is a ranking
shipped as data, which the owner can override in a file. ... a plan whose model is in the
ranking's top tier wins over a metered key. The ranking's `about` stops claiming that a package
keeps it current." Direction 4: "Users connect any model source; default to their strongest
connected source." It follows #5723 (`strongest-plan.md`), which ranks a plan by the model its
engine record declares (`cli.login.model`).

## Research (Fact 14), from the code, before the design

### Where the ranking lives and who reads it

- `owner/src/model-ranking.json` (21 hand-ordered entries) is read by `owner/src/auto.ts` alone
  (`ranks()`, `rankOf`, `ranked`), and `ranked` by `owner/src/runs.ts` `choose()` alone, twice:
  the strongest key whose source lists a named engine (`keyFor`), and `auto`'s pick among keys
  and plans.
- What the ranking must agree with lives in `platform/sources`: every sign-in record's
  `cli.login.model` must match an entry, or that plan ranks unknown. #5723 held that with an
  owner test (`owner/test/auto.test.ts` "a subscription's plan") that reads the sources'
  records: a record change in one package fails a test in another.
- The source records' layer (`ci/kit/layers.ts` "the source records") may hold data in
  `sources/src/**`; the owner, the doors and the runner already import it. Nothing in the
  contract reads model strength, and the contract never reaches the sources. So the ranking's
  home is `platform/sources`, beside the records whose models it must rank, held by the
  sources guard.

### Where an owner file can be read at admission

- A run is admitted on what `Launcher.readAgent` reads before its unit: every path into a run
  (the `converse` and `run_graph` doors, a waiting run's turn on the lane, a mail's delivery, a
  due schedule) calls it, and its whole-file read (inline or chunked) is the one reader of an
  owner file at admission. Reading the owner's ranking there reaches every path at once.
- An in-unit read (`files.open`, synchronous) sees only inline content, 4,096 bytes
  (`INLINE_BYTES`); a ranking written in full can pass that, so the read is the launcher's
  whole read, not the unit's.
- The path must be one only the owner's own authority changes (it decides which credential a
  run spends): `OWNER_TREES`. Under `/workspace/agents` it could collide with an agent of that
  name (`AGENT_ID` allows dots), so it is a top-level owner file beside the profile:
  `/workspace/model-ranking.json` (`contract/src/paths.ts` `RANKING`), added to `OWNER_TREES`.
  It is not in `CLIENT_READ_ONLY`: no engine reads it, only the object.

### What "top tier" needs, and the unknown model

- The ranking was a flat order: no tier, so "a plan in the top tier wins over a metered key" had
  nothing to read. A tier is a group of comparably strong models. One rule covers the call with
  no special case for tier 0: **within a tier, a plan comes before any key**. A key outside the
  top tier already ranks below a top-tier plan, so a top-tier plan wins over every key, as the
  call says. Below the top it reads the same way (a plan beats a key of its own tier; a key a
  tier above beats it). The tiers shipped group the existing order by model generation: the
  current frontier (`claude-fable-*`, `gpt-6.1-*`, `gpt-6-*`, `gemini-4-*`, `claude-opus-5*`),
  the one before it, then two more. The order inside and across tiers is the old order, unchanged.
- On main a model no entry matched sorted after every matched one by its catalog output price,
  silently, and a plan was given a fake price of 0 to sort at all (`planModel`). Now an unknown
  model ranks after every tier, ordered as a tier is (plans first), its keys by their catalog
  output price: the one stated order an unranked model has. When `auto` runs one the agent did
  not name, the owner is told (`model_unranked`), once while that notice is kept, not once a run.
  On the platform's ranking no plan is unknown: the sources guard holds every sign-in record's
  model to an entry. An owner's own ranking may leave a plan unranked; it is then unknown too.
- An owner's file that is no ranking refuses the run (`invalid`, naming the file), never a
  silent fall back to the platform's (Fact 8). It replaces the platform's whole: a merge would
  need a rule of its own for every conflict, and the owner who wants the platform's entries
  copies them.

## Problems found (untangle step 2), with the fix above each

- **The ranking moves to `sources/src/model-ranking.json`, read by `sources/src/ranking.ts`, and
  its plan check to the sources guard (`sources/guards/ranking.test.ts`).**
  - `owner/test/auto.test.ts` held the sources' sign-in records to the owner's ranking: a
    hidden link across packages.
  - `auto.ts` `ranks()` held the file with a hand object test (the contract's `keyed` is the
    kernel, `ci/guards/kernels.test.ts`), and `glob` built a regex for every entry at every match.
- **A plan is a candidate of its own (`Plan`; `Candidate.connection` null), not a fake key.**
  - `runs.ts` `PLAN = "plan:"`, a fake connection id, and its two `startsWith(PLAN)` cases.
  - `runs.ts` `planModel`, a fake `ModelRecord` with a price of 0, cast, so a plan could sort.
  - `runs.ts` `UsableSource`, a copy of `connections.ts` `Usable` (its comment said so).
- **Tiers, and within a tier a plan before a key**, so a top-tier plan wins over every key.
- **The unknown model is a stated last group, and `auto` tells the owner when it runs one**
  (`Notices.once`, `RunHost.noticeOnce`): on main the price order was a silent guess.
- **The owner's file, `/workspace/model-ranking.json`, read whole with the agent's file**
  (`Launcher.readAgent`, `AgentRead.ranking`), handed to `choose` on the ask.
  - `choose(file, unattended, authority = OWNER, now = 0)`: defaults no caller used; it takes
    the ask now.
- **`owner/src/paths.ts` `AGENTS_OWNER_ONLY` names `OWNER_TREES` from the list**, where it was a
  hand copy of it; `owner/test/agent-doors.test.ts` "name the agent trees", a copy of
  `contract/guards/paths.test.ts`'s list, goes.
- **The `about` is corrected**: it no longer says a package keeps the ranking current. The
  platform changes it by a reviewed change; nothing updates it on its own; an owner replaces it.
- Left, with reasons:
  - The owner's ranking is read before the admission unit and not held to its etag there, as
    `agent.json` is: `agent.json` names the run's scopes, so a change between read and unit
    refuses (`agent_changed`); a ranking edited in that moment ranks the run by the version read
    a moment before, as if the edit landed just after, and widens nothing.
  - The same-provider rule (a key of a plan's provider takes the plan, at the key's rank) is
    #5723's, unchanged.
  - Where the tier boundaries fall is a judgment of model strength, the founder's to move (data,
    one file): question 1.
  - The bundled Mistral, Groq and Cerebras sources' models match no entry and rank unknown:
    question 2.
  - The starter's files do not name `/workspace/model-ranking.json` to the agent: the path is
    the contract's, the spec's and the ranking's `about`, and the `model_unranked` notice names
    it. A line in `main`'s `AGENTS.md` is a starter version of its own.

## Owed

Nothing.

## Founder questions

1. **The tiers.** The shipped tiers group the old order by model generation; the top tier is
   `claude-fable-*`, `gpt-6.1-*`, `gpt-6-*`, `gemini-4-*`, `claude-opus-5*`. So Claude Max (Opus
   5.5) and Codex (`gpt-6.1-sol`) plans are top tier, Gemini's (`gemini-3.1-pro-preview`) second,
   Kimi's fourth. Move a boundary by editing `sources/src/model-ranking.json`.
2. **The bundled sources the ranking does not rank.** Mistral, Groq and Cerebras keys' models
   rank unknown (after every ranked model, by price, with a notice). Should the platform rank
   them?

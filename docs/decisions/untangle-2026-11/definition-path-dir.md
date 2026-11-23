# definition-path-dir: a folder at an agent's definition file refuses the definition's read (2026-11-22)

`untangle/definition-path-dir`, owed by `ranking-path-dir.md` (#5756):
`owner/src/definition.ts` `definitionRead` served a folder at the agent's `AGENTS.md` or
`SOUL.md` as an absent file, so a main agent was served the agent without its instructions or
soul, and nothing told the owner. One rule, the one `untangle/agent-mcp` (#5754) sets for the
files a run is admitted on: absent means none; anything else that is not a file is refused,
naming the path (Fact 8).

## Research (Fact 14), from the code

- The one reader is `definitionRead`, which every channel of the MCP door's definition reads
  (`doors/src/definition.ts`: the resources, the `agent` prompt and the Skills extension, one
  read a request). A refusal from it already reaches the main agent through `answered`, as the
  definition past `DEFINITION_BYTES_MAX` does (`result_too_large`, `doors/test/mcp.test.ts`),
  and the door's tools still serve. So a refusal is the mechanism, and no case needs a new one.
- #5754 refuses a folder at `agent.json`, `mcp.json` and the owner's ranking with
  `Refusal("invalid", "<path> is not a file")`, written inline in `Launcher.#whole`. The same
  check written again in `definitionRead` would be a copy. On main the files' own module
  (`Files`) answers `stat` and `open` (which refuses a folder as `is_directory`, without the
  path, and an absent file as `not_found`), and has no read that answers an absent file as
  null. Both readers need exactly that read.
- #5756 changes `#whole`'s read after its `open` (the size ceiling); it does not touch the
  stat.

## Problems found (untangle step 2), with the fix above each

- **`Files.openFile(path)`: the file at `path`, pinned as `open` pins it, or null where
  nothing is; anything else there refused `invalid`, "`<path>` is not a file". The one
  "is not a file" refusal, in the files' module, which every reader of a file whose absence
  has a meaning takes.**
  - `definitionRead`'s `one` read a folder at `AGENTS.md` or `SOUL.md` as no file.
  - #5754's `#whole` writes the same refusal inline (owed below, so it is not a copy).
  - `fileAppend` (`files.ts`, on main) wrote it inline too, as "`<path>` is a directory"
    (reviewer's find): it now reads through `openFile`, the message the one rule's.

## Left, decided

- **Skills.** A skill is a folder, its name not hidden, holding a `SKILL.md` file
  (`skillUnion`, the contract's, which the runner's projection and this read share). A folder
  at `SKILL.md`, or a file where a skills folder goes, holds no skill by that rule, as a folder
  of notes without a `SKILL.md` does; it is not a file read as absent. Refusing it here alone
  would make the doors and the runner disagree on the agent's skills. A skill's own files are
  every file below its folder (`filesBelow`), so no folder there is read as a file.
- **`starter.ts`** reads `stat(...)?.kind === "file" ? open : null` in four places. There a
  folder at a starter file's path is the owner's own, and the starter leaves it; nothing reads
  it as the starter's file. A different rule, so not this read.
- **`fileEdit`** refuses an absent file and a folder alike (`not_found`): an edit needs a file,
  and no absence has a meaning there, so it is not `openFile`'s reader; nothing is read as
  absent in silence.

## Owed

- [x] `untangle/agent-mcp`: `Launcher.#whole` takes `Files.openFile` in place of its own
  inline check, the message and code unchanged, so its tests hold. If #5754 lands first, this
  branch makes that change on merging `origin/main`. Done (#5754).
- [x] #5756 (`untangle/ranking-path-dir`): whichever of #5756 and this PR lands second ticks
  `ranking-path-dir.md`'s `untangle/definition-path-dir` line, done by this PR. Done (#5754).

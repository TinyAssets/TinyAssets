# ranking-path-dir: an owner file a run is admitted on, refused whole when it is not a usable file (2026-11-22)

`untangle/ranking-path-dir`, owed from #5744's review: the owner's
`/workspace/model-ranking.json` (`contract/src/paths.ts` `RANKING`) replaces the shipped
ranking (`sources/src/model-ranking.json`). One rule: absent means the shipped ranking;
present but not a usable ranking file refuses the run, naming the file (Fact 8).

## Research (Fact 14), from the code

- The one reader is `owner/src/launch.ts` `Launcher.#ranking`, through `#whole`, the
  launcher's whole read of an owner file at admission, which `readAgent` uses for the agent's
  `agent.json` too. Every path into a run (the `converse` and `run_graph` doors, a waiting
  run's turn, a mail's delivery, a due schedule) calls `readAgent`.
- How a bad owner file is reported: the run is refused. A door's caller gets the refusal. On
  the lane the owner is told: `run_refused` for a waiting run, `mail_refused` (and the mail
  marked undeliverable) for a mail, the schedule's notice for a schedule. `agent.json` is
  reported the same way (`agent_file_invalid`). So a refusal from the reader is the one
  mechanism, and no case needs a new one.
- Each way the file can be unusable, on main:
  - Absent: the shipped ranking. Right.
  - A directory: read as absent, so the shipped ranking was used silently. This is fixed
    by `untangle/agent-mcp` (`#whole`: "`<path>` is not a file", `invalid`, for every
    caller), so this branch leaves it to that one.
  - Bytes that are not UTF-8: refused `invalid`, "`<path>` is not JSON text" (the strict
    decoder runs inside the JSON parse's `try`). Covered.
  - Invalid JSON: refused `invalid`, the same message. Covered (`owner/test/runs.test.ts`).
  - The wrong shape: `rankingOf` refuses it `invalid`, naming the file and the rule broken.
    Covered (`sources/guards/ranking.test.ts`, every shape; `runs.test.ts`, one end to end).
  - Too large: **not covered.** `#whole` read any size whole, joining every chunk into one
    buffer outside the owner pool (ADR-037), so a large file was read in full into the
    isolate that every owner object on it shares, with no refusal.

## Problems found (untangle step 2), with the fix above each

- **An owner file a run is admitted on is at most `ADMITTED_FILE_BYTES`, half a unit
  (`owner/src/holds.ts`). Past that the run is refused `invalid`, naming the file. A
  chunked read takes `ADMITTED_FILE_UNITS` of the pool.** This is a technical ceiling with
  its reason (Fact 15): the read parts and their joined copy are in hand at once, within one
  unit, the same reasoning as `DEFINITION_UNITS`. It is not tied to `DEFINITION_BYTES_MAX`,
  whose reason is the MCP door's body. One check in `#whole` covers `agent.json`, the
  ranking and, once `untangle/agent-mcp` lands, `mcp.json`. The bytes are derived from the
  units, and the ceiling has its row in `limits-audit.md` (review).
  - `#whole` held a whole file of any size, outside the pool.
- **The chunks are joined by `Files.range`, the files' one join.**
  - `#whole` had a hand copy of `range`'s fetch-and-join.

## Left, decided

- Bytes that are not UTF-8 read "is not JSON text": the code, the file named and the
  owner's fix (write JSON text) are the same, so the message stays.
- `JSON.parse`'s own objects are not counted in the pool, nor the file's bytes and text once
  the read's unit is released (as `definitionRead`'s answer is not). This was already true of
  every parse of an owner file. The text it parses is now bounded where before it was not.
- The directory case is `untangle/agent-mcp`'s, so this branch does not repeat its check.
  The two branches merge cleanly: this one's size check sits after the unit that branch
  changes.

## Owed

- [ ] `untangle/agent-mcp`: a folder at `RANKING` (and at `agent.json` and `mcp.json`)
  refuses the run, so it does not read as absent.
- [ ] `untangle/definition-path-dir`: `owner/src/definition.ts` `definitionRead` serves a
  folder at `AGENTS.md` or `SOUL.md` as an absent file (`one`), the same silent case on the
  path that serves a main agent its definition.

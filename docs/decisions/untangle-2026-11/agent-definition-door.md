# agent-definition-door: the MCP door serves the agent's definition (2026-11-21)

`untangle/agent-definition-door`, slice D3 of `agent_definition_research.md` §4.4. It builds
the founder's call 1 (`founder-answers.md`): the door serves the owner's agent definition as
MCP resources, prompts and skills that the client loads, never as tool text that steers. The
research is the design (§3: no main agent of the four applies a server's prompts, resources or
skills without the user's step, so every channel is served from one read); the coordinator's
calls (`next_builders.md`, "Agent definition") settle which agent and the custom profile's
instructions.

## What landed

1. **The SDK is 2.4.0** (`@modelcontextprotocol/server` and `client`, published 2026-10-23,
   pinned exactly): it ships the Skills extension (`ext/skills`, SEP-2640, Final). The lockfile
   moves three packages and drops the owner's nested copies the Agents SDK's 2.4.0 peer needed.
2. **The skill union is the contract's (`skillUnion`, `contract/src/agent-definition.ts`)**,
   out of `sandbox/runner/project.ts`: the agent's own `.agents/skills`, then
   `/workspace/.agents/skills`, then `/workspace/skills`, the first holding a name winning it; a
   skill is a directory, its name not hidden, holding `SKILL.md`. It reads through a
   `SkillTree` (`dirs`, `file`), so the runner reads its local disk and the owner its files
   store by the same rule.
3. **One owner door, `definitionRead`** (contract row: class `read`, capability `read_files`,
   no request, no tool; owner steps: `account`, `units`, `seeded`; handler
   `owner/src/definition.ts`): the definition of the agent the caller acts as, its `AGENTS.md`
   and `SOUL.md` (null when absent) and every file of each skill of its union, bytes as stored
   (`ServedDefinition`). Pinned in one unit, then its chunks read. Its capability is held by
   the scope step, as every file read is, so an outside client sees only what its grant reads:
   a key's scoped grant, with no registered agent, is refused as its `file_read` is.
4. **Which agent:** a run's own (`account.run.agent`); every other caller's, `main`
   (`MAIN_AGENT`). The coordinator's call is "the agent its connection registered, else `main`";
   a registration (`agent_register`, `outside_agents`) names a command center, a display name,
   a delivery method and a recipe, never one of the owner's agents, so every connection is the
   `else` today, and a key on `/mcp` (#5735) resolves the same way by its grant. Owed below.
5. **The MCP door (`doors/src/definition.ts`)**, beside the tools, from that one read, made
   only when one of these methods is asked (a tool call reads nothing more):
   - resources: `tinyassets://agent/<agent>/AGENTS.md` and `SOUL.md`, and each skill's
     `SKILL.md` listed (`skill://<skill>/SKILL.md`), every skill file readable;
   - the `agent` prompt: the two files embedded, then a `resource_link` to each skill;
   - the Skills extension (`skills/list`, `skills/get`), its entries built by the SDK's own
     `skillEntry` from the bytes, so each digest is that of the bytes `resources/read` serves.
     The SDK serves it on 2025-11-25 too. A skill whose `SKILL.md` is not an Agent Skill's (a
     name matching its directory and a description, in its frontmatter) is left out of the
     extension with a warning, as the SDK's own catalog leaves one out; the file tools still
     read it.
   - An owner refusal is the request's JSON-RPC error (`asRequest`, `refused.ts`).
   - Every byte is served as stored (Fact 9), text when it is UTF-8; nothing is run.
6. **The `instructions` are a table by profile (`INSTRUCTIONS`, `instructions.ts`)**, which
   `mcp.ts` reads in place of its ternary. `run` and `directory` are the fixed texts as before;
   `custom`, the owner's own connector, adds one sentence naming the `agent` prompt, after the
   first 512 characters (the coordinator's call). The prompt's name, title and description are
   platform text there too, so the served-text guard holds them.

## Departures from the research

- **One prompt, `agent`, not one per skill.** The skills reach a client through the extension
  and their resources, and the `agent` prompt links each; a prompt per skill would be a third
  copy of each skill's entry.
- **No `definitionRead` digests from the files store.** The store keeps a content id, not a
  file's SHA-256; the SDK hashes the bytes it serves, which is what a digest must equal.
- **The served-text guard does not hold the owner's definition.** The starter's `AGENTS.md`
  says "never ... install packages ... under /workspace", which the guard's ask pattern reads
  as an ask. The definition is the owner's text, served when the user pulls it in; the guard
  holds the platform's own words (the instructions, the prompt's title and description).
- **A ceiling, `DEFINITION_BYTES_MAX` (2 MiB, half `MCP_BODY_MAX`, `contract/src/limits.ts`,
  `limits-audit.md`)**: the SDK materializes each MCP answer whole, the `agent` prompt both
  files at once, so the answer with its JSON escapes stays within what the doors' isolate
  holds a few at a time (ADR-037, ruling 3); the object reads it in one unit of its pool
  (`DEFINITION_UNITS`). The review lowered it from 16 MiB, which put up to 80 MiB of one
  answer's copies in a 128 MB isolate outside any pool. Past it, `result_too_large`, never cut
  (Fact 15: a recorded technical ceiling). Raising it needs one file an answer and stored
  digests.

## Owed

- [x] A registration names the owner's agent its connection is, and `definitionRead` reads it, so a connected main agent's definition is the agent it registered, else `main` (the coordinator's call; `untangle/registered-agent`). Done there: the connection's grant names it (`registered-agent.md`).
- [ ] Claude.ai's prompt picker shows `agent`, and one Skills-extension client (VS Code, in the matrix) lists the skills (live: at the dark redeploy).

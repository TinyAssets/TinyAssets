# agent-definition: the agent definition's file formats as one contract (2026-11-21)

`untangle/agent-definition`, slice D1 of `agent_definition_research.md` §4.4. It builds the
founder's calls 1 to 3 (`founder-answers.md`) as shapes only: one agent definition for every
engine, its own MCP servers and its one hook definition. The research is the design; the
coordinator's calls (`next_builders.md`, "Agent definition") settle its open questions: a
server's credential comes through a connection the connector way (`packages_connectors_research.md`
C1), never in the file. Nothing here reaches a run or a door yet: D2 to D5 read what this slice
defines.

## What landed

1. **One contract for the agent's files: `contract/src/agent-definition.ts`.**
   - `AGENT_FILES`: `agent.json`, `AGENTS.md`, `SOUL.md`, `.agents/skills/`, and the two new
     files, `mcp.json` and `hooks.json`; `agentPath(agent, file)`.
   - `agentFile` moved here from `owner/src/agent-file.ts` (deleted), with `AUTO`,
     `AGENT_UNDEFINED` and `AGENT_DISABLED`, so every format of the definition lives in one
     module. `CREDENTIAL_KINDS` moved beside its type in `contract/src/sandbox.ts`, so the
     contract imports nothing from `sources`.
   - `agentMcp`: `{"mcpServers": {<name>: {"url"} | {"command", "args"?, "env"?}}}`, the shape
     the CLIs share. A name is one a rule's `tools` can name (`mcp:<server>:<tool>`,
     `TOOL_MATCHER`), never `tinyassets` (`RUN_DOOR_SERVER`). A URL is https with no user or
     password. No `headers` field exists, and no variable shaped like a credential
     (`CREDENTIAL_NAME`, moved to `contract/src/text.ts` from `sources/src/check.ts`). Every
     value is literal: no NUL, no `$NAME` or `${NAME}` (Claude Code and Gemini CLI expand
     them in their MCP config), no leading `!` (pi runs it).
   - `agentHooks`: `{"hooks": {<event>: [{"run": [argv], "match"?: {"kinds"?, "tools"?},
     "timeout_s"?}]}}`. The events are `HOOK_EVENTS`: `pre_tool`, `post_tool`, `prompt`,
     `session_start`, `stop`, `session_end`, `pre_compact`, and only the two tool events take a
     `match`, by R5's vocabulary (`acts.json` kinds, `TOOL_MATCHER`, moved to `acts.ts` so the
     sandbox template's tree does not reach `policy-document.ts`). `run` is a list, never a
     shell line. **No field allows a call or rewrites its input**, so R5's root-owned hook
     decides every call on every engine, whatever each engine's own precedence (research §1.6).
   - One refusal for every file, `agent_file_invalid`, its message naming the file. An absent
     `mcp.json` or `hooks.json` is no servers or no hooks; an absent `agent.json` stays no agent.
   - `definitionFileOf(path)` (a JSON file directly in an agent's directory, nowhere else) and
     `checkDefinitionFile(path, text)`, which the starter package's check runs on every file it
     carries; `definitionWidening`, below.
2. **Each CLI record's `definition` block (`sources/src/schema.ts` `Definition`).**
   `instructions` and `skills_paths` moved into it as `definition.instructions` and
   `definition.skills.paths` (the runner's projection reads the new place; nothing else changed
   there), beside:
   - `mcp`: where the agent's servers go, which is where the run's door already goes:
     `servers`, the dotted key in the record's MCP file, or with `flag` one
     `<flag> <servers>.<name>.<key>=<JSON>` pair a value (Codex's `-c`); `http` and `stdio`, a
     server's entry in the engine's own keys (`{url}`; `{command}`, `{args}`, `{env}`, each once,
     otherwise fixed text: Gemini's `httpUrl`, pi's `exposure: "direct"`, which R5's MCP tool
     names rely on); `names`, a list each name joins (Gemini's `mcp.allowed`).
   - `hooks`: the engine's own name of every event (research §4.2), with the page read
     (`cite`, `read`).
   - The record check holds what the code did not show: the servers' key holds the door, named
     `RUN_DOOR_SERVER`, in the MCP file's template (or the flag gives it), `names` names it,
     and `hooks` is `unsupported` exactly where `decide` is, since an engine's hooks go in the
     root-owned layer its decide hook does.
   - Kimi: both `unsupported`, its server entries not researched while its record waits to be
     dropped (call 17), and no root-owned hook layer (R5 item 8).
3. **The starter carries `agents/main/mcp.json` (no servers) and `hooks.json` (every event,
   none filled)**, managed, before `agent.json`; version 9 (and the plugins' 1.0.9, which follow
   it). The starter guard holds that `hooks.json` names exactly `HOOK_EVENTS`.
4. **A starter update widens by the definition's own rule (`definitionWidening`).** Scopes
   `agent.json` adds, as before; servers `mcp.json` adds or changes; hooks `hooks.json` adds or
   changes. Before, every non-`.md` file counted as a script, so the two new files would have
   reached every seeded owner as "a new script" proposal; an `agent.json` deeper in a skill was
   read as the agent's. Both follow from `definitionFileOf` now.

## Departures from the research

- **The records' `definition` block lands in D1**, not D2 (the coordinator's scope), and holds
  `mcp` and `hooks` data too. D2 adds `native` beside it.
- **The owner's file doors refuse no write** (research D1). `agent.json` is checked where it is
  read, at a run's admission, and the new files follow the same rule: D4 and D5's admission
  reads them, and D3's door serves them. A write check would be one path's special case in the
  generic files plane.
- **The skill union (`project.ts`) stays in the runner** until D3, its second reader.
- **The hook's answer format** (block on `pre_tool` and `prompt`, context on `session_start` and
  `prompt`) is the dispatcher's, D5's, with each engine's output words; D1 defines only what the
  owner writes.
- **No upper bounds** on servers, hooks, arguments or timeouts (Fact 15): the files plane bounds
  the file, and NUL is refused because no argument or variable can carry it.

## What each later slice reads

- **D2 (`untangle/agent-definition-runner`): native layers out, and the instruction checks.**
  Landed: see "D2: a run reads the definition and nothing beside it" below.
- **D3 (`untangle/agent-definition-door`): the doors serve the definition.** `AGENT_FILES`
  (`AGENTS.md`, `SOUL.md`, `.agents/skills`) for the resources, prompts and Skills extension,
  and the skill union moved out of `project.ts` into this module, its second reader.
- **D4 (`untangle/agent-mcp`): the agent's own servers.** `agentMcp` at admission, filtered by
  the egress policy (`checkDestination`); `definition.mcp` to render each server beside the run
  door. The run door itself becomes one entry of the same `http` template then, so
  `cli.mcp.template` loses its hand-written entry. A setting a server needs on one engine only
  (Codex's `startup_timeout_sec`, Gemini's `timeout`) is fixed text in that record's `stdio` or
  `http` entry, never a field of `mcp.json`; a stdio server that will not start without a
  credential-shaped variable gets its placeholder from the connection it is bound to, which D4
  designs (review, 2026-11-21).
- **D5 (`untangle/agent-hooks`): one hook definition.** `agentHooks` at admission, rendered into
  `{system}/hooks.json` for the dispatcher; `definition.hooks.events`, and the dispatcher's
  template and answer words the record gains then. ADR-034 decisions 2 and 11 gain their
  Status lines when it lands.

## D2: a run reads the definition and nothing beside it (2026-11-21)

`untangle/agent-definition-runner`, slice D2 of research §4.4, with the coordinator's call that
every engine's native settings are refused on every run, not only Gemini's or a hooked run's.

1. **Each record's `definition.native`** (`sources/src/schema.ts` `DefinitionNative`): `refuses`,
   the engine's own settings paths (Claude Code `.claude/settings.json`, `.claude/settings.local.json`,
   `.mcp.json`, `.claude/agents`; Codex `.codex/config.toml`, `.codex/hooks.json`, `.codex/skills`;
   Gemini `.gemini/settings.json`, `.gemini/skills`; pi `.pi/settings.json`, `.pi/mcp.json`,
   `.pi/extensions`, `.pi/prompts`, `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md`; Kimi `.mcp.json`,
   `.kimi-code/mcp.json`, `.kimi-code/AGENTS.md`, `.kimi-code/skills`, read from its 2.4.1
   bundle), each with its page; and `argv`, added on every run (pi's `--no-approve`, which
   replaces its `unattended_argv` `--approve`). The record check holds that no refused path is
   one the definition renders (its instructions, their shadows, its skills folders).
   `decide.refuses` is gone: the one list holds on every run, hooked or not.
2. **One refusal, before each start of the CLI** (`sandbox/runner/guards.ts`
   `definitionRefusal`, run by `runner.ts` `definitionHeld` before the start check and again
   before a resume after a renewal): in the cwd and each ancestor under `/workspace`, a
   `shadowed_by` file is `instructions_shadowed` (Claude Code's gains `.claude/CLAUDE.md`, pi's
   `AGENTS.override.md`, per `docs/configuration.md`); a `native.refuses` path is
   `engine_settings_refused` (it replaces `hook_settings_refused`); instructions past
   `max_bytes` (Codex's 32 KiB) are `instructions_too_large`, never cut. Each goes out as
   `ta.refused` with its path, and the app says it in words (`doors/app/view.js`). The
   env-file refusals run beside it at both starts (review: a resume skipped them, so an
   `.env` the agent wrote mid-run reached a resumed CLI).
3. **Skills by argv where the engine takes them so** (`definition.skills.argv_each`, `{skill}`
   the skill's directory, only there): pi's `paths` is empty and its argv carries `--skill`
   per skill of the union (`project.ts` `skillsOf`, in name order within each source, the
   agent's own first), so its untrusted `.agents/skills` and `.pi/skills` never load.

Departures:
- **`max_bytes` counts every copy** in the cwd and its ancestors under `/workspace` together:
  Codex's cap is on the total it reads, and it reads the ancestors when a `.git` marks a root
  above the cwd. Without one it reads the cwd's alone, so an ancestor's `AGENTS.md` can refuse
  a run Codex would not have cut; refusing errs loud (Fact 8).
- **`.pi/skills` is not refused**: the projection linked pi's skills there before, so the
  folder stays, emptied of its links at the next sync-in; `--no-approve` leaves it unread.
- **Claude Code's `--setting-sources user` stays on hooked runs only** until B1 says it keeps
  `AGENTS.md` and `.claude/skills`; its project settings are refused at start on every run.
  Claude Code reloads them live (R5 item 14), so on an unhooked run a file the agent writes
  mid-session is read by that session until its CLI ends; the next start is refused. That
  session's agent holds no rule a layer could lift, and its shell already does what one could.
  Gemini and Codex read theirs at start, and `--no-approve` holds for pi's whole process.
- **`skillsOf` is exported from `project.ts`** for the run's argv; D3 moved its rule into the
  contract (`skillUnion`), which it now reads.

## Owed

- [x] D2: every run keeps the agent's engine-native settings out, refusing a Gemini run whose folder holds `.gemini/settings.json` (coordinator's call), and checks `definition.instructions` (a shadowing file in the cwd or an ancestor refused, Claude Code's `.claude/CLAUDE.md` added to its `shadowed_by` per research §4.2, past `max_bytes` refused on Codex), pi on `--no-approve` with `--skill` per skill; owed live checks B1 to B4 of the research (`untangle/agent-definition-runner`).
- [ ] live: at the dark redeploy, B1: Claude Code 2.1.309 with `--setting-sources user` still loads the cwd's `AGENTS.md` and its `.claude/skills` links; if not, its record renders them by argv (`--append-system-prompt-file`, `--plugin-dir`).
- [ ] live: at the dark redeploy, B2: a run on a fresh `CLAUDE_CONFIG_DIR` loads `AGENTS.md` (its `system/init`, or a probe question), not skipped as a first session.
- [ ] live: at the dark redeploy, B3: Codex 0.166.0 reads no `.codex/config.toml` or `.codex/hooks.json` from the agent's folder in a fresh `CODEX_HOME`, so the start's refusal is the only path they take.
- [ ] live: at the dark redeploy, B4: pi 1.4.0 with `--no-approve` and `--skill` per skill (and `-e` on a hooked run) in `--mode json` lists the skills, connects the run's door, and loads no `.pi/` file of the agent's.
- [x] D3: the doors serve the definition (`AGENT_FILES`) as resources, prompts and the Skills extension on SDK 2.4.0, the skill union moved here from `project.ts` (`untangle/agent-definition-door`). Done (`agent-definition-door.md`).
- [x] D4: the agent's own MCP servers run, `agentMcp` read at admission and filtered by the egress allowlist, rendered by `definition.mcp`, the run door one entry of the same template; Gemini's `--allowed-mcp-server-names` taking each name is checked there (`untangle/agent-mcp`). Done (`agent-mcp.md`: the entries moved into `cli.mcp`).
- [ ] D5: one hook definition, `agentHooks` rendered for a runner-owned dispatcher in the decide hook's root-owned layer, which drops any allow or rewrite from a hook's answer; ADR-034 decisions 2 and 11 Status lines (`untangle/agent-hooks`).

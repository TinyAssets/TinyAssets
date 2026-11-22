# definition-commands as built (2026-11-22)

`untangle/definition-commands`, owed by D5 (`agent-hooks.md`, "Left, with why" and "Owed") and
of D4's shape (`agent-mcp.md`): whether an owner's rule on `workspace.shell` holds over the
commands the agent's own definition files run, `mcp.json`'s stdio servers and `hooks.json`'s
hooks, researched first (Fact 14).

## Research first (Fact 14), read 2026-11-22

**Which commands run, as whom, when, and by which path** (this repo at `ebf9502a5`):

- **A stdio MCP server** (`mcp.json` `{"command", "args", "env"}`): the admission reads the
  file whole (`owner/src/launch.ts` `readAgent`), keeps every command server (`network.ts`
  `agentServers`: only a URL is held to the egress), and the start carries it
  (`SandboxStart.mcp_servers`). The root runner renders it into the record's MCP config
  (`sandbox/runner/engine.ts`): Claude Code's `{home}/run/mcp.json` with `--strict-mcp-config`,
  Codex's `-c mcp_servers.<name>.*` pairs, Gemini's system settings and its
  `--allowed-mcp-server-names`, pi's `{home}/.pi/agent/mcp.json`. Each such file is root's and
  never writable by the CLI (`session.ts` `configOwned`). **The CLI starts the server**, as the
  run's user, by its command and args with its env, when it connects its servers, which is at
  its start. No tool call is made, so no CLI's pre-tool hook, R5's among them, sees it.
- **A hook program** (`hooks.json` `{"run": [program, ...args]}`): read whole beside it, carried
  in the start (`SandboxStart.hooks`), and written by the root runner into the run's own
  root-owned file (`{system}/hooks.json`, `runHooks`). **The dispatcher runs it**
  (`sandbox/runner/hooks.ts`, `ta-runner hooks <event>`, started by the CLI from the root-owned
  hook layer on each event the agent has a hook on), by its argv through `spawnBounded`, as the
  run's user, in the agent's directory, on every matching event of the run. The start check
  (`runner.ts` `hooksCheck`) only checks each program is there; it runs none. Again no tool call:
  R5's hook never sees it.
- **A skill's inline command (review).** Claude Code runs a skill's or command's ``!`cmd` ``
  as it renders, with no tool call, once its permission rules allow it (a skill's own
  `allowed-tools` does); `allowManagedHooksOnly` and `strictPluginOnlyCustomization` stop
  only hooks (code.claude.com/docs/en/skills; 2.1.309 reads `disableSkillShellExecution`
  from `policySettings`, the one switch). So the managed file sets it: while a rule needs the
  hook, each such command is Claude's placeholder, and the model can run it only as its own
  call, which the hook decides. Whether another engine's skill or custom command runs one
  (Gemini's `.gemini/commands` `!{...}`) is owed below.
- **Nothing else runs a definition command.** Every engine's own settings layers that could
  (Gemini's `tools.discoveryCommand` and its workspace `mcpServers`, pi's `.pi/mcp.json` and
  `shellCommandPrefix`, Claude Code's project `.mcp.json` and settings, Codex's project
  `config.toml`) refuse the run before its CLI (`engine_settings_refused`, the runner test "the
  agent's definition, and nothing beside it"); a main agent runs neither file (`hookNote`'s
  `main_agent`).

**TOCTOU.** What the admission reads is what runs: the start document carries the servers and
hooks; the runner reads neither of the agent's files from `/workspace` and renders each into a
file root owns (`configOwned`, `ownHooks` refusing any file not root's); the dispatcher reads only
that file. A program a hook or a server names is the agent's file and may change after the
admission, but a rule on `workspace.shell` is on the act, not on a command's text (below), so
what the program does was never what decides it, as for a `Bash` call (`acts.json`: "seen by its
command line, not by what the programs it starts do").

**What R5's rules can express** (`contract/src/policy-document.ts` `ActionRule`,
`owner/src/actions.ts` `ruleOn`, `decideTool`; `r5-decide.md`, `r5-hooks.md` item 7): a rule is
`{act, do: "ask" | "never", agent?, tools?}`. It names no command, path or pattern: every call of
its act by its agent (or by any) is held, the strictest rule winning. A `workspace.shell` rule
holds every call of its act's hook kind `shell` (and any MCP tool its `tools` name). So "the
owner's shell rules, the same matcher R5 uses" is the act and the agent: under any rule on
`workspace.shell` that holds for an agent, every command that agent's run starts is the act.

**The options.**

- **(a) The admission decides each definition command** as a call of the acts its kind is, by
  `Actions.decide`, the one decision: `never` refuses it, `ask` files its card, a yes admits it
  once. Whatever wrote the file (the agent's file tool, a sync, the owner, the starter, an import)
  and whenever the rule was written, the command is decided where it would run.
- **(b) Writing `hooks.json` or `mcp.json` is a guarded act.** A rule has no path to name, so it
  is new vocabulary, and a hidden link: a shell rule that silently holds a file write. It decides
  at the write, not at the run, so a file written before the rule, by the owner, the starter or
  a sync commit (which no rule decides, `r5-decide.md` item 3) still runs its commands. It
  cannot make the escape impossible.
- **(c) Both**: (b) adds nothing (a) does not hold, and keeps (b)'s new vocabulary.

**Chosen: (a).** A definition command is a command the run starts, so it is of the kind `shell`
(`acts.json` `kinds`); it is decided at the one place every such command passes, the admission
that hands it to the runner, by the same decision a tool call of that kind takes (`decideTool`:
every act a rule never lets be first, then the first act asked alone). No special case: the act
is read from the kind, as for a tool call.

**Left out, or the run refused?** Left out, with the owner told, as D4 leaves out a server the
egress refuses. The run goes on with what the owner's rules let it run. An agent's hook only
blocks or adds context (D5), and the owner's own rules hold beside it whatever it says, so a hook
left out never lets past anything an owner's rule holds; refusing the run instead would make one
`ask` rule refuse every run of every agent that has a server or a hook until a yes.

## What landed

1. **The kind (`contract/src/acts.ts` `COMMAND_KIND`, `acts.json` `kinds.shell`)**: a command
   the agent's own definition starts is of the kind `shell`, said in the kind's own words, so a
   rule holds over it by the act its kind is, never by a list of acts.
2. **The decision (`owner/src/actions.ts` `decideCommand`)**: the command decided as the run's
   call of each act of that kind, by `decideTool`'s own rule, now one private step both share
   (`#decideAs`: every act a rule never lets be first, then the first asked alone). The call's
   door is the definition's file (`mcp.json`, `hooks.json`), its request the entry (a server
   with its name, a hook with its place, `pre_tool[0]`), so the card shows the owner the command
   whole. The actor is the run's own (`runActor`, which `decidedActor` now uses too), so the card
   is bound to the run's conversation like any call of the run.
3. **The admission (`owner/src/launch.ts` `#ownCommands`)**: in the admission's unit, once
   `runs.admit` admitted a run that starts now, each command server and each hook is decided;
   one a rule holds is left out of the start, and the owner told once of each, and why
   (`definition_command_dropped`: the rule's words, or the card it waits on). A run retried or
   written waiting decides nothing, so it uses no yes (the run's own admission, when its turn
   comes, decides); a later refusal of the unit takes the cards back with it. A server reached
   by its URL is no command (the egress holds it, D4), and a command server is bound to no
   connector, so leaving one out changes no grant.
4. **A yes resumes the conversation, whose next run starts the command once.** The card's yes
   writes the resume run as for any held call (`r5-resume-hooks.md`); that run's admission finds
   the same call (door, agent, client, conversation, entry) approved and admits it, once. Its
   input (`resumeInput`) now says so for both kinds of held call: a call of the agent's own is
   made again; a command of its definition already runs.
5. **The honest note** (`ruleNotes`): a rule on an act of `COMMAND_KIND` holds at `definitions`,
   at every engine, Codex's included, since the admission is before any engine.

## Departures from the brief

- **No guarded write (option b)**, for the reasons above.
- **Once a run, as `ask` means elsewhere.** An `ask` rule asks for each definition command at
  each run of the agent (one card a command and conversation while it waits). A rule names no
  command, so there is no standing yes for one command; an owner who wants an agent's servers and
  hooks to run names the rule's `agent` instead.

## Left, with why

- **A rule written mid-run holds from the agent's next run.** The commands are decided at the
  admission, as a run's own hook file is written at its start (`r5-hooks.md` item 3); the
  agent's own shell calls are decided at once, at the decide hook.
- **What a hook's or a server's program does** is the agent's file, which it may change after
  the admission, as a `Bash` call's script may change: a rule is on the act, not the command's
  text, so nothing decides by it.

## Owed

- [ ] live: at the dark redeploy, an agent with a stdio server and a hook under a `workspace.shell` `never` rule starts neither on each engine, and the owner's `definition_command_dropped` notice names each; on Claude Code, a skill's ``!`command` `` under its `allowed-tools` renders as the placeholder.
- [ ] Whether a skill or custom command of Gemini's, Codex's or pi's can run a command as it loads, with no tool call (Gemini's `.gemini/commands` `!{...}`, which no `native.refuses` names), researched first (`untangle/skill-commands`).

# skill-commands as built (2026-11-22)

`untangle/skill-commands`, owed by #5769's review (`definition-commands.md`, "Owed"): the owner's
`workspace.shell` rules hold over the agent's own tool calls (R5's decide hook) and over the
commands its `mcp.json` and `hooks.json` start (decided at admission, #5769), and Claude Code's
skills' inline `` !`cmd` `` is off while a rule needs the hook (#5769's review). Whether any other
engine runs a command from the agent's own files with no tool call the hook sees, researched
first (Fact 14), each from its pinned version's own source or docs.

## Research first (Fact 14), read 2026-11-22

Sources: Gemini CLI 0.66.0's npm bundle (`bundle/*.js`, each part headed by its source path) and
its `docs/`; Codex `rust-v0.166.0` (`codex-rs/`); pi 1.4.0's npm package (`dist/`, `docs/`);
Claude Code 2.1.309's linux-x64 binary (its embedded script). Only the paths an agent's own
folder can hold count: a run's home is made fresh by root from the engine's store (its secret
and session paths alone, `session.ts` `makeHome`), and a resumed CLI is given the runner's own
`CONTINUE_PROMPT`, never a `/` command, so nothing the agent writes into the home mid-run is
ever loaded as a command.

| Engine | Path in the agent's folder | Runs a command with no tool call the hook sees? | Closed by |
|---|---|---|---|
| Claude Code | a skill's or custom command's `` !`cmd` `` and ```` ```! ```` blocks (`.claude/skills`, `.claude/commands`, plugin) | yes, as it renders | the managed `disableSkillShellExecution` (#5769): each becomes `[shell command execution disabled by policy]` while a rule needs the hook; it covers skills, synced skills, commands and plugins, and only a `policySettings` skill (root's `/etc`) is exempt |
| Claude Code | a skill served by an MCP server (a main agent's load of the door's skills) | no: a skill loaded from MCP never runs its inline commands | the engine itself |
| Gemini | a custom command's `!{...}` (`.gemini/commands/*.toml`) | **yes**: a prompt starting with `/` runs it in a non-interactive run (`nonInteractiveCliCommands.ts` `handleSlashCommand`); `shellProcessor.ts` asks only the policy engine (`run_shell_command`), which yolo allows (`policies/yolo.toml`, `toolName = "*"`), and runs it by `ShellExecutionService`, with no tool call and no `BeforeTool` hook. Its docs say it asks to confirm; under yolo the code does not | **refused**: `.gemini/commands` in `native.refuses`. No setting turns the injection off (`FileCommandLoader.ts` adds the processor whenever the prompt holds `!{`), and folder trust, which skips project commands, also takes yolo away |
| Gemini | a subagent's own servers (`.gemini/agents/*.md` frontmatter `mcp_servers`, a stdio `command`) | **yes**: started with the subagent (`agentLoader.ts`; the registry loads project agents when folder trust is off) | **refused**: `.gemini/agents` in `native.refuses` |
| Gemini | a skill (`activate_skill`) | no: its body is returned as text (`tools/activate-skill.ts`) | |
| Gemini | `@file` in `AGENTS.md` | no: file reads (`memoryImportProcessor.ts`) | |
| Gemini | a workspace policy's external safety checker (`.gemini/policies`) | no: an external checker resolves only built-ins, and there are none (`safety/registry.ts`) | |
| Codex | a skill's `SKILL.md` | no: `skills/src/invocation.rs` only recognises the model's own shell reads of a skill | |
| Codex | a skill's own MCP server (`agents/openai.yaml` `dependencies.tools`, `type: mcp`, `transport: stdio`, `command`) | **yes, on a first-party client**: when the prompt names the skill, `core/src/mcp_skill_dependencies.rs` writes the server into `$CODEX_HOME/config.toml` and starts it, auto-approved under `approval_policy = "never"` with full access; feature `skill_mcp_dependency_install`, stable, on by default. `codex exec` (originator `codex_exec`) is not first-party today, and nothing the agent writes moves it (`$CODEX_HOME/.env` drops `CODEX_` keys, the run's environment is the allow-list), but the code says "for now" | **off**: `-c features.skill_mcp_dependency_install=false` in `native.argv`, the engine's own switch, on every run |
| Codex | an agent role (`.codex/agents/*.toml`) | **yes**: a whole config layer for the subagent (`agent-roles/src/loader.rs` reads `<layer>/agents` for every layer, a bare project `.codex/` included; `core/src/agent/role.rs` applies it unsanitised), so its `mcp_servers` start with the subagent (`multi_agent`, stable, on by default), spawned by a tool call no shell kind names | **refused**: `.codex/agents` in `native.refuses` |
| Codex | custom prompts (`$CODEX_HOME/prompts`) | no: the TUI's only (`tui/src/bottom_pane/custom_prompt_view.rs`); `codex exec` expands none | |
| Codex | `.codex/rules` | no: execpolicy rules allow or forbid a command line, and run none | |
| pi | a prompt template (`/name`) | no: positional substitutions only (`docs/prompt-templates.md`); `.pi/prompts` is refused anyway (D2) | |
| pi | a skill (`--skill`, `/skill:name`) | no: `_expandSkillCommand` reads `SKILL.md` as text (`core/agent-session.js`) | |
| pi | a `!cmd` prompt | no: the interactive mode's only (`modes/interactive/interactive-mode.js`); JSON mode hands the text to `session.prompt` | |
| pi | an extension | in-process code | refused (D2: `.pi/extensions`; `--no-extensions` while hooked) |

A main agent runs neither the agent's files nor its skills' commands in the sandbox: what its own
engine does with a skill it loads from the door is its own (`acts.json` `unseen.main_agent`),
and Claude Code's runs none.

**The options, for each path found.** (a) The engine's own switch, as data: a managed setting
or flag that turns the path off (Claude Code's setting, Codex's feature). (b) Route the command
through R5's hook: no engine offers it. Gemini's injection asks the policy engine, not its
hooks, and a subagent's or role's servers start with no call at all. (c) Refuse the run loudly
while the agent's folder holds the file, as D2 refuses engine-native settings, with the owner
told the path (`engine_settings_refused`). **Chosen:** (a) where the engine has the switch,
(c) where it has none. A custom command, a subagent and an agent role are each the engine's
own layer beside the one definition (Direction 2): what they do goes in a skill, `mcp.json`
or `hooks.json`, where every engine reads it and the admission decides its commands.

## What landed

1. **Gemini's record** `definition.native.refuses` gains `.gemini/commands` and
   `.gemini/agents`. Its `cite` is now `packages/core/src/config/storage.ts`, the file that names
   every project path it refuses.
2. **Codex's record** `definition.native.refuses` gains `.codex/agents`, and `native.argv` is
   `-c features.skill_mcp_dependency_install=false`, added on every run.
3. **The owner's words** (`doors/app/view.js`): `engine_settings_refused` says the path is the
   engine's own (its settings, commands, subagents or extensions), not "its settings" alone.
   `DefinitionNative`'s comment and the record check's message say the same.
4. **Tests** (`sandbox/runner/runner.test.ts`): a run is refused, before its CLI, by the folder,
   with a Gemini custom command, a Gemini subagent or a Codex agent role in the agent's folder
   (beside pi's extension, now one table); every Codex run, attended or not, carries the switch.

## Departures from the brief

- **Subagent layers beyond skills and commands.** The research found two more paths of the same
  kind (a Gemini subagent's and a Codex agent role's own servers), each closed the same way.
- **The policy note gains no entry.** Every path found is closed on every engine, whatever the
  rules (Gemini's and Codex's on every run, Claude Code's whenever a rule needs the hook, which
  is whenever a shell rule holds). Where shell rules do not reach stays what `ruleNotes` already
  names: `main_agent` and `sandbox_programs` (`acts.json` `unseen`), each engine whose hook the
  agent can alter (Codex's), and `<engine>.fails_open`. A refused run tells the owner its path.

## Left, with why

- **Claude Code's `.claude/commands` and `.claude/workflows`** stay unrefused: neither starts a
  command no hook sees while a rule holds (a command's inline shell is the placeholder; a
  workflow runs only as the model's own `Workflow` call, whose script API is `agent()`,
  `parallel()`, `pipeline()`, `phase()` and `log()`).
- **Codex's plugins** come from the config layers' `[plugins]` and the vendor account's own
  installs; a repository's `.agents/plugins/marketplace.json` only lists them. Neither is the
  agent's folder.

## Owed

- [ ] live: at the dark redeploy, a Codex run starts with `-c features.skill_mcp_dependency_install=false` and no unknown-feature warning, and a Gemini run whose agent's folder holds `.gemini/commands` is refused with the owner shown its path.

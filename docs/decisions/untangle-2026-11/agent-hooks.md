# agent-hooks as built (2026-11-22)

`untangle/agent-hooks`, slice D5 of `agent_definition_research.md` §4.4: the agent's one hook
definition (`hooks.json`, D1's `agentHooks`) runs on every engine through one runner-owned
dispatcher, placed in the root-owned layer R5's decide hook is in (`r5-hooks.md` items 3, 6, 13,
14). It is runner protocol 4's second deploy; the expand is `untangle/runner-protocol-4`
(`runner-protocol-4.md`), merged first, as D4 was with protocol 3.

## Research first (Fact 14), read 2026-11-22

What the research left open, from each pinned version's own docs or source:

- **Claude Code 2.1.309** (code.claude.com/docs/en/hooks): a command hook takes `shell`
  (`bash` by default) and runs in Claude Code's environment; matching hooks run in parallel; a
  matcher omitted matches every event; exit 2 blocks `PreToolUse` and `UserPromptSubmit` (its
  stderr the reason) and also refuses a `Stop`, blocks a `PreCompact` and shows stderr on a
  `PostToolUse`; `hookSpecificOutput.additionalContext`, under the event's `hookEventName`,
  adds context on `UserPromptSubmit` and `SessionStart`; a hook that cannot start, times out
  or exits another code is a non-blocking error unless its entry sets `onFailure: "block"`
  (v2.1.295 on), which then does what exit 2 does on the event: it blocks a call or a prompt,
  does nothing on a `Stop`, and would block a `PreCompact` (review, read 2026-11-22).
- **Codex CLI 0.166.0** (`codex-rs/hooks/src/events/*.rs`, `engine/output_parser.rs`,
  `schema.rs`, `config/src/hook_config.rs`, `features/src/lib.rs`): managed hooks are the
  requirements file's `[[hooks.<Event>]]` groups, each matcher optional; exit 2 with stderr
  blocks `PreToolUse` and `UserPromptSubmit` and refuses a `Stop` or a `PostToolUse` result;
  `hookSpecificOutput.additionalContext` (plain stdout too, on `UserPromptSubmit`) adds
  context; `features.hooks` is on by default; every command runs through `$SHELL -lc`.
- **Gemini CLI 0.66.0** (`docs/hooks/reference.md`, `packages/core/src/hooks/types.ts`,
  `hookPlanner.ts`): only command hooks; stdout must be JSON; exit 2 blocks `BeforeTool` and
  `BeforeAgent` but hides an `AfterTool` result and retries an `AfterAgent`;
  `hookSpecificOutput.additionalContext` (its types name `hookEventName` too) adds context on
  `BeforeAgent` and `SessionStart`; hooks of one event run in parallel unless one is
  `sequential`; the default timeout is 60 000 ms. A hook with an empty stdout has its stderr
  read as its answer, JSON included, whatever its exit (`hookRunner.ts`), and no setting makes a
  hook that fails block (review, read 2026-11-22).
- **pi 1.4.0** (`docs/extensions.md`, `dist/core/extensions/types.d.ts`): `tool_call` blocks
  by `{block, reason}`, and a handler that throws blocks too; `input` answers `continue`,
  `transform` or `handled` (consumed, never run); context goes in as `before_agent_start`'s
  `message`; handlers run in load order; `ctx.sessionManager.getSessionId()`.

So **one answer shape serves every engine**: exit 2 and the reason on stderr blocks, exit 0
and `hookSpecificOutput.additionalContext` under the native `hookEventName` adds context, exit
0 and nothing is no decision. The research's per-record `hooks.out` words would be one value
copied four times, so the dispatcher writes that one shape (as R5 item 2 did for the helper),
and pi's template maps it onto its own events.

## What landed

1. **The format says what each event takes (`HOOK_EVENTS`)**: `block` on `pre_tool` and
   `prompt`, `context` on `prompt` and `session_start`, nothing on the rest, so no engine's own
   reading of an answer there (a stop refused, a compaction blocked, a tool result replaced) is
   ever reached. A hook with no `timeout_s` runs `HOOK_TIMEOUT_S` (60 s, as each record gives
   its decide hook); D1's "the engine's own default" had no meaning behind a dispatcher.
2. **The admission reads `hooks.json` whole beside `mcp.json` (`launch.ts` `readAgent`)**:
   absent, no hooks; not `agentHooks`'s shape, `agent_file_invalid`, so the run is refused. The
   start carries them (`SandboxStart.hooks`), and the sandbox Worker maps them into the start
   document's `hooks` (`control.ts`, protocol 4).
3. **Each record's `definition.hooks` gains the dispatcher's place** (`sources/src/schema.ts`
   `DefinitionHooks`): `file` (the decide hook's file, its only shared one, or another of the
   run's own under `{system}`), `template` and `each` (each event's entry, `{event}` and
   `{native}` in it, `{dispatcher}` its command line), `argv` (a run's own file loaded), `runs`
   (a shell, or `exec` for the record's own `.mjs`), `fails`, `alterable`. The checker holds
   each settings part read whole once filled, with no credential key; an `.mjs` template
   carrying `{dispatcher_argv}` and `{hook_events}` and naming each event's own name; argv only
   for a run's own file; the placeholders only in that file, `{event}`, `{native}` and
   `{on_failure}` only in `each`. Codex's `[features] hooks = true` moved from the decide template to the hooks
   template, the part its shared file always holds, so the table is written once.
4. **The rendering (`engine.ts`)**: every run whose record has a decide hook gets
   `{system}/hooks.json` (`runHooks`: each hook's argv, its timeout, its `match` as the
   anchored pattern of the CLI's own names by `toolPatterns`, the agent's directory, the
   kinds of the CLI's tools). The dispatcher's entries go in the hook file after the decide
   hook's (several parts of one file joined: JSON merged with its lists one after the other,
   any other text one after the other). A file every run shares (Claude Code's managed
   settings, Codex's requirements) carries every event, since any run's agent may have a hook
   there, and is now always written; a run's own (Gemini's system settings, pi's `-e
   {system}/hooks.mjs`, loaded after `decide.mjs`) carries the events its agent has hooks on,
   and nothing without one.
5. **One matcher rule (`contract/src/acts.ts` `toolPatterns`)**: the CLI's own names of a
   kind, and a `TOOL_MATCHER`'s pattern, moved out of `owner/src/actions.ts`, so a rule's tools
   and an agent hook's match by one rule.
6. **The dispatcher (`sandbox/runner/hooks.ts`, `ta-runner hooks <event>`, protocol 4's
   `hooks`)**: it finds its run by its group (the one root-owned directory under `/etc/ta/runs`
   of its gid, `withRunUser`'s, which removes any other of that group a dead runner left), reads
   the run's file, runs each hook the call matches in
   parallel through `spawnBounded` (the session's one way to start a process), as the same
   user, in the agent's directory, with `{event, engine, session_id, tool?, prompt?}` on stdin,
   each stopped past its timeout or past `HOOK_ANSWER_BYTES` of answer (1 MiB, a technical
   ceiling: the answer is read whole, and no engine keeps more than 10 000 characters of
   context). A hook's exit 2 or `{"block"}` blocks where the event takes one; `{"context"}`
   adds context where it takes one; every other field (`allow`, `updatedInput`, `decision`,
   `tool_input`, any engine's words) is dropped, since the dispatcher writes only its own
   shapes, each block's reason after its hook's label, so its stderr is never JSON, which
   Gemini would read as an answer (review). A hook that fails on a blocking event blocks, with
   why (Fact 8); elsewhere the
   dispatcher exits 1, which every engine shows as a hook's error.
7. **The start check (`runner.ts` `hooksCheck`)**: where the agent has a hook, `hooks check`
   is run as the CLI runs the dispatcher (its shell, or by argv), as the run's user, in the
   CLI's environment, before the CLI: a dispatcher that cannot read the run's file, or a hook
   whose program is not there, fails the run loudly (`ta.runner_error`). R5's start check and
   this one share one runner of a check (`checkAs`).
8. **The owner's note (`actions.ts` `hookNote`, `policyRead`'s `hooks`)**, `ruleNotes`'
   neighbour: the agents' own hooks run at each engine with the layer (`holds_at`), never for a
   main agent (`main_agent`), not held at Codex (its dispatcher runs through the login shell
   the agent writes, `alterable`), and, mid-run, let a call through where a dispatcher that
   cannot start fails open (`gemini.fails_open`). Claude Code's entries set `onFailure` by the
   event (`{on_failure}`: `block` on a call or a prompt, else `continue`, so no compaction is
   blocked), so there a dispatcher that cannot start or runs past 600 s blocks (review).
9. **Runner protocol 4**: the runner speaks 4, the Worker 3 and 4. With 2 no longer spoken,
   the sign-in document's `region` goes from `SigninDoc` and `signinDocument`: what
   `runner-protocol-3.md` owed to `untangle/runner-protocol-3-contract`, done here.

## R5's deny always wins

The dispatcher's answer is never an allow or a rewrite, so whatever an agent's hook says, the
decide hook beside it decides the call: on Claude Code `deny` outranks no decision; on Codex
any block wins and no other `updatedInput` exists; on Gemini and pi nothing merges a changed
input (the dispatcher's stdout is only its context, its stderr never JSON). The runner test holds both halves on every engine: the decide hook's entries are unchanged
beside the dispatcher's, and a silent hook's dispatch is no decision.

## Departures from the research

- **No `hooks.out` words in the records**: one shape serves every engine (above).
- **The dispatcher runs every hook of an event in parallel**, as Claude Code and Gemini run
  their own; nothing in the format orders them.
- **A shared hook file carries every event, so every Claude Code and Codex run pays one
  dispatcher start (about 65 ms on this host) per hooked event and tool call**, an agent with no
  hooks too: a file one sandbox's runs share cannot hold one run's events, and writing only
  the union of live runs' events would have to be ordered against R5's generation-ordered
  writes. A run's own file (Gemini, pi) carries only its agent's events.
- **A hook's timeout past what the engine gives the dispatcher's entry** (600 s on Claude
  Code and Codex, 600 000 ms on Gemini, none on pi; Codex's `SessionEnd` is held to 3 s and
  Claude Code's to 60 s whatever the entry says) is cut by the engine: a vendor's limit, and on
  a blocking event the call goes on where the engine fails open (Gemini, Codex), as the note
  says; Claude Code blocks it (`onFailure`).
- **A blocked prompt on pi is `handled`**: pi runs none of it, and its JSON mode carries no
  reason; the owed check below says what the owner sees.

## Left, with why

- **Codex is `alterable` for the dispatcher too**, as for R5's hook: a hook that starts no
  login shell is R5's owed `untangle/r5-hooks-codex`, which this follows.
- **An agent may write its own `hooks.json` and `mcp.json`** (every agent has full power over
  the owner's definitions by default, `files.ts` `ownersTree`), so a rule on `workspace.shell`
  does not hold over the commands those files run at the next run; only a rule on
  `workspace.files` does. D4 has the same shape. Owed below, researched first; since done:
  each such command is decided at its run's admission (`definition-commands.md`).
- **The `unsupported` forms of `decide` and `definition.hooks`** have no record since Kimi went
  (#5745); they stay as the shape of an engine with no root-owned layer (R5 item 8).

## Owed

- [ ] live: at the dark redeploy, an agent's `pre_tool` hook that blocks holds on each engine (Claude Code's managed command hook, Codex's requirements hook, Gemini's system-settings hook, pi's `-e` extension), on a hooked run and an unhooked one, and a `prompt` and a `session_start` hook's context reaches the model on each.
- [ ] live: at the dark redeploy, Gemini's and pi's loading order puts R5's hook first: Gemini's `BeforeTool` list holds R5's entry before the dispatcher's, and pi's `-e decide.mjs` runs before `-e hooks.mjs`, so a call R5 holds files its card whatever the agent's hook says.
- [ ] live: at the dark redeploy, what a run shows when pi's `input` handler consumes a blocked prompt (`handled`); if nothing, the template sends the reason as a message too.
- [x] `untangle/definition-commands`: whether a rule on `workspace.shell` should hold over the commands the agent's own definition files run (`mcp.json` stdio servers, `hooks.json`), researched first. Done: it does, decided at each run's admission (`definition-commands.md`).
- [ ] `untangle/hooks-shared-cost`: a shared hook file (Claude Code, Codex) carries the dispatcher only once a run of the sandbox has an agent hook, written under `writeShared`'s lock and kept while the sandbox lives, so an owner whose agents have none pays no dispatcher start (about 65 ms here) per event; today every run pays it on every event, two per tool call.

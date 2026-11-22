# hooks-shared-cost as built (2026-11-22)

`untangle/hooks-shared-cost`, owed by D5 (`agent-hooks.md`, #5765): every Claude Code and Codex
run wrote the dispatcher on every event into the hook file all the sandbox's runs share, so
each run started the dispatcher (about 65 ms) on every event, two per tool call, its agent
having hooks or not.

## Research first (Fact 14), read 2026-11-22

- **Claude Code 2.1.309** (the pinned binary's own code, and code.claude.com's `settings`,
  `settings-reference`, `managed-settings`, `hooks`, `plugins/loading`): managed hooks come
  only from `/etc/claude-code/managed-settings.json` and `managed-settings.d/` beside it, a
  fixed path every run of the sandbox reads. Under R5's `allowManagedHooksOnly`, a run's own
  `--settings` file's hooks and a `--plugin-dir` plugin's are blocked; only a plugin managed
  settings force-enable by its `name@marketplace` runs, and a `--plugin-dir` copy of a name
  managed settings lock is not loaded. So no root-owned hook file of one run's own exists there.
- **Codex 0.166.0** (`codex-rs/config/src/loader/mod.rs`, `hooks/src/engine/discovery.rs`,
  `hooks/src/config_rules.rs`): managed hooks come only from `/etc/codex/requirements.toml`
  and the system layer `/etc/codex/config.toml`, fixed paths. A run's own `-c` hooks are the
  session-flags layer, not managed, so they need a trust hash and are switched by hook state
  the user layer sets, in `$CODEX_HOME`, which the agent writes.
- **The 65 ms** (this host, `--cpu-prof`): Node's own start is about 17 ms; the rest is the
  318 KB runner bundle's load, Node's built-in modules it imports and the records' checks at
  import (`served()`). Before Node starts, the engine has already started a shell for the
  entry (`shell: bash`; Codex's `$SHELL -lc`), about 2 ms.

So the shared file stays shared, and the run's own decision moves before Node: the shell the
engine already starts reads it.

## What landed

1. **The run's own hooks are one root-owned file an event** (`engine.ts` `hooksFile`,
   `{system}/hooks.<event>.json`), written only for the events its agent has hooks on, in the
   run's directory before its CLI starts (`withRunUser`). No file there is no hook on it, for
   the dispatcher as for the shell, so the fact is held once.
2. **The dispatcher's command line checks it first** (`dispatcherCommand`, `{dispatcher}`):
   by the shell's builtins alone, it finds the one run directory of its group (`[ -G ]`, as
   `hooks.ts` `ownRun` does; only root makes one) and, where that holds no file of the event's,
   exits 0, no decision, having started nothing. Anywhere else, the file there or no one such
   directory, it starts the dispatcher, which reads the file, or fails loudly and on a
   blocking event holds the call, as before. It is a shell function called with the event, so
   the records' `{dispatcher} {event}` is unchanged; pi's `{dispatcher_argv}` takes no shell
   and loads only its agent's events already.
3. **The dispatcher reads only its event's file** (`eventHooks`), none there answering nothing;
   `hooks check` reads each one there.
4. **Measured in the runner test's box** (median of 21 entries each, as the CLI runs them, an
   event with no hook): Claude Code's 53 ms to 1.7 ms; Codex's 56 ms to 5 ms (its login
   shell's own start). An event with a hook pays what it did.

## R5's and D5's guarantees

- The shared file's text, and R5's entries in it, are unchanged but for the command line; R5's
  deny wins as before, and the dispatcher's answers are unchanged.
- The skip reads only kernel and root facts: the shell's group, and a directory and files root
  wrote whole before the CLI started. No variable or file the agent writes decides it, and the
  agent cannot add or remove a file there. On Codex the login shell stays `alterable`, as
  before.
- It fails closed: only a positive finding (one run directory, searchable, without the event's
  file) skips; no directory, two, or an unreadable one starts the dispatcher, which blocks a
  call or a prompt it cannot read hooks for. The runner test holds both, and that an event
  with no hook starts no Node (run in an address space too small for one).

## Departures from the owed line

- **Not the union of live runs' events, kept while the sandbox lives**: that left a run with
  no hooks paying for another run's agent, and needed the shared file's writes ordered against
  R5's generations. The check per run costs a shell's start whatever the other runs have.

## Left, with why

- **A run with hooks on an event still pays the dispatcher's whole start there** (about 53 ms
  in the box), most of it the runner bundle's load, not Node's. Owed below.
- **No per-run root-owned hook file on Claude Code or Codex**: neither pinned version reads a
  managed hook from a path a run can name (research above); a vendor's limit.
- **The start check (`runner.ts` `hooksCheck`) runs the dispatcher's `check` past the shell's
  skip**, by its argv through the CLI's shell: through `dispatcherCommand` it would skip, no
  event being `check`. The skip's own text is held by the runner test's box test, on `bash -c`
  and on `sh -lc`, as each CLI runs it.

## Owed

- [x] `untangle/runner-start-cost` (done, `runner-start-cost.md`: a hook program of its own, about 8 ms past Node): the runner's start for its internal hook commands (`hooks`, `decide`), about 50 ms past Node's own here, most of it loading every built-in module the bundle imports and checking every record at import (`served()`), so a hooked event or a decided call pays only what it uses; researched first.

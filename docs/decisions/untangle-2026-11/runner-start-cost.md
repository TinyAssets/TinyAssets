# runner-start-cost as built (2026-11-22)

`untangle/runner-start-cost`, owed by `hooks-shared-cost.md`: a run's CLI starts the runner
(`node runner.mjs hooks <event>`, `node runner.mjs decide <door>`) on each event its agent has
a hook on and on each R5-checked call on Codex, Gemini and pi, and each start paid about 46 ms
past Node's own before it did anything.

## Research first (Fact 14), measured 2026-11-22

On this host (4 cores, Node v22.22.0, the image's version), the runner built by its own build
(`build/bundle.mjs`), each command spawned 41 times after 3 warm-ups, wall time, median and
p95 in ms. "Start" is a command that loads the bundle and exits at once (`nosuch`), so it is
the load alone.

| what starts | median | p95 |
| --- | --- | --- |
| `node -e 0` (Node's own start) | 18.2 | 21.7 |
| an empty `.mjs` | 20.4 | 21.2 |
| the runner (`runner.mjs`, 320 KB) | 63.8 | 68.1 |

Where the 46 ms goes (`--cpu-prof`, and each part taken out alone):

- **The records' checks at import, about 20 ms**: `session.ts`'s `RECORDS = served()` checks
  every CLI record whole (`sourceRecord`) when the bundle loads, whatever the command; the
  runner with that line taken out starts in 44-46 ms. `checkedTogether` is 0.2 ms; the checks
  of each record are the cost (12 ms cold, 5 ms warm).
- **Built-in modules, about 18 ms**: the bundle imports `child_process`, `crypto`, `fs`,
  `net`, `path`, `readline` and `sqlite`; imported from ES modules they cost 18 ms past an
  empty `.mjs`, the same set by `require` from a CommonJS file 11 ms. The dispatcher and the
  helper use `child_process`, `fs`, `path` and `crypto` alone.
- **ES module loading, about 7 ms**: the same small bundle starts in 34 ms as `.mjs` and 27-28
  ms as `.cjs` (Node's ES loader, and each built-in's ES facade).
- **The bundle's own compile, 2-6 ms**: 320 KB against 35-90 KB; code that is bundled but
  never run costs little (a bundle of the hook commands carrying the records' code unrun
  starts within 1 ms of one without it).

The options, each measured as the hook commands would start:

| option | median | p95 | what it costs |
| --- | --- | --- | --- |
| nothing | 63.8 | 68.1 | every hooked event and decided call pays 46 ms past Node |
| the records checked when read, one bundle | 44-46 | 51-58 | one change at the source |
| a hook program of its own, `.mjs` | 34.5 | 43.1 | a second file beside the runner |
| a hook program of its own, `.cjs` | 27.8 | 30.0 | the same |
| Node's compile cache (`NODE_COMPILE_CACHE`) on the runner | 62.5 | 63.6 | a cache root must write |
| the compile cache on the `.cjs` program | 27-28 | 30 | the same, nothing gained |
| a startup snapshot of the `.cjs` program | 22.6 | 26.6 | a 5.9 MB blob per digest |
| Node flags (`--v8-pool-size=0`, `--jitless`, `--no-opt`, `--max-semi-space-size`, `--disable-warning`) | 27-28 | 28-40 | nothing gained |

- **Node's compile cache** caches only user code's compile (2-4 ms on the runner, nothing on a
  small program), not the built-ins or the records' checks, and is read from a directory
  named by a variable or written at first use: the cache must be root's, written by root, or
  the agent could plant compiled code in an enforcer. Not taken: nothing to gain.
- **A startup snapshot** (`--build-snapshot`) saves 4-5 ms more, but Node 22 warns that
  `child_process` is a module it does not support in a snapshot and that state "may be out of
  sync", and the 5.9 MB blob would be built per runner digest by the same Node binary. Not
  taken: unsupported, for 4 ms.
- **A persistent root-owned dispatcher** that each hook command talks to over a socket would
  cut the start to the client's: 2-5 ms. But the client cannot be Node (that is the start
  being cut), bash reaches no Unix socket, and no other client is in the image (an image
  change); a TCP loopback port names no caller (no peer credential), so the daemon would
  have to find its run some other way; the agent's tool input, today parsed by a process of
  the run's own user, would be parsed by root's; and it would need its own life across
  pause, resume and reboot (`recover`), each a way for a block-capable hook to fail open on
  Codex and Gemini, which do not fail closed when a hook cannot answer. R5's deny would still
  win, but for 25 ms more than the `.cjs` program, every one of those is new surface. Not
  taken.
- **One bundle, the records checked when read** (no `RECORDS` at import) is needed whatever
  else is done: the dispatcher's own module graph reaches `session.ts` for `spawnBounded`,
  the one place a process is started (the template guard). Alone it leaves 26 ms past Node:
  the built-ins the hook commands never use and the ES loader.
- **A hook program of its own** (`hooks.cjs`, from an entry of the hook commands alone)
  leaves 9-10 ms past Node: the built-ins it uses. It must be root's and named by the
  runner's digest, as `runner.mjs` is. The loader (`template/ta-runner`) installs one file,
  and teaching it a second, or shipping a third bundle beside the runner and the probes,
  changes the image and the pair. Built into the runner's own bytes instead, and written by
  the runner (root) beside itself at each run's start, before any file of the run names it,
  it needs no image change: the digest that names the runner names the program in it, and
  the run's start checks (`decide check`, `hooks check`) run it as the CLI will, so a program
  that is not there fails the run loudly.

So the choice is both: the records are checked when read, and the hook commands are a small
CommonJS program of their own, built into the runner and written beside it.

## Checked against the intent

- **Root-owned values only**: the program is the runner's bytes, written by root under
  `/opt/ta/runner/<digest>/` (root's, 0755) as root's 0644, as the loader installs
  `runner.mjs`; it reads what the dispatcher and helper read today, nothing new. Its command
  line is the runner's own (`--no-warnings`, the file, the command), in the same root-owned
  hook files.
- **Fail loudly**: no program there fails each run's start check; a program that cannot
  start mid-run is what a dispatcher that cannot start was before, unchanged per engine.
- **One home**: the program's path is one function (`protocol.ts` `hookProgram`), its bytes
  one build (`build/bundle.mjs`), its commands one list (`HOOK_COMMANDS`), each command one
  handler (in the program, no longer in `runner.mjs`).
- **No shim**: `runner.mjs` no longer takes `decide` or `hooks`; nothing forwards between
  them.

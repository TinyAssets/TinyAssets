## Why

PLAN.md Scoping Rule 1 now says the universe IS its agent's harness, project
folder and workspace (PR #3970, pi.dev as the reference shape). Today the
served universe agent cannot touch its own folder at all: every file and shell
tool is denied, because own-files access was "deferred to an OS sandbox"
(`universe_intelligence.py`). PR #3958 supplies that sandbox for provider
launches. This change is slice S1 of the design in
`design-notes/universe-harness-design.md`: prove the shape by giving the
agent exactly four tools over its OWN folder, inside the jail, and letting a
skill file it writes change what it does next turn.

It is Tier 2: it adds authority (an agent that can write and execute inside a
universe) and it changes the served tool surface.

## What Changes

- Four platform-executed tools, `read`, `write`, `edit` and `bash`, over the
  universe folder mounted at `/u`. The platform runs them; no vendor CLI
  built-in is enabled (Hard Rule 3). They are served as engine-MCP handles on
  the per-universe engine route, so `claude -p`, `codex exec` and the
  OpenAI-compatible HTTP loop all see the same four definitions (~330 tokens).
- A tool jail built by the same `provider_jail.jail_argv` as a provider
  launch, narrower: the universe at `/u` and nothing else of `/data`, its
  root read-only with only the agent-owned brain files and harness dirs
  writable; every hidden root entry (credential vault, `.runtime/`, consent and
  usage DBs) masked; no network (no `--share-net`); empty environment; no
  credential snapshot; a seccomp filter refusing `symlink`/`mknod`, because
  the daemon reads the folder from outside the jail and follows links.
- Per-call, per-universe resource limits, fail-closed: `prlimit` inside the
  jail (address space, processes, cpu, file size, open files, core), a wall
  clock, an output cap, a process-tree count and memory watch, a free-space
  floor on the shared disk, and lock-file slots per universe and per host.
- The founder turn that has the tools gets a short harness section in its
  system prompt: the four tools, the folder map, and the skill index (name and
  one-line description of each `skills/<name>/SKILL.md`, read fresh each turn;
  the body is read on demand).
- Every hidden directory at the universe root except `.runtime/` is masked in
  every provider launch view, so a CLI's own project settings dir (`.claude/`,
  or any future CLI's) is never a loading mechanism (design risk 8, decided
  here without naming a vendor).
- The claude engine-route config (it carries the route bearer) moves from the
  universe root into `.runtime/`.

Nothing is removed: the existing ten engine handles stay (removal is S8). No
top-level handle is added to the public connector.

## Capabilities

### New Capabilities

- `universe-harness`: the universe agent's own-folder tools, their jail and
  limits, and the skill index.

### Modified Capabilities

None. The provider-launch jail change (hidden root dirs masked) is recorded
in the new capability because it exists for the harness.

## Impact

`tinyassets/universe_tools.py` and `tinyassets/universe_files.py` (new, the
latter the one safe reader every daemon-side universe read now routes through:
`universe_intelligence`, `universe_soul`, `universe_self_model`, `persona`),
`engine_mcp_server.py`, `served_tools.py`, `providers/provider_jail.py`,
`providers/claude_provider.py`, `node_sandbox.py` (one shared process-tree
walk), `.github/workflows/linux-jail-proof.yml`. No storage schema, credential,
migration or money change. Linux only, like the #3958 jail; a Windows or macOS
tray refuses the tools (fail closed) and the skill index is empty there.

Owner: Claude. Branch: `claude/harness-s1-four-tools`, based on #3958. One PR.
Cross-family (Codex) review is owed before landing.

# agent-mcp as built (2026-11-22)

`untangle/agent-mcp`, slice D4 of `agent_definition_research.md` §4.4, with §5 question 3 and §6
B5, and the coordinator's call (`next_builders.md`, "Agent definition"): the agent's own MCP
servers get their credentials the connector way, so `mcp.json` lists URLs and commands only, and
a credential-shaped field is refused (D1's `agentMcp`; review added a URL's query parameter
and an argument's option to the variable it refused by name, `credentialShaped`). The credentials
themselves are C1's (`untangle/connector-credentials`), not this slice's.

## Research first (Fact 14), read 2026-11-21 and 2026-11-22

What the research left open, from each pinned version's own source or docs:

- **Claude Code 2.1.309** (code.claude.com/docs/en/mcp): an `--mcp-config` entry is
  `{"type": "stdio", "command", "args", "env"}` or `{"type": "http", "url", "headers"}`; an entry
  with a `url` and no `type` is read as stdio and skipped, so the record's `http` entry keeps its
  `type`. `--strict-mcp-config` loads only the servers passed.
- **Codex CLI 0.166.0** (`codex-rs/utils/cli/src/config_override.rs`,
  `codex-rs/config/src/mcp_types.rs`): `-c key=value` splits on the first `=`, trims the value,
  parses it as TOML (`_x_ = <value>`), and when that fails takes the raw text with its quotes
  stripped, silently. So every value goes as JSON, which TOML reads as its own for a string or a
  list of strings, a map goes one pair an entry (`.env.<NAME>=`), and a value TOML cannot carry
  (a lone surrogate, a control character) must never reach it: D1's `literal` now refuses every
  character `isPrintable` refuses, not only NUL. A server is `command` (`args`, `env`) or `url`.
- **Gemini CLI 0.66.0** (`docs/tools/mcp-server.md`, `packages/cli/src/config/config.ts`,
  `settings.ts`): `httpUrl` is streamable HTTP (`url` is SSE); `command`, `args`, `env`. The
  allowed servers are `argv.allowedMcpServerNames ?? ` the intersection of every settings layer's
  `mcp.allowed`, so both name every server. `--allowed-mcp-server-names` is `type: "array",
  nargs: 1`, comma-split: one flag per server, repeated and joined (`--allowed-mcp-server-names=<name>`),
  parsed by its pinned yargs 17.7.2 to `["tinyassets", "-y", "docs"]`; bare, a server named `-y`
  is read as `--yolo` (review, against the pinned 0.66.0 bundle's option and yargs 17.7.2).
- **pi 1.4.0** (`docs/mcp.md` in the npm package): `command`, `args`, `env` or `url` (streamable
  HTTP; `type` optional, SSE refused), `exposure`; `${VAR}` and `!command` values, which
  `literal` already refuses.

## What landed

1. **One MCP block a record, the run's door one of its servers (`sources/src/schema.ts` `Mcp`).**
   D1 put the agent's servers' entries in `cli.definition.mcp` beside `cli.mcp`, the door's, and
   the checker held the two to each other. They are one rendering, so they are one block:
   `cli.mcp` holds `servers` (the dotted key), `file` (JSON) or `flag`, the `http` and `stdio`
   entries, `template` (what else the file holds), `names` (Gemini's `mcp.allowed`), `argv`,
   `argv_each` (`{name}` a server's name: Gemini's `--allowed-mcp-server-names`) and `trust`
   (a note). `definition.mcp` and `trust.argv` are gone. The door is an `http` server like any
   other: no record hand-writes its entry, and `{mcp_url}` is no placeholder any more. Claude
   Code's template keeps only the door's own header (`x-ta-run: {run_secret}`), merged over its
   entry; Gemini's keeps its other settings; pi's file holds the servers alone.
   - **`Mcp` has no `unsupported` form.** Every engine's run takes the run's door; Kimi, the one
     record that had none for the agent's servers, is gone (#5745).
   - The checker: a file or a flag, one of the two; a template and names only with a file, and
     a template holding no server but the door's own keys and nothing at `names`; `argv_each`
     carrying `{name}` joined to its option, as `{prompt}` is (`NAME_ARG`), and `{name}` nowhere else; the entries as D1 checked them.
2. **The owner's admission (`owner/src/launch.ts`, `owner/src/network.ts` `agentServers`).**
   `readAgent` reads `mcp.json` whole beside `agent.json` (absent: no servers; not UTF-8, or not
   `agentMcp`'s shape: `agent_file_invalid`, so a server named as the door, `tinyassets`, refuses
   the run). The admission, in the unit that makes the run's network, keeps each server the
   sandbox's network lets out by `networkDocument`'s own rule: a command always (its traffic is
   the sandbox's, held to the network like any other); a URL when its host is a name
   (`checkDestination`: never an IP address), on 80 or 443 (the only ports E2B matches a name on),
   and, in `allowlist` mode, a destination's host on one of them. The start carries the rest
   (`SandboxStart.mcp_servers`), and the owner is told once of each left out, and why
   (`mcp_server_dropped`, `notices.once`).
   - **Fact 8 on the way:** `#whole` refused nothing where a folder stood at the path, so a
     folder named `mcp.json` (or `model-ranking.json`) read as no file. It is refused
     (`invalid`, "<path> is not a file"), so the queued `untangle/ranking-path-dir` finding is
     fixed here.
3. **The runner (`sandbox/runner/engine.ts`).** The run's servers are its door (`urls.mcp`, or
   `urls.mcp_bare` where the template sends the secret as a header), then the start's
   `mcp_servers`, each the record's entry for its transport, its own values in their places:
   into the record's file under `servers` (every name on `names`'s list), or as the flag's pairs,
   and `argv_each` once a server. A start naming a server `tinyassets` fails loudly.
4. **Runner protocol 3 (`protocol.json`, frozen by digest).** Protocol 2 plus the start
   document's `mcp_servers` (each server by its URL, or its command, args and env), less the
   sign-in document's `region`, which `drop-kimi.md` owed to protocol 3: one record carries both,
   since a record is frozen once recorded. The runner speaks 3, and the Worker 2 and 3
   (`SPEAKS`); each document the Worker writes holds every field a protocol it speaks reads
   (`protocol.ts`), so a protocol 2 runner reads the start (it ignores `mcp_servers`) and a
   protocol 3 runner the sign-in (it ignores the null `region`). The sandbox Worker's test holds
   each document to the joined shape of the protocols it speaks (`spokenShape`), and the fake E2B's
   runner speaks the runner's own protocol unless a test scripts another, not a stale 1.
   - **Two deploys (`protocol.ts`).** The expand, a Worker speaking 1, 2 and 3 beside protocol 2's
     runner, with protocol 3's record exactly as recorded here, is `untangle/runner-protocol-3`
     (`runner-protocol-3.md`, which says what the rule and its guard require); this branch is the
     second deploy, merged on top of it. As for P-hooks (`r5-hooks.md` item 5), the order matters
     only to a rollback past this deploy, which the dark platform has none to make. The contract
     step, `region` gone from `SigninDoc` and `signinDocument` once the Worker no longer speaks 2,
     is `untangle/runner-protocol-3-contract`.

## Departures from the research

- **The owner's notice, not a run's `ta.notice` event.** The admission decides which servers
  are left out, so it tells the owner there, once a state, as `auto`'s unranked model is told.
  A run event would have the start document carry the dropped list only for the runner to echo
  it back, and no runner event type is added.
- **No notice for a command that installs at its start** (`npx`, `uvx` needing its registry on
  the allowlist). It would name commands in code; the server's own failure to start says it.
- **The servers live in `cli.mcp`, not `cli.definition.mcp`** (above): the research's table put
  them in the record's `mcp` too ("`mcp.servers` templates, merged with the run door").

## Owed

- [x] `untangle/runner-protocol-3`: protocol 3's expand deploy (the Worker speaking 1, 2 and 3 beside protocol 2's runner, this record as it is), merged before this branch. Done (#5757).
- [x] `untangle/runner-protocol-3-contract`: once no runner speaks 2, `region` dropped from `SigninDoc` and `signinDocument` (`drop-kimi.md`). Done in `untangle/agent-hooks` (`agent-hooks.md` item 9).
- [x] `untangle/connector-credentials`: credentials for the agent's own MCP servers the connector way (`packages_connectors_research.md` C1): egress injects each server's sealed token, and no credential is ever in `mcp.json` or the sandbox. Done for keys (`connector-credentials.md`); OAuth is `untangle/connector-oauth`.
- [ ] live: at the dark redeploy, B5: one stdio server and one http server on the allowlist connect on each engine (Claude Code's `--mcp-config`, Codex's `-c` pairs, Gemini's system settings with `--allowed-mcp-server-names` repeated, pi's own `mcp.json`) and their tools are callable, and a host not on the list never reaches the CLI (the owner's `mcp_server_dropped` notice says so).

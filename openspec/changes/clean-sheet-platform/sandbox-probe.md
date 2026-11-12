# Slice S: the sandbox, probed live on E2B (2026-11-03)

Raw HTTP to E2B's platform API and envd, as `platform/sandbox/src/e2b.ts` speaks them (no SDK
on the path), from scripts kept outside the repo. Every sandbox was created with the metadata
`ta_probe=slice-s`, killed, and the account listed at the end: **0 running or paused**. Usage
was well under one sandbox-hour (most of it a 2 vCPU / 512 MiB base sandbox or the 2 vCPU /
1 GiB template, for minutes each), about $0.10 at E2B's published rates. The template builds
(then `template/build.py`, now `image.py`) are kept: the last, `tinyassets-sandbox:s41230244835b286a`, is the one
`E2B_TEMPLATE` names.

## Network (G4's owed list)

| Probe | Result |
|---|---|
| Transform on `/r/<run>/<secret>/mcp` and `/r/<run>/events` (HTTPS) | Both get the rule's headers; a header of the same name the VM sent is **replaced**; other headers (`x-ta-run`) pass. The VM sees no token in its environment or files. |
| Transform on plain HTTP (port 80) | **Not applied**: E2B sets transform headers on HTTPS only. |
| Transform readability | The token is readable back from `GET /sandboxes/{id}` (`network.rules`): the API key has its custody, as G4 found. |
| `/etc/hosts` redirect | With the transformed host mapped to another IP in `/etc/hosts`, the request still reached the real host with the header: E2B's proxy resolves the name itself (SNI), so the VM cannot carry the token elsewhere. |
| Private CIDRs | `10.0.0.1`, `172.16.0.1`, `192.168.1.1`, `100.64.0.1`: blocked. `169.254.169.254` answers `401` (the VM's own metadata service, inside the VM). |
| A name not allowed | Blocked. IP-literal HTTPS: blocked, except to an allowed DNS server: `https://1.1.1.1/dns-query` answered (DoH), confirming `BOUNDARY_NOTE`. |
| DNS | `/etc/resolv.conf` is `8.8.8.8` at boot whatever `dnsServers` says; with `dnsServers: ["1.1.1.1"]` nothing resolves until the runner's `prepare` pins it. UDP 53 to `8.8.8.8` and `9.9.9.9`: no answer; `1.1.1.1`: answers. |
| IPv6 | Link-local only; `curl -6` fails. No IPv6 egress. |
| Network update (`PUT /sandboxes/{id}/network`) | Applies at once to the running sandbox (a newly allowed name answered `200`). |
| WebSocket through the transforming proxy | `101 Switching Protocols` and an echoed frame (`ws.postman-echo.com`, a rule on it). The POST batches stay the path; a socket is possible later. |
| Node's TLS through the proxy | Once failed `UNABLE_TO_VERIFY_LEAF_SIGNATURE` (template 1), once verified (template 3); the runner and every run carry `NODE_EXTRA_CA_CERTS` (E2B's CA, `RUN_BASICS`), so a Node engine's calls to the egress door verify either way. Owed: why it varied. |

## Lifecycle and the runtime ceiling (the founder's rule)

| Probe | Result |
|---|---|
| Create (base, template) | 246 ms and 1,078 ms to the API's answer. |
| Pause / resume a 1 GiB template sandbox with a live run | 462 ms / 587 ms. |
| Renewal across a forced 45 s window | The pause and immediate resume moved `startedAt` to the resume and `endAt` to resume + timeout: **the window resets**. A ticking process kept its pid and count through it. |
| The window let lapse | E2B auto-paused the sandbox at its timeout (`pauseMode: full`); a resume found the process alive. |
| A stream dropped from envd | The process kept running. |
| Resume after a long pause | **75 minutes paused**: resume to first byte 589 ms; the process alive, 10 MB file intact. Days remain unmeasured. |
| Resume after days paused | The long-pause rows come from `e2b-pause-probe.yml` (`platform/sandbox/pause-probe.ts`): 14 and 30 days, with and without about 5 GB of scratch. |
| Renewal with the real runner and a live run (template 1) | `ta-runner renewing` 252 ms (its `ta.renewing` frame posted, posts held), pause, resume, `ta-runner renewed`: frames went on, the run ended `exit_code 0` with all 30 of its events. |
| `X-Total-Running` | The list answers E2B's own running count in one call: the concurrency check's source. |
| Credit and spend | E2B's public API has no balance or spend endpoint (team metrics give concurrency only): the credit alert is owed. |

## The template

| Probe | Result |
|---|---|
| Pinned CLIs | claude 2.1.309, codex 0.166.0, gemini 0.66.0, kimi 2.4.1, pi 1.4.0, Node v22.22.0, installed from the committed locks. |
| `--version` as the engine's user | claude 540 ms, codex 44 ms, gemini 1,718 ms, kimi 1,437 ms, pi 361 ms. |
| Peak RSS of a not-signed-in run (all its processes) | claude 226 MB, gemini 354 MB, codex 172 MB: 1 GiB holds one run of each, not two of Gemini beside the rest. Real turns, owed. |
| Users | (First round: one uid an engine; replaced, review H-4.) The run pool: 1,000 users `ta-run-<n>` (`RUNS_CEILING`), each its own group and `ta`; `ta-sync` reads the runner bundle, which holds no secret. |
| `sudo` | **E2B's provisioning puts its default `user` back in `sudo` and adds `user ALL=(ALL:ALL) NOPASSWD: ALL` to `/etc/sudoers` at each boot**, though the build removed both (its own check passed). `prepare` now takes both away at every boot and resume and checks: `user`, `ta-sync` and `ta-run-0` each "not allowed to run sudo". |
| hidepid | `mount -o remount,hidepid=2 /proc` works as root; an engine user sees its own processes only. |
| **World-writable system files** | E2B's base image boots with `/usr/local/bin` (the runner's Node), `/usr/bin/envd`, `/etc/inittab` and systemd units writable by every user, and a template build's `chmod` does not survive into the sandbox. The runner's `prepare` takes every user's write away (once a boot, about 7 s) and fails loudly if any remains; verified after it and across a pause. |
| Config files | The run's MCP config is root's, read-only to the CLI (an append failed). |

## The review round (2026-11-03, templates `s2470aee6d75344fc`, `s04af25e74977d042` and `sfbd5920361a47279`; 5 throwaway sandboxes, under 2 sandbox-minutes)

| Probe | Result |
|---|---|
| `secure: true` on the Worker's create | The answer carries `envdAccessToken`. envd refuses a process start **without** it (`401`) and with another token (`401`), and takes the right one (`200`). |
| The list by metadata, both states | `GET /v2/sandboxes?metadata=ta_owner%3D<tag>&state=running,paused` answered exactly the tagged sandbox: the erasure's stray search. `X-Total-Running` present (`1`). |
| `ta-runner prepare` on the built template | Exit 0: `/proc` hidden, nothing writable by another user left, `sudo` taken away; `ta-runner state` answers `{live: 0, quiet: true}`. |
| Modes across a memory pause and resume | Nothing writable by another user (others, or group `ta`) right after the resume, before any `prepare`: **E2B did not loosen modes on a memory resume** (`pauseMode: full`). `prepare` re-checks at every resume regardless. |
| The verification's follow-ups (template `s04af25e74977d042`: `sudoers` kept with no include) | The same results again: envd 401/401/200, `prepare` exit 0 at the boot and after a memory resume, nothing writable, no user but root may `sudo`. |
| Owed to the founder's live test | A Gemini run loading its `{system}` settings with no "Parent directory is not owned by root" warning, yolo kept (needs a run past the sign-in; the runner test checks every ancestor is root's as the CLI sees it). |
| Clean-up | Each sandbox killed; the account listed **0 running or paused** after. |

## Sign-in up to its prompt (slice SIGNIN, 2026-11-03, templates `se0d866e5d827bf05` and `s6dec61a38e839d37`; 4 throwaway sandboxes, under 10 sandbox-minutes)

The template build's standing check (then `build.py`'s `check_logins`, now the probes'
`login-probe`, run by `template/checks.ts`): each record's login run by
`ta-runner login-probe` as a pool user on a terminal (`script`), stopped at its prompt, no
account used.

| Record (region) | Result on `s6dec61a38e839d37` |
|---|---|
| claude-code | ok: a URL on `claude.com`, no code; its redirect is not localhost. |
| codex | ok: a URL on `auth.openai.com` and a device code. On `se0d866e5d827bf05` it failed: Codex refuses a `CODEX_HOME` that does not exist ("CODEX_HOME points to ... but that path does not exist"), so a first sign-in (an empty store) showed nothing. The runner now makes each directory the record's `home_env` names in every home. |
| gemini | ok: a URL on `accounts.google.com` (`NO_BROWSER`), no code. |
| kimi (global) | ok: a URL on `www.kimi.ai` and a device code. |
| kimi (mainland-cn) | ok: a URL on `www.kimi.com` and a device code. |

Every sandbox killed; the account listed **0 running** after. A completed sign-in on each
vendor needs the founder's accounts (owed below).

**The review round (template `s7a0747eb6136d488`; 2 throwaway sandboxes, under 6
sandbox-minutes).** Each record's whole sign-in URL was read off its pinned CLI as a pool user
under `script` (Claude, Codex, both Kimi regions), and Gemini's from its pinned CLI's own code
(`authWithUserCode`, its URL shown only with its settings file in the home), to pin each page
as data (`cli.login.url`: hosts, path, fixed and free parameters). Claude's:
`claude.com/cai/oauth/authorize`, `client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e`,
`redirect_uri=https://platform.claude.com/oauth/code/callback`, its scope fixed, the PKCE
challenge and state free. Codex's: `auth.openai.com/codex/device`, no parameter. Kimi's:
`www.kimi.ai` or `www.kimi.com` by region, `/code/authorize_device?user_code=...`. Then the
build's standing check ran each login under the production egress allowlist (the egress door
alone, widened by that engine's own hosts, all else denied, the resolver pinned): all five ok
against the pinned pages (claude.com, auth.openai.com with a code, accounts.google.com,
www.kimi.ai and www.kimi.com with a code). Every sandbox killed; 0 running after.

The verification round's fixes (the runner's store lock and generation) changed the runner, so
the template was built again as `s354d7998f07439db`: its standing check passed all five the same
way (1 throwaway sandbox, under 3 sandbox-minutes, 0 running after).

## The route probe (Codex's base URL build, 2026-11-03, templates `sb3da938719c968b4` and `s97c42c9c1b38e998`)

`build.py`'s `route_probe` after every build (now the probes' `route-probe`, one engine and
provider a step, run by `template/checks.ts`): a throwaway sandbox of the template, `strace`
installed while its network is open, then every outbound address denied but the pinned
resolver; `ta-runner route-probe` runs every keyed engine on every provider it keys as a run
runs it (the real `invocation`, `runEnv` and `sandboxEnv`, an API-key run's home, a pool
user), its door on a loopback capture server at the real path
(`/r/<run>/<secret>/c/<connection>/...`, `/r/<run>/c/...` with `x-ta-run` for Claude Code),
a fake sign-in seeded in each of the engine store's `secret_paths`.

- On `sb3da938719c968b4` every engine passed but Claude Code: its first non-MCP requests
  were its MCP client's OAuth discovery (`/.well-known/oauth-protected-resource`, `/register`)
  of the MCP door, which the capture server had answered 401. Its model calls
  (`POST .../c/<connection>/v1/messages`) were on the door. The capture server now answers
  the MCP door 404 (no server: no discovery), and the template was built again.
- On `s97c42c9c1b38e998` all eleven pass: Claude Code (anthropic), **Codex (openai, the
  dotted `-c model_providers.tinyassets.*` form: `POST .../c/<connection>/v1/responses`, no
  WebSocket GET)**, Gemini CLI (google, `.../c/<connection>/v1beta/models/...`) and pi on
  anthropic, cerebras, google, groq, mistral, openai, openrouter and xai, each first model
  request under the door's path with the run's placeholder key and secret, **no connect
  outside** loopback and the resolver, and no request carrying the fake sign-in. The sign-in
  check passed all five logins on both templates.
- 4 throwaway sandboxes in all (2 builds' sign-in checks and route probes), under 20
  sandbox-minutes; every sandbox killed, the account listing 0 running after.

### Real turns (review round 3, template `sad436b51fe608258`)

The capture server now answers each model request with a minimal valid reply in its sealed
API's wire format, and the probe runs two turns per engine and provider: a new conversation,
then its resume from a home made again from the store; Codex compacting first
(`model_auto_compact_token_limit=1`, its record's `cli.compaction`). Every request of both
turns is judged.

- Two development runs on the earlier template with the new runner bundle put in place
  (2 sandboxes, about 4 sandbox-minutes): all routes passed but Gemini CLI, whose model
  router asks `gemini-3.5-flash-lite` for JSON by a schema and failed on plain text (exit 1,
  four retries); the reply now gives a schema's smallest object. Then all eleven passed.
- The build of `sad436b51fe608258`: all five logins ok; all eleven routes ok, each with both
  turns exiting 0 with their final text, the second resumed; **Codex compacted** (its compact
  prompt's request on the door), every request under the door's path with the run's key and
  secret, no connect outside, no fake sign-in bytes. Its record is in
  `sandbox/template/passed.json`, which the template guard holds the pin to.
- Every sandbox killed; the account listed 0 running after.

### A resume proven by its history (review round 4, template `sf26c8eb4a2f657bf`)

The first turn's prompt carries a word of its own (`probe<hex>`); the resumed turn passes only
if its requests carry that word, so a CLI that silently starts a fresh conversation fails. A
development run on `sad436b51fe608258` with the new runner (1 sandbox, 2 sandbox-minutes),
then the build of `sf26c8eb4a2f657bf`: all five logins and all eleven routes pass, each
resume carrying the first turn's word, Codex compacting. Recorded in `passed.json`, pinned.
Every sandbox killed; 0 running after.

## Real CLIs, not signed in (the runner live, an in-sandbox doors stand-in)

The runner posted its sign-ins (`claude-code`, `codex`: `signed_in: false`), synced in, ran the
CLI as its user from the record, posted frames and synced the sessions out
(`/workspace/.sessions/owner/claude-code/.claude/projects/main/<sid>.jsonl`, pi's likewise).

- **Claude Code:** `system/init` with `session_id`, `permissionMode: auto`, `apiKeySource: none`,
  the MCP server `tinyassets` listed (`failed`: no door in the probe); then
  `error: "authentication_failed"` and exit 1. The record's matchers read the init.
- **Codex:** `thread.started` with `thread_id`, `turn.started`, then `error` events (its
  WebSocket to the vendor first, falling back to HTTPS); not signed in it **never exits**
  (it retries the network). The sandbox Worker's sign-in check refuses such a run first.
- **Gemini CLI:** **skips its system settings file** ("Parent directory ... is not owned by
  root"): it checks every ancestor up to `/`, so a settings file under the engine's own home
  can never load, and with it the MCP server, `context.fileName` and the update switch are
  lost; it also overrode `--approval-mode yolo` to `default` in an untrusted folder. Owed: a
  root-owned settings path outside `{home}`, which the records' checker does not yet admit,
  and folder trust. Not signed in it exits 41 on stderr (no event).
- **Kimi Code:** a `{"role": "meta", "type": "system.version"}` event, then "No model
  configured" on stderr, exit 1.
- **pi** (on a key, its base URL a stand-in): a `session` event with `id`, `agent_start`,
  `turn_start`, `message_start`; exit 0.

## Scratch and the disk (2026-11-03, template `sfbd5920361a47279`; one throwaway sandbox, about 4 sandbox-minutes)

Measured by `scripts/probes/sandboxprobe/probe.mjs` (the sandbox Worker's own E2B client), the sandbox killed after and listed gone by its tag.

- **The disk.** `/` is ext4 on `/dev/vda` (`relatime,discard`), 14,427,623,424 B, with 10,745,143,296 B free when made (the image takes 3.3 GB).
  - 3,788,096 inodes, 4 KiB blocks: one inode for every 3.8 KB, so a tree of small files runs out of bytes before inodes.
  - 78,150 blocks (320 MB) kept for root.
  - 2 vCPU and 1 GiB of memory.
- **Scratch written.** 6 GB of random bytes and 20,000 small files went to `/scratch` in 24 s; 4.3 GB was then free.
- **Resume with that disk.**
  - Pause with memory: 7.0 s.
  - After 60 s paused, `connect`: 881 ms, then the first command 460 ms, for 1.34 s in all.
  - The scratch was all there.
- **A full disk.**
  - A run user's write fails `No space left on device`, and `df` shows 0 free.
  - Root still wrote: ext4 keeps root's 320 MB. So the runner's own state survives a full disk, but the sync agent, its own user, does not. Hence the runner lets its reserve go before sync-out on a full disk (`runner.ts`).
  - envd answered at once while the disk was full (590 ms).
  - Deleting the fill recovered the disk.
- **Sync memory** (measured here, not in the sandbox: the same code):
  - before streaming, a sync of a 489 MB tree (100 × 5 MB, 3,000 small files) grew the sync agent by 709 MB;
  - streaming, the same tree grew it by 169 MB, and a 966 MB tree by 180 MB.

  On the template's 1 GiB, the old sync would have died at about that size; the new one stays bounded.
- **Owed:**
  - the resume after hours paused with 5–10 GB of scratch;
  - a clone of a 500 MB repository by a real CLI with its sync-out, in the sandbox;
  - `scripts/linux_oracle.py` for the scratch modes and the reserve (it runs the Python suite in a container; the runner's own root tests here cover the modes: `/scratch/<agent>` root and group `ta`, 2775).

## Owed to the founder's live test (each needs a model; TinyAssets provides none)

Every sign-in (`auth login` with a pasted code, `login --device-auth`, Gemini's `NO_BROWSER`,
Kimi's regions); the event shapes of real turns (final text, usage, tool calls, Kimi's and
pi's matchers); a resume of a real conversation (`resume_argv`, and Kimi's `--continue`), and
a renewal whose CLI's model call is cut by the pause; MCP reaching the run's door; an API-key
run's model calls through the egress door; CLI memory at real turns.

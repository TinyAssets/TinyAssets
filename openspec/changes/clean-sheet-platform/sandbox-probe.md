# Slice S: the sandbox, probed live on E2B (2026-11-03)

Raw HTTP to E2B's platform API and envd, as `platform/sandbox/src/e2b.ts` speaks them (no SDK
on the path), from scripts kept outside the repo. Every sandbox was created with the metadata
`ta_probe=slice-s`, killed, and the account listed at the end: **0 running or paused**. Usage
was well under one sandbox-hour (most of it a 2 vCPU / 512 MiB base sandbox or the 2 vCPU /
1 GiB template, for minutes each), about $0.10 at E2B's published rates. The template builds
(`template/build.py`) are kept: the last, `tinyassets-sandbox:s41230244835b286a`, is the one
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
| Renewal with the real runner and a live run (template 1) | `ta-runner renewing` 252 ms (its `ta.renewing` frame posted, posts held), pause, resume, `ta-runner renewed`: frames went on, the run ended `exit_code 0` with all 30 of its events. |
| `X-Total-Running` | The list answers E2B's own running count in one call: the concurrency check's source. |
| Credit and spend | E2B's public API has no balance or spend endpoint (team metrics give concurrency only): the credit alert is owed. |

## The template

| Probe | Result |
|---|---|
| Pinned CLIs | claude 2.1.309, codex 0.166.0, gemini 0.66.0, kimi 2.4.1, pi 1.4.0, Node v22.22.0, installed from the committed locks. |
| `--version` as the engine's user | claude 540 ms, codex 44 ms, gemini 1,718 ms, kimi 1,437 ms, pi 361 ms. |
| Peak RSS of a not-signed-in run (all its processes) | claude 226 MB, gemini 354 MB, codex 172 MB: 1 GiB holds one run of each, not two of Gemini beside the rest. Real turns, owed. |
| Users | Each engine its own uid in group `ta`, home 0700; **E2B's `useradd` put them in `sudo`**, the build takes them out (`sudo -n`: password required); the engine user cannot list `/var/lib/ta` or read the runner. |
| hidepid | `mount -o remount,hidepid=2 /proc` works as root; an engine user sees its own processes only. |
| **World-writable system files** | E2B's base image boots with `/usr/local/bin` (the runner's Node), `/usr/bin/envd`, `/etc/inittab` and systemd units writable by every user, and a template build's `chmod` does not survive into the sandbox. The runner's `prepare` takes every user's write away (once a boot, about 7 s) and fails loudly if any remains; verified after it and across a pause. |
| Config files | The run's MCP config is root's, read-only to the CLI (an append failed). |

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

## Owed to the founder's live test (each needs a model; TinyAssets provides none)

Every sign-in (`auth login` with a pasted code, `login --device-auth`, Gemini's `NO_BROWSER`,
Kimi's regions); the event shapes of real turns (final text, usage, tool calls, Kimi's and
pi's matchers); a resume of a real conversation (`resume_argv`, and Kimi's `--continue`), and
a renewal whose CLI's model call is cut by the pause; MCP reaching the run's door; an API-key
run's model calls through the egress door; CLI memory at real turns.

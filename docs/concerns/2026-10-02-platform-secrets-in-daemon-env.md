---
severity: P1
title: The daemon and its engine MCP children held the platform's own secrets
filed: '2026-10-02'
summary: the account-wide DigitalOcean token, the live Stripe key, the tunnel token and the WorkOS key are in the environment of the daemon and every engine MCP child; a daemon RCE or an env leak is the whole cloud account plus live billing. STILL LIVE ON MAIN — the fix is an env split written but unmerged in PR #4267 (security/platform-secrets-scope); the DO token also needs rotating by the founder.
---

# The daemon and its engine MCP children held the platform's own secrets

**Filed:** 2026-10-02.
**Cross-family review:** Codex (gpt-6-astra) round 1 ADAPT: the
`--restore-bundle` path re-converged the failing image, the line filter was not
Compose-exact, the process scan lost record boundaries and skipped unreadable
processes, a refused render left the source committed, and the repair command
was not installed. All folded in.
**Severity:** P1. One defect in the daemon (RCE, an exception or log line that
dumps the environment, `/proc/<pid>/environ` read by anything at uid 1001)
yields an **account-wide** DigitalOcean token and a **live** Stripe secret key:
every droplet, volume and DNS record, plus charges and refunds.

## Status on `main` — the finding is open, the fix is not merged

This file was authored on the fix's own branch, so until 2026-10-02 the only
record of a live P1 sat where a fresh checkout of `main` could not see it. It is
carried onto `main` for that reason. Everything below the next heading is the
original text, unedited; the front-matter summary and this section are the only
additions.

**Re-verified 2026-10-02 against `origin/main` `342ab4101`.** Both causes in
*Why it happened* are still present:

1. `deploy/compose.yml:184` still gives the daemon `env_file: /etc/tinyassets/env`,
   the box's whole secret store. `deploy/install-tinyassets-env.sh` on `main` has
   no `render-daemon-env`, so `/etc/tinyassets/daemon.env` does not exist.
2. `tinyassets/engine_mcp_http.py:270` is still `env = dict(os.environ)` inside
   `_EngineServer.start`.

So no part of *The fix* below is on `main`: no env split, no deploy-time scope
refusal, no `tinyassets-env` helper, no unit refusal. Read that section as the
**proposed** fix, which is written and reviewed on branch
`security/platform-secrets-scope` (PR #4267).

**When #4267 lands,** it carries its own copy of this file. Whoever lands it
should drop that copy and keep this one, deleting this status section — or adopt
its copy and delete this file. One file, either way; `git` holds the other.
Resolution is still what *How to resolve* / the verification block below says,
plus the founder's DO-token rotation.

## Source (verbatim)

> Found 2026-10-02 (read-only, names only): the prod daemon process
> (tinyassets-daemon, pids 1/7/12) and its engine_mcp_server children carry
> DO_API_TOKEN (an account-wide DigitalOcean token), a live STRIPE_SECRET_KEY,
> CLOUDFLARE_TUNNEL_TOKEN and WORKOS_API_KEY in their environment. Jailed
> provider children don't. Nothing under tinyassets/ or deploy/ on origin/main
> reads DO_API_TOKEN (the workflows use their own GitHub secret), so the copy in
> /etc/tinyassets/env looks unused.

## Why it happened (verified against the code, 2026-10-02)

Two independent causes:

1. **The daemon container loaded the box's whole secret store.**
   `deploy/compose.yml` gave the daemon `env_file: /etc/tinyassets/env`, the
   same file the systemd unit and `deploy_fail_safe.sh` pass as
   `--env-file` for interpolation. Everything the host or a sidecar needs —
   the tunnel token for `cloudflared`, the Better Stack token for `logs`, a
   hand-placed DO token — became daemon `Config.Env`, which Docker gives to PID
   1 (`tini`), the Python daemon, every descendant, and every `docker exec`
   (the healthcheck included).
2. **The engine MCP server copied the daemon's environment.**
   `tinyassets/engine_mcp_http.py` `_EngineServer.start` built the child env as
   `dict(os.environ)`. Provider children were already clean:
   `tinyassets/providers/base.py` `_provider_child_runtime_env` is an
   allowlist, and the stdio engine server inherits that.

Who reads what, by grep of `tinyassets/` (names only):

| Name | Read by | Belongs in |
|---|---|---|
| `DO_API_TOKEN` | nothing; workflows use the GitHub secret | nowhere on the box |
| `CLOUDFLARE_TUNNEL_TOKEN` | compose interpolation into `cloudflared` | tunnel only |
| `BETTERSTACK_SOURCE_TOKEN` | compose interpolation into `logs` | logs sidecar only |
| `SUPABASE_DB_URL`, `GITHUB_OAUTH_CLIENT_SECRET` | nothing | nowhere |
| `SUPABASE_SERVICE_ROLE_KEY` | `host_pool/client.py`, which nothing imports | nowhere |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `TINYASSETS_BILLING_ENTITLEMENT_KEY` | `billing/stripe_adapter.py`, called only by daemon HTTP routes in `onboarding/` | daemon process, no child |
| `WORKOS_API_KEY` | `account_deletion.py`, called only by an `onboarding/` route | daemon process, no child |

## The fix (branch `security/platform-secrets-scope`)

Least privilege by deploy config, nothing done by hand on the host:

- **The daemon loads `/etc/tinyassets/daemon.env`**, which is
  `/etc/tinyassets/env` minus `DAEMON_FORBIDDEN_ENV` (the first five rows above
  plus the Supabase key). `deploy/install-tinyassets-env.sh render-daemon-env`
  writes it with the source's owner and mode and reads the result back. It
  tracks where each dotenv value begins and ends (quotes, multi-line values,
  a BOM) and refuses what it cannot place exactly; on a Docker host it was
  checked against Compose's own parser on every edge case the review raised.
  Every `set` / `set-once` / `delete` of the source checks the render BEFORE
  committing the source, then re-renders, so the deploy (`set
  TINYASSETS_IMAGE`, the canary sync, `retire_platform_llm_logins.sh`,
  `apply-daemon-env`) keeps it current and cannot leave it stale. The tunnel still gets its token by
  interpolation from the untouched source, so the public surface is unchanged.
- **Engine MCP children get `platform_secrets.child_env(os.environ)`**, which
  also removes the Stripe and WorkOS names the daemon itself still needs.
- **The deploy refuses a daemon holding any forbidden name,** twice:
  `validate_bundle` rejects a staged compose whose daemon `env_file` lists the
  source, or whose rendered `environment` names a forbidden secret (before any
  install, prod untouched); and after the candidate is healthy,
  `daemon_env_scoped` reads `Config.Env` and the procfs environ of every
  container process from the host, keeping record boundaries. A hit, or any
  live process it cannot read, rolls the candidate back. Names only, never
  values. Rollbacks are exempt (the automatic one and `--restore-bundle`):
  they restore the previous state as it ran.
- **Every deploy installs the env helper as `/usr/local/sbin/tinyassets-env`**,
  which is what the unit's refusal message and `DEPLOY.md` tell an operator to
  run after a hand edit; the copy in `/tmp` does not outlive the run.
- **The entrypoint's empty-env sentinel gained `TINYASSETS_WIKI_CANARY_TOKEN`**,
  since the daemon no longer receives two of its three old sentinels.
- **The unit refuses a missing or stale `daemon.env`** (`DAEMON-ENV-UNREADABLE`,
  `DAEMON-ENV-STALE`) so a hand edit of the source cannot be silently ignored.
  This does not stop running containers: the unit has no `ExecStop`.
- `hetzner-bootstrap.sh`, `dr-drill.yml` and `DEPLOY.md`'s manual env-edit
  recipe render after editing the source.

Rollback: the bundle transaction restores the previous `compose.yml`, which
reads `/etc/tinyassets/env` as before; `daemon.env` left behind is inert.

## Founder action (not automatable, not done)

- **Rotate `DO_API_TOKEN`.** It sat in a long-running process environment. Then
  delete it from `/etc/tinyassets/env` (`sudo tinyassets-env delete
  DO_API_TOKEN`); nothing on the box reads it. Rotating also means updating
  the GitHub secret of the same name, which the workflows do use.
- **Consider replacing `STRIPE_SECRET_KEY` with a restricted key** (`rk_live_`)
  scoped to Checkout, Subscriptions and webhook reads. The daemon still holds
  it, by necessity; a restricted key bounds what a daemon compromise can do
  with it.

## Known residuals

- `STRIPE_*` and `WORKOS_API_KEY` remain in the daemon's `Config.Env`, so a
  `docker exec` into the daemon sees them. That process runs as the same uid
  as the daemon, which already holds them: no new exposure.
- `PID 1` is `tini`, started with `Config.Env`, so its environ matches the
  container config. The split removes the names from that config; the
  entrypoint's own strip list would not reach PID 1.
- `cloudflared` receives its token on the command line (`tunnel run --token
  ...`), visible to host `ps`. `TUNNEL_TOKEN` in its environment would not be.
  Host-only exposure; separate change.
- Daemon children launched without `env=` (`git_bridge.py`, `bid/node_bid.py`)
  inherit the daemon's environment, so the Stripe and WorkOS names. Platform git
  operations, not universe code, but a repository hook would run with them.
- An image-only deploy onto a box whose live compose still predates the split
  fails the scope check and rolls back. The next normal deploy carries the
  bundle and migrates it.
- procfs shows a process's environment as of its `execve`; a name a process
  sets on itself afterwards is invisible to the deploy check.

## Post-deploy verification

The deploy prints `daemon environment holds none of: ...` only after reading
the live container. Independently, on the host as root, names only:

    forbidden='DO_API_TOKEN|CLOUDFLARE_TUNNEL_TOKEN|BETTERSTACK_SOURCE_TOKEN|SUPABASE_DB_URL|SUPABASE_SERVICE_ROLE_KEY|GITHUB_OAUTH_CLIENT_SECRET'
    for p in $(docker top tinyassets-daemon -eo pid | tail -n +2); do
      tr '\0' '\n' < /proc/$p/environ | cut -d= -f1 | grep -xE "$forbidden" | sed "s/^/pid $p: /"
    done
    # engine MCP children additionally must not hold the Stripe or WorkOS names:
    for p in $(pgrep -f tinyassets.engine_mcp_server); do
      tr '\0' '\n' < /proc/$p/environ | cut -d= -f1 \
        | grep -xE "$forbidden|STRIPE_SECRET_KEY|STRIPE_WEBHOOK_SECRET|TINYASSETS_BILLING_ENTITLEMENT_KEY|WORKOS_API_KEY" \
        | sed "s/^/engine pid $p: /"
    done

Empty output from both loops is the pass. Pair it with
`python scripts/deployed_sha.py --assert-contains <merge sha>`.

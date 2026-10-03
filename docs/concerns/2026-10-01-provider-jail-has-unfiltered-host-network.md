---
severity: P2
title: Provider CLIs run on the container network with no egress floor
filed: '2026-10-01'
summary: 'The provider jail launches every provider CLI with bubblewrap `--share-net`, so the cross-user egress floor the tool jail gets (universe_egress) does not apply to them. Measured in production: a provider-jailed process reaches the daemon on loopback, other universes'' engine MCP ports (bearer-gated), DigitalOcean metadata, the host''s sshd and cloudflared metrics, and an unauthenticated log listener. No cross-user read was found. The floor gaps: no SMTP refusal and no connection caps.'
---

# Provider CLIs bypass the egress floor

**Filed:** 2026-10-01, from the NVIDIA OpenShell comparison
(`ta-scratch-lead/nvidia-sandbox-research.md`, recommendation 1). Claims pinned to
origin/main `21bae300`.

## The gap

`tinyassets/providers/provider_jail.py` `jail_argv` defaults to `share_net=True`,
and `confine_launch` keeps that default, so every provider CLI (claude, codex,
any future command adapter) runs on the daemon container's own network namespace.
The universe tool jail instead has an empty network namespace plus one unix
socket to the checking proxy in `tinyassets/universe_egress.py`. That proxy is
the cross-user floor: globally routable destinations only, resolved and pinned
by the proxy, SMTP ports refused, 32 connections per universe and 128 per
host. None of it applies to a provider CLI, and every provider CLI ships its
own shell tool.

## Measured reach (production, 2026-10-01)

Method: `jail_argv` itself (so `--share-net`) run inside the live
`tinyassets-daemon` container, making TCP connects and three small GETs.
Nothing was written.

| Target | Result | Gate |
|---|---|---|
| `127.0.0.1:8001` (daemon app) | open, 200 | Same routes and auth as the public tunnel. No code trusts loopback or forwarded headers (grep: `client.host`, `X-Forwarded-For`, `CF-Connecting-IP`, `cf-access` all absent) |
| `127.0.0.1:8790-8792` (engine MCP of other universes) | open | Per-server bearer, `hmac.compare_digest`, fails closed with no secret (`engine_mcp_server.py` `_BearerAuth`). Universe A's jail sees only A's own `.runtime` config; the route map and the servers' `/proc` are not visible |
| `169.254.169.254` (DO metadata) | 200 | None. `user-data` is 0 bytes, so it exposes droplet identity only |
| `172.18.0.1:22`, `:20241`, `:5355` (host sshd, cloudflared metrics, LLMNR) | open | sshd is key-only; the other two have no auth |
| `172.18.0.3:24224` (vector fluent-forward) | open | None: a provider can inject forged platform log lines |
| `::1`, host-loopback services, `172.18.0.1:25` | refused | - |

No cross-user read or write was found. What makes this P2:

- the floor is bypassed. No SMTP refusal means mail from the shared address can
  burn every user's reputation, and with no connection caps one universe can
  take the shared box's sockets;
- forged log lines undermine the platform's forensic record;
- defence in depth: one future loopback service without its own auth would be
  reachable from every provider jail.

## Fix

Route provider launches through `universe_egress`, the same as the tool jail:
`share_net=False`, bind the universe's egress socket, set the proxy environment,
and add a second pinned relay for the universe's OWN engine MCP port, because
claude reaches it on loopback and the floor refuses loopback by design. Each CLI
must be verified to honour `HTTPS_PROXY`; a CLI that does not is refused,
never silently exempted. A separate gap from the same comparison: the provider
jail loads no seccomp filter and sets no rlimits.

---
name: infra-ops
description: Drives domain, DNS, SSL, and Cloudflare/GoDaddy admin for the TinyAssets public surfaces. Use when changing DNS records, nameservers, Cloudflare Workers/routes/tokens, GoDaddy domain or Websites+Marketing settings, or doing emergency DNS routing.
---

# Infra Ops (Cloudflare + GoDaddy)

Operate the project's domain and edge infrastructure for the public surfaces
(`tinyassets.io`). Two providers, one skill; each has a detailed reference with
hard-won selectors, IPs, and gotchas — read the relevant one before acting.

## Shared browser-lock discipline

Both providers' dashboards run through the host's CDP Chrome
(`scripts/lead_browser.py`) and obey the shared browser lock and one-tab rule:

```bash
python scripts/user_sim_auth_hook.py          # expect 'no-browser' or 'approved'
python scripts/browser_lock.py acquire lead <provider>-<intent>
python scripts/browser_lock.py status          # owner=lead
# ... do the work ...
python scripts/browser_lock.py release lead     # always, even on failure
```

Never `--force` the lock unless you've verified the prior PID is dead. First use
of a provider in the Chrome-for-Testing profile hits SSO — ask the host to log in
once; the profile persists the session.

## Cloudflare

DNS/Worker/token operations. Platform Workers deploy only through **Platform
deploy** (`platform/deploy/checklist.md`); never `wrangler deploy` the old proxy
(`deploy/cloudflare-worker`) by hand (`tasks.md` §9 R1). Prefer the **API**
(`scripts/emergency_dns_flip.py` for DNS + Worker-route CRUD) over dashboard
automation — the dashboard is a one-time gate to mint the API token. The Custom
Token form uses react-select and is unreliable to automate (>5 tool calls without
a green run → hand to the host). Full token scopes, rotation cadence, Monaco/Radix
selectors, and dashboard gotchas: [references/cloudflare.md](references/cloudflare.md).

## GoDaddy

Domain portfolio, DNS (only when GoDaddy manages the nameservers), SSL, and
Websites+Marketing. No first-class API — navigate the admin UI via
`lead_browser.py`. Key facts: W+M origin IPs `76.223.105.230` + `13.248.243.5`
(apex A records) with `www` CNAME to the `.godaddysites.com` slug; **Cloudflare
proxy must be DNS-only (gray)** for those records or W+M SSL breaks; ignore stale
`*.godaddysites.com` prototypes — the real site binds to the custom domain. Known
URLs, DNS quirks, tour-popup dismissal, and pitfalls:
[references/godaddy.md](references/godaddy.md).

## When to stop automating

For one-time dashboard operations, if a script draft takes >5 tool calls without a
green run, release the lock and ask the host to finish manually (30 seconds by
hand). Reserve automation for repeated, API-driven operations.

## Verification

- [ ] Browser lock acquired before and released after any dashboard work
- [ ] DNS changes verified via `nslookup`/`curl -I`, not the UI's success toast
- [ ] Records pointing at W+M origins are DNS-only (no Cloudflare proxy)
- [ ] API-driven path used where a token exists; dashboard only for one-time gates

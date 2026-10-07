# Probe principals come from GitHub OIDC, not a shared secret

## Why

With no anonymous principal, the uptime probes need somebody to be. I gave them
a shared bearer, `TINYASSETS_WIKI_CANARY_TOKEN`, and made the deploy refuse
while it was unset. Only a person can write a repository secret, so a deploy
needed a human — which it never did before. The founder, 2026-09-03: *"i dont
understand why you cant deploy, i have never been needed for that."*

The obvious fix — make the probes anonymous again — was refuted (Codex design
review, 2026-09-03). "Platform-wide" is not "carries no user information":

- `_platform_last_activity_at()` is a timing side-channel for the founder's
  activity on a single-tenant deployment;
- the worker summary carried worker and runtime instance ids, counts, crash
  counters and a tenant count (narrowed to five fields the same day, but still
  tenant-derived);
- `has_work` says whether anybody has queued work;
- and the deep probes are not passive reads at all: `--assert-handles` forms an
  MCP session, and the wiki canary performs a persisted write/read round trip.

The other obvious fix — have the deploy mint the token and store it back as a
repository secret — is worse. `GITHUB_TOKEN` cannot administer secrets, so it
needs a PAT with `Secrets: write`: a standing, MORE powerful credential
introduced to rotate a narrower one.

## What changes

**Public, already done:** `/mcp/pulse` is the one unauthenticated read (git
sha, image tag, deploy time, uptime). The container's own healthcheck uses it,
so a container never depends on a credential to know it is serving.

**This change:** the probes that form a session or write get a short-lived
GitHub OIDC token instead of a standing bearer.

- The scheduled and deploy probe jobs get `id-token: write` and request a JWT
  with a TinyAssets-specific audience.
- The daemon validates GitHub's issuer and JWKS, then the exact repository id,
  `workflow_ref`, event, ref/environment, audience and expiry.
- A validated token binds a named probe principal, behind the existing
  request-shape allowlist, with the narrow authority the probes need and
  nothing else.
- `TINYASSETS_WIKI_CANARY_TOKEN` is deleted once the probes are green on OIDC.

## Impact

- No standing shared credential, so nothing to mint, rotate, or drift between
  the droplet and the repository.
- No human in the deploy.
- Probe actions stay attributable, which is what the founder's rule asks for.

## Interim

The bearer remains OPTIONAL: present, it works; absent, the deep probes skip
loudly and the deploy proceeds. A missing observability credential is not a
reason to leave production on an old image.

# Tasks

- [ ] Add a probe audience and mint the JWT in the probe workflows
      (`id-token: write`, `core.getIDToken(audience)`).
- [ ] Validate GitHub's issuer + JWKS in the daemon, with the key cache.
- [ ] Bind the claims that matter: repository id, `workflow_ref`, event,
      ref/environment, audience, expiry. Reject on any mismatch.
- [ ] Bind a named probe principal on a validated token, with the narrow
      authority the probes need and nothing more.
- [ ] Point `mcp_public_canary --assert-handles` and the wiki canary at it.
- [ ] Prove one green run of each probe on OIDC against production.
- [ ] Delete `TINYASSETS_WIKI_CANARY_TOKEN` from the workflows, the deploy sync
      step, `scripts/_canary_common.py` and the env catalog.
- [ ] Remove the interim "deep probes skip loudly" branch from `deploy-prod.yml`.

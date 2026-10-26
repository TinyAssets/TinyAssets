# Previewing & approving the site

This is the production source for `tinyassets.io`. These preview paths do not
change the live site. A push to `main` that changes site source
(`WebSite/site-react/**`, `WebSite/design-system/**`, `WebSite/shared/**`)
or `deploy-site-react.yml` runs that workflow. A manual run with
`confirm: deploy` is the rollback. See `../DEPLOY.md`.

## 1. Local hot-reload

```bash
cd WebSite/design-system && npm ci && npm run build   # first time, and after token edits
cd ../site-react
npm ci             # first time
npm run dev        # → http://localhost:3000
```

The dev server proxies `/mcp` → `https://tinyassets.io/mcp` server-side (no
CORS), but public site code supplies no bearer and does not use that route.
The commons list shows its labelled checked-in snapshot; live readings require
a signed-in connector.

## 2. Production-shaped static preview

```bash
npm run preview    # next build + serves the real static export at http://localhost:4322
```

No `/mcp` exists on localhost. The protected surfaces render the same labelled
snapshot and signed-in state as the production-shaped public site.

## 3. Tests and the rendered sweep

```bash
npm test                                  # node --test scripts/*.test.mjs
npm run build && python scripts/sweep.py  # every route + alias, phone + desktop
node scripts/snapshot-public.mjs          # refresh lib/mcp-snapshot.json from the live endpoint
```

`scripts/` holds:

- `canonical-mcp-contract.test.mjs`, `public-boundary.test.mjs`: the connector
  contract and the boundary the pages must keep (no public browser MCP request,
  snapshot provenance retained, no operator status in a browser).
- `snapshot-public.mjs`: the snapshot baker (public universe list only).
- `sweep.py`: the Playwright sweep (Python `playwright`).

## The approval loop

1. A change lands on a branch / PR (made by you or by an agent).
2. Reviewers use the local preview and the sweep screenshots.
3. On approval, merge to `main`. A merge that changes site source, or the
   deploy workflow, publishes through `deploy-site-react.yml`.
4. Complete the public canary and rendered-browser checks in `../DEPLOY.md`.
   Roll back by running that workflow with `confirm: deploy` on the last good
   revision.

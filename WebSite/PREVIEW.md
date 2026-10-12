# Website preview loop — read this first

`WebSite/site-react/` is the only site source for `tinyassets.io` (Next.js 14,
static export). The Svelte rollback tree was retired on 2026-09-02; there is
one tree to edit, preview, test and build.

## The preview commands

| Purpose | Command | URL |
|---|---|---|
| Hot-reload preview | `cd WebSite/site-react; npm run dev` | `http://localhost:3000/` |
| Production-exact static export | `cd WebSite/site-react; npm run preview` | `http://localhost:4322/` |

Run `npm ci` in `WebSite/design-system` (then `npm run build` there) and in
`WebSite/site-react` before the first preview. The site consumes the built
design system from `../design-system/dist`.

`npm run dev` still proxies same-origin `/mcp` for authenticated application
work, but public site code supplies no bearer and makes no MCP request. Both
preview modes show the checked-in snapshot and an explicit signed-in-connector
requirement for live readings.

## Edit order

1. Edit `WebSite/site-react/**` (tokens and shared vocabulary live in
   `WebSite/design-system/`; rebuild it after a token change).
2. Review the hot-reload preview at `http://localhost:3000/`.
3. `npm test` (authenticated-read boundary, preview trust boundary, public boundary).
4. `npm run build`, then `python scripts/sweep.py` for the rendered check
   (every route and alias, phone and desktop, zero console errors).
5. Open a pull request. Merging does not deploy.

## Hot reload

The Next development server pushes saved changes to every connected tab. If a
tab needs a manual refresh, treat that as a preview failure worth
investigating; fix syntax and compilation failures before relying on the
preview.

## Shipping

After review and merge, a push to `main` that changes
`WebSite/site-react/**`, `WebSite/design-system/**`, or `WebSite/shared/**`
runs `.github/workflows/deploy-site-react.yml`. A manual run with
`confirm: deploy` is the rollback. Follow `WebSite/DEPLOY.md`.

## Files involved

| File | Role |
|---|---|
| `WebSite/site-react/` | The site source (Next.js static export) |
| `WebSite/site-react/PREVIEW.md` | Local and hosted preview details |
| `WebSite/site-react/scripts/` | Node test suites, the snapshot baker, the Playwright sweep, the preview validators |
| `WebSite/design-system/` | Tokens, base styles, components (`@tiny/design-system`) |
| `WebSite/brand/` | The mark and its exporters |
| `WebSite/shared/mcp/public-read-contract.js` | Snapshot baker's public projection contract |
| `.github/workflows/deploy-site-react.yml` | Production deployment on site-source pushes to main, plus manual rollback |
| `WebSite/DEPLOY.md` | Deployment and verification playbook |

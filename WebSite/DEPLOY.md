# Deploy `tinyassets.io`

**Production source:** `WebSite/site-react/` (Next.js static export). A push
to `main` that changes site source runs `.github/workflows/deploy-site-react.yml`.
The paths are `WebSite/site-react/**`, `WebSite/design-system/**`,
`WebSite/shared/**`, and `.github/workflows/deploy-site-react.yml`, so a
change to the publisher itself publishes the current site. A manual run of
the same workflow, with `confirm` set to `deploy`, remains for a rollback or
a republish.

The Svelte rollback tree and its `deploy-site.yml` were retired on 2026-09-02.
Rolling back now means dispatching `deploy-site-react` on an earlier revision
of `main`.

## Before a production deployment

1. Build the design system, then the site:

   ```powershell
   cd WebSite/design-system
   npm ci
   npm run build
   cd ../site-react
   npm ci
   npm test
   npm run build
   ```

2. Run the rendered sweep over the export and read the screenshots:

   ```powershell
   python scripts/sweep.py --shots ../../out-shots
   ```

3. Check the live-data surfaces on `npm run dev` (which proxies `/mcp`): the
   commons list and the fine-print reachability strip must render readable
   records or an explicit, labelled empty or failed state after `Refresh MCP`.
4. Merge the reviewed pull request through the normal path.

## Publish

A merge that touches the paths above starts the workflow. For a rollback, or
to publish a revision those paths did not change:

1. Open GitHub Actions and select `deploy-site-react`.
2. Choose the revision.
3. Run the workflow with `confirm` set to `deploy`.

Confirm the build job produces `WebSite/site-react/out` and the deploy job
succeeds in the `github-pages` environment. Record the deployed revision. A
green merge that does not touch those paths, or a local build alone, is not
a shipped site.

## Verify production

```powershell
python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp
python scripts/deployed_sha.py --assert-contains <sha>
```

Then load `https://tinyassets.io/` in a browser and exercise the routes and
controls the deployment changed: rendered content, navigation, console errors,
and meaningful live data or explicit empty states. Deployment evidence is the
workflow result, the deployed revision, the public canary, and the rendered
browser result.

## Roll back

Dispatch `deploy-site-react` with `confirm: deploy` on the last good revision
of `main`, then re-run the canary and rendered checks. Fix forward on `main`
and redeploy.

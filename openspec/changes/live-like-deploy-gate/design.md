## G1. One driver, phase parity

One host-side driver, `deploy/deploy_host.sh <phase>`, holds every host step of
`deploy-prod.yml`. Each production step keeps its name, id, order, `if:`,
`timeout-minutes` and `environment: production`; its body is now
`bash deploy/deploy_host.sh <phase>`, given the values it already computed.
Authority stays inline in the caller: the DigitalOcean lookup, GHCR resolution,
WorkOS, environment secrets, incident issues.

`.github/workflows/live-like-gate.yml` runs the same phases, in the same order
and with the same wiring, on a GitHub runner prepared as a droplet-like host
(`DEPLOY_TARGET=local`). It binds no environment and reads no secret.
`tests/test_deploy_phase_parity.py` enforces the parity and names every
production step the gate does not run, with its reason.

## G2. The gate host runs the production branch live

The runner becomes a droplet-like host and the image production serves starts
live on synthetic data (owners, centers, SQLite writers, engine sockets, a
timer); the cutover then runs through the non-disposable code paths with a
root rclone `spaces` remote, never `BOX_MIGRATION_DISPOSABLE`.

## G4. Forced rollback leaves the host as the next attempt needs it

A fault after the one-shot window rolls back with no manual step: the old image
serving on the old layout, no leftover box-store `.img`/`.locks`, no candidate
`harness-<digest>`, and the next read-only preflight passes.

## G5. Synthetic production-shaped workload

`scripts/live_like/` builds the data (at least 3 owners and 5 centers, real
SQLite stores, sidecar state) and keeps it live (sockets, writer, timer). Its
shapes follow a redacted profile of production (`deploy/live_like/shape-profile.json`);
a store or file shape the profile carries but the fixture cannot build fails the
drift check (`profile.md`).

## Decisions (one line each)

- Packaging: a shell driver, not a composite action or reusable workflow; per-step `timeout-minutes` and environment binding stay in each caller's YAML.
- Transport: `DEPLOY_TARGET=local` runs the same remote command strings through `bash -c` and copies with `cp`; production keeps ssh/scp.
- Base image: the image production serves now (public `/app` build header -> GHCR tag), so the gate deploys onto the layout production actually has.
- Candidate image: built in the gate with the production build cache read-only, pushed by digest and untagged to the public daemon package (`ghcr.io/tinyassets/tinyassets-daemon`), the only repository the retention helper and fail-safe accept; no tag, so nothing that resolves tags can select it. A host-local registry failed the retention helper's `invalid_pull_reference`. Fork PRs get a read-only token and cannot run the gate.
- Caller inputs: synthetic per run (HMAC keys, canary bearer and door, tunnel token, app-ingress key, expected instance id), masked, never production values.
- Backup destination: D's MinIO behind a root rclone `spaces` remote at `/root/.config/rclone/rclone.conf`, the droplet's path and form; BACKUP_DEST is `spaces:<bucket>/workflow-backups`.
- Box store: the explicit loop image install-boxhostd-preflight.sh already accepts (`BOX_STORE_DEVICE=/var/lib/boxhostd/boxes.img`), set up with gVisor and idle boxhostd by the live revision's own installer, as its deploy left the droplet; the change's deploy upgrades them.
- Order: data before services, and the stack starts through the daemon unit, so the watchdog timers behave as on the droplet instead of racing a second `compose up`.
- uid 1001: the runner owns it, so `tinyassets` is a second name for 1001 and listed first in `/etc/group` (the unit's ExecStartPre checks `root:tinyassets`).
- Core canary: `CORE_CANARY_PROVISIONED=1` in the gate; the WorkOS sign-in is production-only and named as not exercised.
- Turn wait: `YIELD_WORKFLOWS` empty; nothing else mutates the gate host.
- Rollback case: a matrix case; the deploy step is `continue-on-error` there and post-deploy success-path steps carry `matrix.case == 'cutover'`, which the parity test strips before comparing.
- Trigger: `merge_group`, manual, and `pull_request` for this branch only; non-required until green.

## G3. Read-only live preflight

`live-preflight` runs before the first host mutation in both callers:
`scripts/live_preflight.py` refuses on leftover box-store `.img`/`.locks`, disk
above the retention low-water mark, special files outside the regenerated
runtime directories, and an unprovisioned canary without the waiver. It names
what to remove and removes nothing.

## Not exercised by the gate

- WorkOS core-canary sign-in and the core capability canary (production credentials).
- The Cloudflare tunnel's edge connection (a synthetic token keeps `cloudflared` running, unconnected) and the Access 403 check.
- The droplet's attached block volume (the loop store is the production fallback path).
- DigitalOcean metadata identity (synthetic expected instance file).

## Findings (recorded, not fixed here)

- Layout marker mode under 6aa7dd1: `tinyassets/storage_layout.py:149` rewrites `.layout.json` through `mkstemp`, so the marker lands 0600 owned by the writing uid (1001). A later start as root with every capability dropped cannot bypass file permissions, cannot read the marker, and crash-loops on `Permission denied: /data/.layout.json`. The gate avoids it by seeding the move production already finished; production hits it on any marker rewrite by uid 1001.
- `deploy-prod.yml`: `steps.deploy.outputs.rc == '0'` is true when the deploy step never ran (GitHub compares `''` and `'0'` as numbers), so candidate-evidence steps can run with nothing deployed.

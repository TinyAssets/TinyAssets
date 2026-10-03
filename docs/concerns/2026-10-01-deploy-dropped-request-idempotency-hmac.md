---
severity: P1
title: Deploy stopped validating, installing and rotating the request-idempotency HMAC key
filed: '2026-10-01'
summary: the 5th drop from #2442 -- deploy-prod.yml lost the key's validation, install, shared-env leak guard and incident rotation while DEPLOY.md still documents all four; decision RESTORE
---

# Deploy stopped validating, installing and rotating the request-idempotency HMAC key

**Filed:** 2026-10-01 by the test-hygiene lane. Found while triaging
`KeyError: 'rotate_request_idempotency_hmac'` in
`tests/test_deploy_prod_workflow.py`.
**Severity:** P1. There is no outage today, because an existing host keeps its
hand-set key. But an authority/secret-handling path is gone, and the
documentation claims it still exists.
**Decision:** RESTORE. The daemon still mounts and uses the key
(`deploy/compose.yml:202`). The deploy-incident lane does the restore after
#4242.

## What happened

#2442 (`5aeb64da`, "MVP live: ... fail-safe deploy (dark)") replaced
`.github/workflows/deploy-prod.yml` wholesale. It is the change behind the four
earlier deploy drops (see `2026-08-27-full-tests-permanently-red.md` and the
concerns it links). This is the fifth. The old workflow:

1. exposed a `rotate_request_idempotency_hmac` boolean `workflow_dispatch`
   input;
2. validated the GitHub secret `TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY`
   before touching the host;
3. installed it into `/etc/tinyassets/request-idempotency.env` (daemon-only),
   with `set-once` on an ordinary deploy, so the deploy FAILED CLOSED when the
   GitHub secret and the host key differed;
4. asserted the key ABSENT from the shared `/etc/tinyassets/env`, which workers,
   Cloudflare and logging sidecars also read;
5. on rotation, used `set` instead of `set-once`, after proving the running
   image and worker identities matched the target.

The current workflow does none of these. `grep -n REQUEST_IDEMPOTENCY
.github/workflows/*.yml` finds nothing.

## What is lost

- **Incident rotation through the workflow.** An exposed key can now be
  rotated only by hand on the host.
- **Fail-closed mismatch detection.** Persisted idempotency hashes and
  admission witnesses depend on the current key, so a silent GitHub-vs-host
  divergence is a correctness hazard that nothing checks anymore.
- **The shared-env leak guard.** Nothing asserts that the key stayed out of
  the env file every container reads.

## Stale documentation

`deploy/DEPLOY.md:117-131` still says "The deploy validates it before touching
the host and installs it before recreating the daemon/workers". It also
documents incident rotation via `rotate_request_idempotency_hmac=true`.
Neither is true of the current workflow. Fix it together with the restore.

## Tests that encode the spec: do not delete them

These assertions in `tests/test_deploy_prod_workflow.py` fail on main and
describe exactly the dropped behaviour:

- `:90` the rotation input;
- `:822` the shared-env scrub and fail-closed duplicate check;
- `:1531` the pre-host validation secret;
- `:1580` and `:1594` the install secret and install mode;
- `:1641` the rotation runbook line.

They read as stale only because the file is in `.github/heavy-test-files.txt`.
Its job runs on a schedule, is red at baseline and gates no merge, so nothing
reported them. Deleting them as "stale" would delete the spec for this
restore. Retarget them to the restored steps instead.

## Resolve by

Restore steps 1-5 in the current deploy-prod.yml shape. Bring the tests above
green against it, and correct DEPLOY.md if the mechanism differs. Then delete
this file.

---
severity: P2
title: An unhealthy candidate is rolled back inside deploy_fail_safe.sh, so its logs are never captured
filed: '2026-10-03'
summary: 'deploy-prod.yml can capture failed-candidate diagnostics only when the swap succeeded and a public surface then went red (rc 0), because that is the one window where the candidate container still exists. When the candidate never reaches healthy, deploy_fail_safe.sh rolls back internally before control returns to the workflow, so the container carrying the failure is gone and only the script stdout in the job log survives. Capturing that path needs the script to dump logs before it rolls back.'
---

# An unhealthy candidate is rolled back inside `deploy_fail_safe.sh`, so its logs are never captured

Filed alongside the restoration of the failed-candidate diagnostics path that
#2442 dropped (see `tests/test_deploy_prod_workflow.py::
test_failed_candidate_diagnostics_are_preserved_before_rollback`).

## The two failure modes, and why only one is covered

`deploy-prod.yml` has two ways a deploy can fail after the image is pulled:

1. **The swap succeeds, then a public surface goes red.** `deploy` exits rc 0;
   the MCP canary or the app probe fails; `Roll back if the public canary is
   red` is a *workflow* step. The candidate container is still running when the
   failure is known, so `Capture failed candidate startup diagnostics` can read
   its state and logs before anything replaces it. **This is covered.**

2. **The candidate never reaches `healthy`.** `deploy/deploy_fail_safe.sh`
   detects this within `HEALTH_TIMEOUT` and rolls back *itself*, restoring the
   previous image and bundle, then exits rc 2. By the time the workflow sees the
   failure, `tinyassets-daemon` is the PREVIOUS image in a container with the
   same name. **This is not covered, and cannot be from the workflow.**

The capture step's condition is `steps.deploy.outputs.rc == '0'` for exactly
this reason. It is deliberately not `always()`: reading
`docker logs tinyassets-daemon` after an internal rollback would attribute the
restored image's logs to the candidate. The restored step guards that twice
over -- the identity probe compares the container's
`org.opencontainers.image.revision` label and `.Config.Image` against the run's
target before any log is collected, and files
`candidate_identity_match: false` when they do not match.

So mode 2 is not silently mislabelled; it is simply empty. All that survives is
the script's stdout in the job log, which is unstructured, unsanitised and not
retained as an artifact.

## What would resolve it

`deploy_fail_safe.sh` should dump the candidate's `docker inspect` and
`docker logs` to a file on the droplet *before* it rolls back, and the workflow
should fetch that file in the same capture step (it already runs on `failure()`,
so only the `rc == '0'` guard would relax, with the fetch keyed to the file the
script wrote). Mode 2's rollback is the script's most dangerous path -- it is
what keeps prod from being left stopped -- so that change wants its own review
and its own test rather than riding along with a restoration.

Until then: a deploy that fails because the new image is unhealthy leaves no
retained, sanitised evidence of why.

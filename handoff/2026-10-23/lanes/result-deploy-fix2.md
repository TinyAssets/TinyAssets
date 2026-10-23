Pushed the fix round to [PR #5238](https://github.com/TinyAssets/TinyAssets/pull/5238). Head: `c0f07f9588501563e6b6f32529b38040d9af7c94`.

The deploy now uses one exit recovery path, keeps the dirty marker through acceptance, and stages and verifies restore before clearing it. Host snapshot paths come from the install manifest and cover env files and box image selection. Broker queueing and readiness now account for pending work without filling the shared worker pool.

**Verification:** 33 selected Linux oracle test executions passed. The host-path, worker-pool, and readiness regressions failed before their fixes. Ruff, shell syntax, workflow YAML parsing, diff checks, and commit hooks passed.

Live verification and the full suite remain undone under the brief’s production access and testing constraints.
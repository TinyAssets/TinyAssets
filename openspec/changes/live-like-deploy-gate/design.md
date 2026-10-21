## Shared execution boundary

Production currently has one 1,196-line workflow with host preparation, secret installation, expected-instance preparation, disk retention, an in-flight-turn wait, boxhostd installation, `deploy_fail_safe.sh`, public checks, rollback, owner-box roll, and backup. Extracting only `deploy_fail_safe.sh` would leave two definitions of the surrounding deployment. The shared unit must include those host steps and their outcome/rollback wiring; production must retain the same step order and defaults.

The CI host should be disposable and reached through localhost SSH. It needs systemd, Docker, pinned runsc, root-owned `/etc/tinyassets`, the normal Docker volume, and a loop-backed XFS owner store. Start the current image with multiple synthetic owners and centers, a live engine socket, writes, and a timer before the candidate deploy. Run a deliberate pre-swap refusal, inspect residue, then retry. Build the candidate image with the existing GHA cache.

## Unresolved production boundary; implementation stopped

The present workflow's host steps use `DO_DROPLET_HOST` and `DO_SSH_USER`, but `prepare_expected_instance_state.py` requires a DigitalOcean API token and a real droplet identity. Its core canary uses WorkOS credentials and an HTTPS service URL; the MCP and app probes default to `tinyassets.io`; owner-box rolling defaults to the public origin. The production job binds the `production` GitHub environment. A reusable invocation directly from PR code could expose production credentials to a PR, while a local-only copy of these steps would drift. The shared execution boundary and synthetic identity/authority inputs must be designed and reviewed before altering that workflow. No production path is changed by this draft.

The read-only preflight belongs before the first SSH mutation. It may inspect only; its exact host probes must be kept separate from the synthetic host's acceptance checks, because disk and canary state on the real droplet cannot be inferred from CI.

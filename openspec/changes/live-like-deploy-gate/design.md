## Shared execution boundary

Production currently has one 1,196-line workflow with host preparation, secret installation, expected-instance preparation, disk retention, an in-flight-turn wait, boxhostd installation, `deploy_fail_safe.sh`, public checks, rollback, owner-box roll, and backup. Extracting only `deploy_fail_safe.sh` would leave two definitions of the surrounding deployment. The shared unit must include those host steps and their outcome/rollback wiring; production must retain the same step order and defaults.

The CI host should be disposable and reached through localhost SSH. It needs systemd, Docker, pinned runsc, root-owned `/etc/tinyassets`, the normal Docker volume, and a loop-backed XFS owner store. Start the current image with multiple synthetic owners and centers, a live engine socket, writes, and a timer before the candidate deploy. Run a deliberate pre-swap refusal, inspect residue, then retry. Build the candidate image with the existing GHA cache.

## Caller-owned authority (Round 2 decision)

The shared unit contains every host step, including outcome and rollback wiring, in the existing order. The caller owns SSH host/user/key; service, MCP and app URLs; expected droplet identity; canary credentials; and backup destination. Production retains its current calculations and environment protection. DigitalOcean lookup remains in the production caller. The gate supplies localhost SSH, synthetic identity and credentials, local URLs and backup storage, without production secrets or the production environment. Existing application authentication must be reused; if only WorkOS works for the core canary, explicitly report that input-gated step as not exercised locally.

## GitHub execution constraint found in Round 2

GitHub does not permit `environment` on a job calling a reusable workflow. Environment secrets cannot be passed from the caller through `workflow_call`; binding the environment inside the called job would move authority into the shared unit, contrary to the decision. A separate production preparation job does not give the subsequent call its environment secrets. Do not transport credentials through job outputs or artifacts to work around that boundary.

Source: https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows (checked 2026-10-21). The production workflow currently binds `environment: production` on its deploy job.

Round 3 decides one composite action, `.github/actions/deploy-host/action.yml`, called inside the production environment job and the gate's environment-free job. This preserves caller-owned authority and keeps localhost setup and deployment on the same runner. Packaging is settled; no further packaging approval is needed.

The read-only preflight belongs before the first SSH mutation. It may inspect only; its exact host probes must be kept separate from the synthetic host's acceptance checks, because disk and canary state on the real droplet cannot be inferred from CI.

Harness cleanup must account for `install_box_harness` replacing `harness-current` before migration probes. Simply deleting a newly extracted directory on refusal would leave a dangling current pointer. The refusal/retry proof must verify both pointer safety and removal of newly installed unused harnesses, preserving the current harness and any harness needed for committed migration recovery.

## Production behavior conflict found in Round 3

The runner's composite-action schema accepts `continue-on-error` but does not accept `timeout-minutes` on either `run` or `uses` steps. Source checked 2026-10-21: https://github.com/actions/runner/blob/main/src/Runner.Worker/action_yaml.json (`run-step`, `uses-step`). Production currently has twelve individually timed steps. In particular, the nested core-capability action has a 10-minute limit, owner-box receipt upload has a 2-minute limit, and recovery diagnostics upload has a 3-minute limit. The latter explicitly bounds how long recovery holds the production host-mutation concurrency group. Other limits include the 55-minute turn wait and 50-minute owner-box roll.

A literal move is rejected by the runner schema. Deleting the limits lets these steps run until the 145-minute job deadline, changing production failure/recovery behavior. A timeout on the entire composite cannot preserve twelve separate deadlines. Shell `timeout` wrappers cannot bound nested `uses` actions; replacing those actions or adding a custom cancellation supervisor is not a pure move. Keeping timed action invocations and outcome wiring in the caller would instead relax the requirement that the shared composite own all host steps and rollback wiring.

Implementation stopped under Round 3's explicit production-behavior stop condition, before changing executable files. The unresolved constraint is preservation of per-step deadlines, not caller authority or the composite packaging choice. No production access, workflow dispatch, push, or PR occurred in this round; the gate is not implemented or green.

# B24 legacy release retirement as researched (2026-11-18)

The old host can be changed or restarted without an operator dispatch. The repository holds
three automatic host mutators and two producers that can start them:

1. A release-relevant push starts `build-image.yml`. Its successful run starts
   `deploy-prod.yml`, which recreates the old daemon. A successful deployment starts
   `install-host-services.yml`.
2. `release-reconcile.yml` runs every 15 minutes and after the Docker smoke workflow. It can
   dispatch both the image build and the deployment when its history says production is
   behind.
3. An issue receiving the `p0-outage` label starts `p0-outage-triage.yml`, which can recreate
   or restart the daemon.

The shared `production-host-mutation` concurrency group serializes mutations. It does not
stop a queued operation or a later trigger. The installed `daemon-watchdog.timer` is outside
GitHub. Its actual script restarted both the Docker container and
`tinyassets-daemon.service` when a local command recorder reported an inactive unit or a
stopped container. A healthy control made neither call.

Before the final backup can be treated as a writer boundary, the coordinator must drain the
release producers and host mutators and quiesce the installed restart services. The old
image digest, compose and environment inputs, volumes, backup path and startup operation stay
available for the handback period. The deletion-request authority has its own cutoff or
forwarding decision and is not inferred from the release freeze.

The phase-2 manifest eventually deletes the old release workflows with their old-stack code
and tests. Manual recovery and retag workflows need their own inventory evidence. The final
backup and host hold remain founder operations.

This research parsed all 64 workflow definitions on main and on the 47-branch integration
tree. Both yielded the same automatic trigger graph. The watchdog proof ran only against
temporary directories with Docker and systemd command recorders. No provider, host, workflow,
issue, secret or production setting was read or changed.

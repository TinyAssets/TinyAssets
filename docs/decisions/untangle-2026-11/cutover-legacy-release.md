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

The deletion-request boundary has two distinct facts. Before the old endpoint acknowledges a
request -- `202`, or `503` with its durable status capability while withdrawal is pending --
the job is written to the off-host deletion authority before its local identity row. The
import window's `deletions` comparison reads that authority. Fully draining and stopping the
old daemon and broker *before* the last comparison, after every restart path is quiesced,
closes the process-side writer race. Initiating a stop is not the boundary, and stopping after
the comparison would leave a real gap.

That is not yet a complete-enumeration proof. Draft PR #5688 discovers post-backup records by
listing the off-host object store. Its backup-row cross-check catches an omitted older record,
but it cannot catch a newly accepted post-backup record that the listing itself omits. The
repository has no proved list-after-write consistency contract or monotonic writer manifest
that the reader verifies. One of those is a hard gate before the final comparison can be used
to withhold every accepted request.

That ordering does not decide what the product does with a request attempted during the stop.
Today a generic `503` without a valid status capability leaves the old UI's session in place and
asks the owner to try again. This is a deletion-request outage, not an accepted request and not
a forwarding path. The founder must choose that pause-and-retry behavior or require a durable
authenticated forwarder before the writer is stopped. No such forwarder exists in the
repository today. Separately, once #5688's import lane is free, the importer still owes the
retry-safe discard/exclusion of each bundle the final comparison withholds before any owner is
admitted; that engineering work does not wait for the product choice. The read-only comparison
deliberately does not authorize admission.

The phase-2 manifest eventually deletes the old release workflows with their old-stack code
and tests. Manual recovery and retag workflows need their own inventory evidence. The final
backup and host hold remain founder operations.

This research parsed all 64 workflow definitions on main and on the 47-branch integration
tree. Both yielded the same automatic trigger graph. The watchdog proof ran only against
temporary directories with Docker and systemd command recorders. No provider, host, workflow,
issue, secret or production setting was read or changed.

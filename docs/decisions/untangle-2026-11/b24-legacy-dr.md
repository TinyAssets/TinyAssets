# B24 legacy DR drill retirement as built (2026-11-18)

Step 15 names the old weekly Droplet drill for removal. The inventory now names the
workflow, its dedicated single-owner helper, its seven manifest/map/preflight tests, the
operating page and its pass log. Git retains the log. A green run appends to it after
cleanup. Wait for running and queued drills before removing the publisher and its log.

The workflow also provides the cleanup-only operation. A red probe can deliberately retain
a drill Droplet. An unsuccessful delete can leave one after a successful probe. At
retirement the coordinator reconciles run and issue IDs with the provider inventory
while the workflow still exists. Its cleanup check matches the numeric ID, the name
`tinyassets-dr-drill`, and both tags `dr-drill` and `tinyassets`. Confirm the result for
each retained ID and check that no provisioning run started or remains queued before
removing the workflow. The primary Droplet and the disposable import hosts have their
own lifecycles.

The mixed volume proof copies the helper into a privileged fixture, exercises a
map-referenced restore in a separate disposable store, and requires its C2 result line.
Remove those three pieces together. Its next operation deletes the test's local image
and catalog and restores the published volume through the broker. Keep that N6 proof,
its generation and byte assertions, the C10 cases, and their CI selection as long as
the associated volume code exists.

The workflow's two OFFREGION_BACKUP settings have one GitHub consumer. Retire the copies
in the production environment after the last cleanup. No store deletion or provider-key
revocation follows from that observation. The shared DigitalOcean token, SSH credentials,
and backup helpers have other consumers. The import host uses the same DigitalOcean
image selection and request modules. Preserve the founder's final backup and handback
resources through the hold. The legacy vault setup notes describe a proposed drill
role. The checked-in workflow runs in the production environment and never assumes that
role; any actual account resources need separate inventory.

The new platform's `offsite-copy.yml` has a nightly copy and a monthly restore drill.
Its settings, the Files Worker, and its R2/Backblaze data are independent of the retired
workflow. Local proof uses synthetic archives, cleanup identities and a disposable
checkout. The actual step-15 deletion and reconciliation belong to the coordinator.

The rehearsal passed all seven dedicated Python cases before removal. Six synthetic
identities exercised the cleanup workflow's actual identity check. After projecting the
five deletions and removing the legacy guard entry, all 41 cutover/environment cases
passed. The 19 import-host tests also passed on the staged manifest. Two surviving
legacy recovery test files had identical outcomes across all 73 cases before and after
projection (23 passed, 15 failed, 35 setup errors on this host). The privileged mounted
volume proof was checked structurally. Integrating with the founder-setup inventory
requires one edit row per path: combine the two duration entries, the host-actions
instructions, and the deploy checklist notes.

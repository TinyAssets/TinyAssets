# Import deletion comparison as built (2026-11-18)

The old broker writes a deletion job to its off-host store before committing
the corresponding identity-database row. The key is the full SHA-256 digest
of the original principal. Finishing the job updates the record at that same
key. Older cutover records can carry only the digest. Checking unfinished
jobs alone would miss someone whose deletion completed after the final copy.

The manual import window's `deletions` mode reads the current broker records
with the existing read-only Spaces credentials. A failed listing or record
read stops the comparison. It also refuses a listing that omits any job id
already present in the backup. The backup can expose an inconsistent live read, but only
the off-host store supplies new requests. The private temporary scan is
removed on exit and replaced on each run. The Node job has no network.

The check validates the bundle builder's private records and the real bundles.
It matches with the full original digest, including for rehearsals that rename
all their staged owners. It reports the ordinals used by stage and verify.
An affected bundle returns exit 3; a malformed or inconsistent input fails the
check. The output contains no principal, record, status token or digest.

This is an observation at scan time. No receipt from this mode authorizes
admission. Step 11 still requires the old erasure writer to stop accepting
requests, or hand new requests to the replacement, plus a pending-owner
discard and retry-safe exclusion before the first admit. Those operations
are outside this read-only slice. No new credentials or production actions
are needed to exercise it against the synthetic import host.

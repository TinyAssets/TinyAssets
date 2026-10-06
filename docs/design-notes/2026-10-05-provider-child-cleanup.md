# One-time provider-child cleanup

Decision before implementation: #4472 masks `.runtime/provider-child` with a
disposable mount but charges the old on-disk bytes. Do not delete unknown old
session/auth content. An operator selects one immediate child of an explicit
universe storage root and atomically moves its exact provider-child directory
into an existing private archive directory outside that storage root, on the
same filesystem. No recursive deletion, automatic discovery or quota exemption.

The script defaults to dry-run. Apply requires an explicit offline assertion:
stop all writers and provider launches first, and exclude other cleanup runs.
This is an operator precondition, not a lock the script can enforce. Reject
symlink path components, nested mounts/devices, special files, unsafe archive
permissions and an existing archive destination. A missing or empty source is
a no-op, including after the jail recreates the empty mountpoint. Preserve all
other runtime, credential, workspace and database paths. An atomic rename
failure leaves the source in place; never fall back to copy/delete.

Report the source, archive destination, regular-file count and logical bytes.
These bytes are an inventory, not a promise of physical disk space reclaimed:
the archive retains the files, and hardlinks may exist elsewhere. Archive purge
and restoration are separate operator decisions. This lane does not execute
the script against production.

Verification matrix: default dry-run and rerun preserve bytes; apply relocates
only the selected subtree and preserves siblings; symlinks at every boundary,
nested mounts, special files, archive overlap/collision, missing offline proof
and rename errors refuse without altering source data.

# Restore begin and namespace adoption

Beginning a restore of a pending and empty owner verifies its private root and generation,
then adopts the namespace and wrapped key in file_space and writes the index step in
owner_restore. The second eligibility check still runs after every asynchronous read and
required Files hold. The newly adopted namespace and restore record now share one local
transaction. A failed marker leaves a lost object empty, so the same ordinary begin can retry.

The external hold from a previous namespace must already exist before local adoption.
Following a wipe the same SQL statement updates the existing marker and retains the fresh
generation used for undo. A matching redelivery reads the existing record.

An older pending object may already have an adopted namespace and no restore record.
That state alone supplies neither the selected generation nor the original reason for
adoption. Keep the existing nonempty-target refusal. Recovery of those objects needs
verified history and a coordinator-approved procedure before cutover.

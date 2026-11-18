# Import digests as built (2026-11-18)

C6 item 4 identified two one-shot hashes in the import contract. The importer and owner
must agree on each row's contribution to the collection digest, and the importer and
discard door must derive the same rehearsal name. Both use the contract's existing
`sha256Hex`. Their domain, UTF-8 encoding, input order, principal validation and output
lengths stay the same. The contract's kernel guard now requires zero duplicate hashes in
this module. The Files Worker's separate Web Crypto item remains in its own guard.

On Node 22 and 24, base and head each matched Python SHA-256 for 64 row leaves and five
rehearsal names, and each rejected the same four invalid names or runs. Changing the
row domain or shortening the name broke the corresponding comparison. The importer
suite passed 167 tests, the contract suite passed 297 with 18 skipped, and all six kernel
guard tests and platform typecheck passed.

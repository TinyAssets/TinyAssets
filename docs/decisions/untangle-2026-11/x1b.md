# x1b as built (2026-11-18)

The deploy kit's tests and founder checklist were the last two temporary exceptions to the
live-name guard. The tests had 73 whole-name occurrences and the checklist had 17. Reading
the R2 policy examples also found the files bucket embedded after `_default_`, where the
guard treats the whole resource identifier as a different name.

The tests read Worker, KV and bucket names from `facts.ts`. Producer bindings come from the
generated queue table and each dead-letter queue comes from its consumer's facts. The
storage assertions still identify the expected resources by role and check which are
created in a fresh, existing or partially provisioned account. Dropping the dead-letter
queues from provisioning fails both the import and the operator case. Renaming the import
staging bucket and its dead-letter queue independently in the doors config passes both.

E2B template lookup tests use a fixture alias. The deploy test checks that the image CLI
returns a name and content tag. The sandbox's own template guard checks the actual alias and
digest. The Cloudflare sandbox Worker and the E2B image can have different names.

The checklist points to the Worker configs and `platform.json`. Both R2 policy examples take
the bucket names from `node platform/deploy/facts.ts --import`, the existing import bundle
command. The upload of the import key selects the doors Worker by its checked-in Wrangler
config. Rendering both policies offline preserves the two writable import buckets, the
read-only live files bucket and the import token's IP and expiry. The off-account copy's
token still has only one read grant.

The deploy workspace's debt-only rules file and the helper that constructed those two
exceptions have no remaining readers. The ordinary literal guard still rejects a fresh
occurrence in the checklist. Its permanent exceptions remain individually checked.

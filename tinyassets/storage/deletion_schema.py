"""Digest-only historical erasures, carried once into broker custody.

Runtime erasure is owned solely by broker.admission.mutate. The cutover reads
these retained digests into that authority; owner initialization never creates
or writes this table.
"""

TABLES = {
    "deleted_principals": "CREATE TABLE IF NOT EXISTS deleted_principals (digest TEXT PRIMARY KEY)",
}
INDEXES = ()

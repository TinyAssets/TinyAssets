# Resolve resource identities before issuing an ask

The deploy kit, importer and operator share the Cloudflare queue listing. On
9c19513a, the last entry silently won if two pages gave the same queue name
different IDs. A missing identifier could also become the string "undefined".
Import staging, dead-letter acknowledgements and signed operator asks all
trusted that map. A bucket entry without its name could likewise make the dark
deploy try to create an existing bucket.

Read the complete listing before using it. A queue or KV namespace must have
both an identifier and a name, and each must identify one row. Bucket names
must be readable. The shared reader applies these rules before callers start
changes. Valid empty and omitted bucket lock rules still describe an empty
list of active rules. Queue IDs remain opaque.

An independent replay drove staging with a synthetic bundle, removing a dead
letter and issuing a signed operator request through their actual entry points.
For each, valid single-page and paged listings selected the expected ID.
Six ambiguous or incomplete inventories per caller stopped before its first
write or consumer lookup. Five dark-deploy cases checked held and unreadable
resources and both representations of empty lock rules. All 29 fixtures ran
offline on Node 24. This evidence concerns resource selection. The real stage
rehearsal and live resource identities remain cutover obligations.

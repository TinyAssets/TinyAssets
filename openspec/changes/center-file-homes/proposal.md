# Center file homes

## Why

The C6 owner box separates owner content from host control files. Mixed host and box reads make pause, worker status, ship caps, and first contact disagree.

## Change

Keep platform control and accounting files at `<data_root>/<center>` on the host. Read owner `soul.md` only from its box. The ledger keeps its existing name, `auto_ship_attempts.jsonl`, so no data moves.

Wave 2 defines host entries and moved owner entries once in `tinyassets/boxes/layout.py`, including supervisor patterns and SQLite companions. Platform owner reads share `owner_box_reads`: use a matching admitted session or resolve the broker-admitted owner before binding the configured provider, with no host fallback. Route the owner browser, automation brain context, package planning/publishing, soul version allocation, configuration presence, wiki destinations, canon enumeration, output metrics and prompt inventory through it. Keep existing read bounds and refusal envelopes. Migration/deletion, knowledge databases, and deployment remain outside this lane.

Round 3 removes actor/ownership-table threading from box reads. Listings perform no box reads and mark box-derived fields unavailable; multi-center scans isolate refusals. Dispatcher configuration, activity and status belong to the box; runtime status remains host-side. Host helpers anchor to caller paths and treat missing host centers as empty state. Unadmitted presence checks are absent without binding or materialization. Branch deletion reads soul dependencies through the box.

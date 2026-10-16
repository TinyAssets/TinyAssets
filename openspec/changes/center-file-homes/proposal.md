# Center file homes

## Why

The C6 owner box separates owner content from host control files. Mixed host and box reads make pause, worker status, ship caps, and first contact disagree.

## Change

Keep platform control and accounting files at `<data_root>/<center>` on the host. Read owner `soul.md` only from its box. The ledger keeps its existing name, `auto_ship_attempts.jsonl`, so no data moves.

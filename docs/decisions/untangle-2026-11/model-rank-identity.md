# The model name used to rank a run

ADR-034 decision 4 chooses the strongest connected source with an engine.
The owner spec gives the order within one entry of `model-ranking.json`:
newer model ids first, then the older connection. The ranking matches the
full id or its last segment so a router can expose `vendor/model`.

On main `f934871ad` and joined `151c2a83f`, ordering within the matched
entry compared the full ids. The provider prefix could outweigh the model
version or connection age. With `claude-fable-5` on a direct connection and
`anthropic/claude-fable-5.1` on OpenRouter, `Runs.choose` selected the older
model. With `gpt-6.1-sol` on an older direct connection and
`openai/gpt-6.1-sol` on a newer router connection, it selected the newer
connection. These are entries from the platform's pinned pi-ai catalog.

The rank matcher and version comparison now read the model name the same
way. When only a full id matches the entry, its full id orders the models.
Two model ids ending in `/production` can have different versioned prefixes.
The selected source and model retain their original ids when the
request is dispatched. The price fallback for unmatched ranks, ordering
between entries, and an agent's explicit connection continue to use the
existing rules. The ranking file owns relative strength; this change does
not infer that two different spellings name the same catalog entry.

An offline replay used the real `Runs.choose`, source records, catalog
model ids and 18 owner SQLite migrations with no sign-ins or outside
requests. On each tree and runtime, five direct/router model pairs with
both connection ages and either input order exercised twenty automatic
and forty explicit connection selections. Node 22 and 24 agreed.

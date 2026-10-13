# Public model lists, grouped by source

A source that can call its provider's own list-models endpoint needs nothing here:
its ids arrive from discovery already, as `executor_enumerated`, and are public by
construction.

These files exist for the sources that have **no list endpoint** — a subscription CLI
is the case that prompted them, where the only ids a universe could offer were the
ones its owner had typed by hand, so a newly released model stayed invisible.

## What a file is

`models/<source-kind>.json`, where the source kind is the value a connection reports
(`subscription`, `local`, ...). Shape:

```json
{
  "source_kind": "subscription",
  "models": {
    "source-package-name": ["some-model-4-6", "some-model-4-7"]
  }
}
```

The key under `models` is the source's provider or package name. Put each id only
under the source that actually lists it. No per-user data, no per-universe data, no credentials, no URLs, no
prices. A list here is a claim that **these model ids exist publicly** — never a grant
to use one. A universe still needs its owner's own accepted model access before any of
them can be selected, and they appear in the picker under "needs access" until then.

Only the newest of each class is offered, derived from the id's own shape by
`tinyassets/providers/model_class.py` — so listing both `some-model-4-6` and
`some-model-4-7` offers only `4-7`, and neither the loader nor the derivation knows any
vendor's naming scheme.

## Proposing an addition

Open a PR adding the id. An agent may open that PR like any contributor; there is no
special authority and no automatic path.

CI runs `python scripts/check_model_lists.py`, which refuses invalid JSON, an unknown
source kind, a duplicate, a malformed identifier, or an unsorted list. Review and merge
are the moderation, exactly as for any other tracked file.

A model id you typed yourself and that works is already on **your own** list forever
without any of this; these files are only about what everyone with that kind of source
sees.

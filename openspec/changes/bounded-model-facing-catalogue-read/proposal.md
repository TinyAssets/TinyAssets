# A bounded model-facing catalogue read, beside the picker's complete one

**Tier: public MCP surface.** A new read target and a bound on
`structured_content` are both hard to reverse once a client depends on them, so
this gets a proposal before code (AGENTS.md, "Spec what is hard to reverse").

## Why

Live 2026-09-26, turn `8dc8ada56b8e4d1cbfd2e4f37a111e7d`, free account: a
universe agent asked to build a custom UI called
`read_graph target="model_options"` and received **1,274,067 bytes** — the whole
provider catalogue. It did not fit the selected model's context, the turn was
abandoned after five rounds, and the owner was told "we could not identify why".

PR #4037 fixed the **engine** surface: one ceiling on any single served tool
result, plus a compact default for `model_options` and `status`
(`tinyassets/engine_read_views.py`). It deliberately did not touch the
**connector**, and the connector is still exposed:

- `universe_server._faithful_text_content` bounds the **text** block at
  `_MCP_TEXT_CONTENT_MAX_CHARS = 6000`;
- `universe_server._structured_return` hands `structured_content` the **full**
  parsed payload, with no bound at all.

`structuredContent` is the half the Apps SDK path exists to serve and the half a
chatbot client parses. So the same megabyte that killed a free model's turn still
reaches a browser chatbot through the public handle. Filed as
`docs/concerns/2026-09-26-public-connector-structured-content-is-unbounded.md`.

## Why this needs a spec change rather than a bug fix

`openspec/specs/live-mcp-connector-surface/spec.md`, "Shared unpowered model
catalogue", requires the read to keep its return contract, and its scenario
**"Complete choices, not a first-page sample"** requires that *all
protocol-bounded choices survive in structured content* and that *limit does not
silently hide models*.

That requirement protects a real consumer, verified rather than assumed: the
app's model picker reads this handle over MCP —
`tinyassets/onboarding/app.html:1423`,
`getModelOptions(){ return this.callTool("read_graph",{target:"model_options"}, …) }`
— and `tests/test_model_options_api.py::test_full_catalogue_survives_read_limit_and_adapter`
asserts 71 models survive `limit=1`. Bounding the existing response breaks the
owner's ability to pick a model they own. The requirement is right; what is wrong
is that one read serves two callers who want opposite things.

## What changes

1. **`read_graph target="model_options_summary"`** — a bounded, model-facing read
   of the same catalogue. Per source: how many models it has, how many are
   selectable, the current choice, and the top few of the platform's existing
   ordering; `query=` filters, `output_offset=` pages the rest as a cursor.
   Totals accompany every page, so a count is never hidden. No new parameters:
   `query`, `limit` and `output_offset` already exist on `read_graph`.
   Collision-checked: `python check_primitive_exists.py action
   model_options_summary` → CLEAN on `origin/main`.

2. **`target="model_options"` is unchanged** — same arguments, same complete
   document, same `structured_content`. The picker needs no client change, and
   the existing requirement and its test stand as written.

3. **A ceiling on `structured_content`, for `read_graph` only.** The engine's
   marker (`truncated: true`, `original_bytes`, `ceiling_bytes`,
   `returned_bytes`, a verbatim head, and a one-line hint naming how to narrow)
   applies on the connector too, from the same module, so there is one definition
   of "too big" rather than two that drift.

   **Two targets exempt, for different reasons:** `run_file`, whose contract is
   exact bytes and which `file_max_bytes` already bounds; and `model_options`,
   which (2) requires to stay complete. A target qualifies by being unusable when
   partial or by a stated completeness requirement — never by being large, which
   is what the ceiling is for.

   **An allowlist of handles.** `converse` carries the universe's reply to its
   founder, `read_page`/`write_page` carry content the user authored (Hard Rule 9),
   and `get_status` is read by the owner's own app (`active_host`,
   `supervisor_liveness` — `tinyassets/onboarding/app.html:3800`, `:3864`,
   `:3867`). None is bounded. `get_status` needs the same split this change makes
   for the catalogue; that is a separate capability, **out of scope** here rather
   than a third exemption.

4. **The description tells a model which one to read.** `model_options`'s own
   text says it is the complete catalogue, that it exceeds a megabyte on a large
   source, and that a model should read `model_options_summary` instead.

## What this does NOT do

- **It does not guess who the caller is.** Serving "complete to the app, bounded
  to a model" by sniffing a transport, a user agent or a session shape would be
  authority-by-inference, and it breaks the case that matters most: a model
  driving the founder's own signed-in session (the `ui-test` proof route) looks
  exactly like the app. The caller names which read it wants.
- **It does not reuse `query` as a mode switch.** `query` filters; overloading it
  to also select a projection is a second definition of one fact.
- **It does not change `read_model_options`** (`tinyassets/api/model_options.py`),
  the collector. `tinyassets/providers/model_options.py`'s
  `model_options_document()` keeps emitting exactly the row it emits today, so
  the shared learned catalogue (#4028, `claude/model-catalog`) needs no rebase
  against this beyond what it already owes #4037.

## Evidence

- Live turn `8dc8ada56b8e4d1cbfd2e4f37a111e7d`, 2026-09-26 23:27Z, free account.
- PR #4037 (engine-side ceiling and projections), head `8f4a8bcd`.
- `docs/concerns/2026-09-26-public-connector-structured-content-is-unbounded.md`.
- Row size **estimated**, not measured: ~856 bytes/row, from a row hand-built
  out of the fields `model_options_document` emits (#4028's author, who built it,
  states it is an estimate of a typical row rather than a measurement of the live
  payload). On that estimate 1,274,067 bytes is roughly 1,500 rows. **Re-divide
  against the real payload if one is ever captured** — a reasons-heavy or
  score-less row could move it materially.

  What the estimate does and does not carry: it supports the *diagnosis* that the
  blowout is row COUNT rather than a few pathological rows, which is why the
  answer is a per-source sample plus totals and why a smarter per-row trim would
  have produced a few hundred KB and still ended the turn. It does **not**
  underpin the size of the bounded reply — that follows from the compact row and
  the per-source head, both of which #4037's tests measure directly against a
  351-row catalogue. A wrong row-size estimate would change this section's
  narrative, not the requirement.

# Saved model preferences — bounded storage and app ingress

September 10, 2026. Task 2.1 implementation shape. This is preference data, not
provider authority, and does not activate routing or a decorative picker.

## Durable representation

Add `universe_model_preferences` to the existing canonical SQLite database,
using the existing provider-work store connection/transaction setup. Key rows by
`(owner_user_id, universe_id)`. Columns: those keys, positive integer generation,
canonical versioned `policy_json`, and server UTC `updated_at`. No secrets,
catalogue snapshots, provider receipts, request selections or grant digests.

Version 1 policy contains exactly `version`, `mode`, `saved_default`, `fallbacks`.
Mode automatic requires null saved default and an empty fallback array; it
accepts automatic ranking, not a disguised manual sequence. Explicit mode requires
a default and preserves exactly the ordered fallback array, including empty.
References contain exactly opaque `provider_ref` and `model_id`; empty model id
means a native default, not verified actual-model telemetry. Preserve case and
Unicode; reject controls, surrounding whitespace, unknown fields, malformed JSON,
duplicate JSON keys and duplicate references (including primary in fallbacks).
Bound references to 400/200 characters and an order to 1,024 fallbacks; this is a
request/storage safety bound, not a compiled list of models. Catalogue choices
are not bounded by the fallback limit. Version and generation are exact integers,
not booleans. Reject an unsupported stored version rather than silently migrate.

No cost authority is stored here. Signed serving-member cost restrictions remain
the source of permission; a chosen paid model does not grant spending. A future
narrower user price preference can be versioned separately when it is exposed.
Ranking source comes from trusted discovery configuration, not the client.
Unavailable references remain saved for the UI to explain, rather than being
erased or substituted on refresh. Each real attempt revalidates authority and
eligibility through the existing selected-member validator.

Missing row returns `generation: 0, policy: null`: legacy binding behavior is
unchanged. No GET initializes automatic mode, no save rewrites provider
definitions or publishes an assignment. Saving automatic mode is an explicit
opt-in; new-connection initialization will use this same save path later.
Corrupt/unreadable storage is held, never represented as missing or automatic.

Every save supplies expected generation. `BEGIN IMMEDIATE` compares the current
generation and atomically writes generation + 1. Generation 0 creates only when
absent; two initial writers cannot both succeed. Conflict returns the current
owner-scoped snapshot without writing. Corrupt rows cannot be overwritten by a
routine save. Never delete/reset generations (avoid ABA after returning to auto).
Account deletion's existing universe-column sweep removes the home row. Add the
table to its existing owner-keyed exception map so preferences from former homes
also disappear when that owner deletes their account; other owners remain intact.

## Authenticated app ingress

Add GET/POST `/mcp/app/models/preferences`, under existing onboarding flag and
auth middleware. Owner and universe derive only from resolved request identity
and its existing complete founder home, using `_read_home`; body/query ids cannot
select another universe. No bootstrap, LLM, credential read or provider probe.
POST additionally uses existing same-origin JSON check and a 4 MiB streaming body
bound (enough for the maximum Unicode order). Its exact envelope is
`{expected_generation, policy}`. Strict unknown-field and duplicate-key rejection.

GET returns the snapshot; POST returns saved or conflict snapshot. Responses are
no-store. Authentication 401, wrong origin 403, malformed input 400, no complete
home 409, stale generation 409, unavailable/corrupt storage 503; never leak raw
exceptions. Database work runs in the existing worker-thread helper with identity
context. No authority publication and no activated routing is implied by saving.
Do not expose the UI control until the full runtime consumes these preferences.

## Separate next integration

Current-choice ingress and runtime snapshot capture remain unfinished. The app
will keep a current choice distinct from its durable default; an explicit choice
does not silently append the old default as a fallback. The existing kernel's
accepted explicit order remains authoritative. Automatic-tail versus one-turn
manual-order semantics must be resolved visibly before current-choice ingress is
enabled. This storage/API slice does not pretend to settle or implement them.

## Independent shape review disposition

Claude review completed ADAPT in 257 seconds on September 10. Storage, CAS,
authority separation and identity scope agreed. Use `_read_bounded_body`, then
strict UTF-8/duplicate-key parsing directly, not `_read_small_json`; `policy`
must be a non-null object. Decode stored data before checking generation.
The shared `_read_home` helper gains an opt-in `raise_errors` keyword, preserving
all prior callers while allowing this route to distinguish storage failure (503)
from absent/incomplete home (409). Deletion coverage asserts the existing
universe sweep; multi-universe ingress is not enabled by this slice. Implementation
review APPROVE (318s) additionally recommended owner-key cleanup for rebound-home
retention. That mapping and real deletion regression test are now included.
The app additionally requests a same-transaction home/deletion fence when
reading or saving. A home rebind/removal or deletion tombstone between ingress
and SQLite access refuses with 409, without recreating deleted preference data.
This uses existing founder-home/tombstone rows, not a second owner registry.

Verification: strict codec, Unicode/native default, order/empty roundtrip,
concurrent create/update CAS, owner/universe isolation, corruption refusal,
legacy absence, deletion sweep, real middleware anonymous/foreign identity,
same-origin and bounded-body errors, unpowered save and no binder/provider calls.
Windows and actual Linux Docker oracle; independent cross-family review before
landing, then combined feature deployment and rendered app proof.

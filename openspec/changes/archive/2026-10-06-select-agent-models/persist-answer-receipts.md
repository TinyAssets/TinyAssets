# Preserve reply-owned model receipts through history reload

Follow-up, September 14, 2026. Same select-agent-models intent. Not part
of PR3844. Shape review completed before implementation; local implementation
and focused verification are in progress. Independent runtime review, CI and
deployment remain required.

## Observed gap

The live app loses its answering-model display on refresh. In
universe_server.converse, a request-owned WriterExecutionReceipt is projected
into the immediate result, but record_exchange stores the two texts first and
has no receipt argument. conversation_turns stores no execution metadata; Msg
and the opt-in recent_conversation projection carry only text, role and time.
The browser's loadHistory therefore cannot reconstruct the original receipt.
Current defaults, selected model IDs and global last-provider state are not
historical evidence and must not fill this gap.

## Storage and projection proposal

Add one optional execution_json TEXT column, default empty, to the existing
conversation_turns table through the writer-owned idempotent migration seam.
No new database/table, credential cache, conversation identity or public tool.
Old readers ignore the extra column. A code rollback keeps every row intact.
Read-only status/history calls never create the store or migrate it: a legacy
schema is read as legacy, with unknown receipts, using read-only schema inspection.

record_exchange accepts an optional server-derived execution receipt and stores
it on the universe reply row in the same transaction as that reply and its paired
founder message. Obtain one normalized projection before persistence and reuse
that exact snapshot in the immediate response. Do not attach it to founder rows,
background learning, tool results, arbitrary record_turn or backfilled messages.
Keep the existing earned-answer/best-effort persistence contract: a history write
failure does not repeat inference or discard the reply, and does not claim saved
history. Existing retention removes metadata with its owning row.

The closed receipt contains only provider/model/model_status, with the existing
printable 400/200-character label rules and reported-versus-unknown semantics.
No prompts, tokens, credentials, request inputs, cost grants or mutable provider
objects. Normalize/copy on write and read; malformed optional receipt data drops
only the receipt, never changes or removes verbatim message text. Receipts remain
observations, never routing inputs, authorization or consent.

Extend Msg with an optional frozen, hashable receipt value (not a mutable dict),
preserving its existing positional
arguments. Prompt-history rendering remains text/time/role only; do not add
execution labels to model instructions. The founder-only opt-in status peek
includes the validated optional receipt on that same reply. Keep its existing
principal/universe ACL, untrusted fence, text bounds and read-only behavior.

## Browser behavior

Restore each reply's footer through the existing answerExecutionDetail renderer;
display remote labels via textContent. The latest restored universe reply controls
the last-answer display. If its receipt is missing/invalid, show unknown rather
than retaining an older reply's label. Never manufacture receipts for old history.
Loading history does not change next-message choice, saved preferences or access.
Check boot/model refresh ordering so a background catalogue refresh does not wipe
an already restored receipt for the same universe. Preserve genuine scope-reset
behavior so another account/universe cannot inherit the prior label.

## Evidence required before completion

Pre-code Fable5.1 review completed ADAPT in258seconds onSeptember14. Dispositions:
use concrete read-only PRAGMA table_info to preserve legacy reads; one server
normalizer owns all closed label/status validation; immutable receipt keeps Msg
hashable; compute one snapshot before record_exchange and reuse it in response;
add a ModelPicker.observe recorder to the shipped-JS test harness. Existing
automation_context and conversation_retrieval explicit-column readers remain
untouched. A failure to add the optional column must not discard otherwise
writable history: fall back to text-only writes when the column is unavailable,
with the existing migration warning, and return no invented persisted receipt.
The reviewer agrees there is no existing durable receipt seam. No additional
proposal review needed for these bounded corrections; runtime review remains.

- Regression tests fail before implementation for persisted reply/read projection
  and actual shipped JavaScript history restoration.
- Legacy/new databases, same-transaction pairing, readonly no-DDL, retention,
  missing/corrupt receipts, multiline/Unicode labels and literal HTML-looking text.
- Two principals and two replies cannot exchange receipts; newer unknown does
  not reuse older known. Learning/failure metadata remains excluded.
- Existing message text and prompt rendering unchanged; old positional Msg
  callers and old-schema reads remain supported.
- Existing direct string/structured result contract, ACL, picker choice and
  current-home reset tests; focused Windows and Linux verification, independent
  review, fresh CI, generated app checksum and runtime mirrors.
- Verified deployment, then ordinary rendered reply and refresh preserving its
  exact provider/model or explicit unknown. Real-user clean-use evidence remains
  separate. This does not prove complete account enumeration or fallback behavior.

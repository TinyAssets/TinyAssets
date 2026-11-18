# Owner storage remedies and Step 19 dependencies as built (2026-11-18)

## The archive notice

A failed archive move can stop at the owner's storage figure. While the archive is blocked,
the ledger refuses a post at its hot ceiling with the owner's ordinary storage refusal
(`Ledger.#roomForOne`). The notice given at the first blocked move previously promised
an upgrade and said new mail was already refused. An owner-imposed lower cap and the top
tier have no larger plan to offer, and some posts can still fit. The notice now explains
which operation was blocked and that freeing space can help. The write itself uses the
same refusal as other writes.

All four `storage_busy_long` notice producers are in `owner/src/archive.ts`. The other
three concern a lost pass, prolonged contention or a repeated move error. The owner stores
notice details as sent (`owner/src/notices.ts`), and the app lists that detail
(`doors/app/app.js`). No notice type, refusal code or stored format changed.

## Billing before checkout

The contract's `Tiers` comes from the control record. The operator's reserved `tierSet`
writes the owner's admitted tier. `owner/src/storage-full.ts` combines that tier with the
current cap, what is stored and outstanding upload reservations. At the top tier, without
a known tier or with a lower owner cap, `upgrade` is null. On an upload-only refusal the
code is `uploads_open` (409) and the message explains when the reservations release;
the same facts object can still describe the next larger tier. Preserve its wire meaning.
A future purchase action needs both a `storage_full` refusal (507) and an available larger
tier. Keep `file_index_full` and the outside-client `file_share` limits separate.

The object's `Refusal.facts` travels with its RPC error. In `doors/src/refused.ts`, the
JSON surface returns the message in the `error` field, and the MCP tool returns the
message in its error text. The storage message also contains the facts as JSON. Neither
surface currently offers a structured billing link. `/app?upgrade=1` says changing plan
is closed. Review the app's typed response for a billing action before introducing it.

Window Step 10 already drains the old checkout and requires escalation if the census finds
a paid owner or a Stripe identifier. Tier alone can cross admission. The billing identifiers
go to the private cutover record (`import/bundle.ts`), outside the new owner's database.
Preserve the stop, and review promises about cancelling billing on account deletion against
the publicly reachable client and billing path at F2. Prices and paid activation stay with
the founder.

## Connector input before the five renderers

ADR-034 assigns MCP configuration to each engine record. A single on-disk JSON merge
cannot implement the Step 19 connector for every engine. The current five records passed
through the real `sandbox/runner/engine.ts` invocation builder with synthetic URLs:

| Engine | Where the permitted server reaches the CLI |
| --- | --- |
| Claude Code | Generated file and strict config argv |
| Codex | Inline server configuration in argv |
| Gemini | System JSON with a server allow-list, and a separate trusted argv allow-list |
| Kimi | Kimi's own user-level MCP JSON |
| Pi | Pi's agent-home MCP JSON |

Settle which owner-authorized connector, credential and network destination a run may use
before adding these renderings. Keep the platform server and its run-bound principal
separate from external connector credentials. The owner builds the network plan in
`owner/src/launch.ts`, and `sandbox/runner/session.ts` already distinguishes sign-in
secrets from API-key runs and retained sessions. No credentials, vendor session or network
permission were used or changed in this review.

The offline check exercised five refusal combinations and both codes handled by the archive
lane. On main the notice check failed on its unconditional upgrade advice. On the candidate
all seven passed on Node 22 and 24. The lane replay used a simulated move refusal and checked
the actual notice and hold across two passes. Full behavior-record comparison remains open.

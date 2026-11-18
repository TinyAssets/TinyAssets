# Doors errors and retries as built (2026-11-18)

Wave 2's original working listings were not retained. The accepted remaining
build (§A11) explicitly names the Doors swallowed-error and retry entries,
d2:N56 and d2:N57. This is the Doors portion of that index. The full index,
the other areas' acceptance, and the before-cutover recheck remain open.

I enumerated TypeScript catch clauses, promise catch calls and queue retry
calls across every file in `platform/doors/src` on `f934871ad` and
`533733c40`. Each had 43 catch clauses, eight promise catch calls and four
queue retry calls. I followed the operation inside each catch and the caller
that receives its outcome. The count excludes promise rejection callbacks
written with `.then`; the egress settlement, meter and end-signal callbacks
were reviewed separately. This inventory describes current code, including
work already landed in #5598. It cannot reconstruct every historical list2
item from its number.

| Boundary | Decision and consumer |
| --- | --- |
| Public request decoding | `kit.ts`, `config.ts`, `inbound.ts`, `events.ts`, and the E2B JSON/body reads reject malformed inputs at the consuming protocol. In `app.ts`, a file RPC is awaited outside the text decoding catch, so a failed owner read cannot become a binary-file answer. |
| Identity and connections | `oidc.ts` treats an unreadable error envelope as missing details; a malformed successful upstream reply fails. `authorize.ts` surfaces upstream failures to its own protocol. Erased sign-in can retry issuer deletion, logs its failure and still refuses the sign-in. `connect.ts` distinguishes abandonment before credentials are issued from cleanup of credentials already issued. Preserve both lifetimes. |
| Protocol responses | `refused.ts` converts recognized contract refusals to each surface's answer and rethrows unrelated failures. `mcp.ts` logs unexpected errors and returns a tool failure. A failed socket upgrade in `app.ts` logs and returns 500. `canary-clock.ts` turns a failed dispatch into alert input. Delivery and independent clock evidence still have separate release gates. |
| Queue redelivery | The ring, erase, operator and import consumers acknowledge malformed or terminal inputs under their own operation protocol. They request redelivery on an unresolved operation. Import records a terminal answer when an ask can be identified. Erasure and import specify queue delays; operator and ring use their queue's retry. These calls are distinct from immediately asking the same overloaded object to settle a provider call twice. Import discard generation and Files final cleanup remain separately outstanding. |
| Egress accounting and stream cleanup | A failed meter becomes unknown; the owner keeps the reservation. A failed heartbeat leaves the owner's stale-reservation recovery in force. Signal/reader cancellation is best effort after ending the stream. Reserve serialization preserves the original failing promise for its caller. A malformed optional reseal header supplies no new blob. Gateway and stream failures finish the reservation before returning failure. |
| Retry-After | Shared `refused.ts` uses the wait from the contract refusal. D2, C7 and D6 already settled the public status mapping. `doors.ts` handles the runtime's overload flag as a separate 429 at its outer boundary. The flag also applies at the inner egress settlement RPC. |

## One correction to d2:N57

The owner owns unsettled reservations. A lost settlement answer needs one
fresh stub because the original can be broken. A semantic refusal is final.
A runtime overload also ends the immediate attempt. On main the public
entry recognized overload but `settleOnce` treated it like a broken stub,
sending the same request straight back. The synthetic workerd regression ran
through the real provider response and owner boundary and counted two settle
calls on main. With the shared runtime check it counts one. The existing
fresh-stub recovery and public 429 cases still pass. An exhausted settlement
continues to log. On the next qualifying owner operation, the owner can
recover the reservation once heartbeats have been absent for 180 seconds.
An independent replay of the 18 migrations retained the estimated charge at
recovery. A late duplicate could not charge it a second time. This change does not alter the queue redelivery protocols.

This distinction follows the runtime's guidance on overloaded objects and
replacing broken stubs. [Cloudflare Durable Object error handling](https://developers.cloudflare.com/durable-objects/best-practices/error-handling/)

The sandbox's separate s2:N51 has a decision in `pass-sandbox.md` §3: the
offline template serializer is tied to the SDK pin with an executable check.
The Doors review does not reopen that independent decision.

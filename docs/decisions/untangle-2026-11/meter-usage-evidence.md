# Evidence for the price of a completed model call

G4 prices sandbox egress with the upstream adapter. A provider reply may carry
a total token count while omitting one of the differently priced parts. On
main `f934871ad` and combined `151c2a83f`, a positive total and a finite
price from pi-ai pass `meteredUsage`. The total need not prove the price.
The owner's successful settlement in `owner/src/egress.ts` replaces the
token and money reservation with the values received from the gateway.
An unknown settlement keeps the reservation.

## Offline comparison

The real gateway meter fed synthetic SSE replies to the installed pi-ai
adapters. Each pair of replies used the same model, status, response text
and finish reason. Only usage fields changed. Node 22 and 24 gave the same
result on main and the combined build.

| Pinned adapter | Complete reply | Reply missing output usage |
| --- | --- | --- |
| Chat Completions | 801 input, 1,024 output, total 1,825. Returned 1,825 tokens and 0.00012197 dollars. | The wire still said 1,825 total. The adapter recalculated 801 and returned the same cost as an explicit zero output count. An inconsistent seven component tokens and eleven reported total was returned as seven. |
| Anthropic Messages | Eleven input and seven output. Returned eighteen tokens. | Removing output counts from the message start and delta reduced the reported result to eleven, indistinguishable from an explicit zero. These fixtures have no separate total. |
| OpenAI Responses | Eleven input and seven output. Returned eighteen tokens and 0.00075 dollars. | The adapter preserved eighteen total tokens and priced the input alone at 0.00033 dollars. |
| Google Generative AI | Eleven input and seven output. Returned eighteen tokens and 0.000106 dollars. | The adapter preserved eighteen total tokens and priced the input alone at 0.000022 dollars. |

The comparison covers missing input, missing output, explicit zeros and
complete replies for the four formats. A response with only the stated
total was unknown for Chat Completions. The figures are results for pinned
fixture models, not measured vendor bills. No vendor or customer data was
used. The later change to the collector's event lifetime gave the same
outcomes in the same replays.

Six synthetic replies also passed through the real gateway entry on main
in workerd. The fixture sealed the connection, reserved each call, read the
forwarded response through its finish and read the settlement outcome.
Each call returned HTTP 200 and made exactly one reservation. With eighteen
input and twenty-five output tokens, the complete receipt succeeded with
forty-three. Removing the output or input quantity produced successful
receipts for eighteen or twenty-five respectively. Explicit zero output
also succeeded with eighteen. Supplying only the total produced an unknown
receipt. Giving both parts with a conflicting total of seventy-one still
succeeded with forty-three. This used only the local synthetic upstream.

## Work before additional charges

The sealed API's rules already describe how to request usage and how to
establish the service tier. The adapter owns converting reported quantities
into prices. Establish what proves that the quantities needed for a price
were actually supplied. An input, output or cache quantity deliberately
reported as zero must stay distinct from a missing quantity. The
normalized result no longer makes that distinction for all adapters.
A comparison of the normalized component sum with the total would catch
only some of these examples.

Reconcile this with Step 19's metering of server tools before calling an
existing fixture or a new server tool fully priced. Use complete evidence
or the existing unknown settlement for each admitted call. Keep the
adapter's pricing math in one place and verify service tiers, cumulative
and incremental counters, cache accounting and separate tool charges
against the relevant wire formats. The fixtures exercise the meter and the successful gateway callback.
Provider emissions and frequency in real accounts remain unmeasured.

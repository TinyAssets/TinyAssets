# A code script's account of its writes has to reach its caller

**Open at the Step 4 re-check.** The owner can account for a script's writes
in its return value while the caller receives only an internal error.
B26's staged owner spec describes what the owner returns. The Doors spec
limits the JSON of every tool result to 50,000 UTF-8 bytes. The two
requirements can hold individually even when the caller loses its account
of completed work.

## Where the answer goes

On main `f934871ad` and the combined build `151c2a83f`, the pi host supplies
the completed tool calls to the owner's `runScript`. The owner counts outcomes
and compresses consecutive writes with the same tool name and status into
one entry in `landed`. The call order remains recoverable. The owner's
`fittedCode` drops display output before refusing an oversize value. Even its
refusal keeps the call counts and the entire ordered summary. The Doors MCP
handler then calls `resultText`. The API and the account app both use
`answerJson`, which also calls `resultText`. This last step measures the whole
response including the refusal envelope. If it exceeds the ceiling the
client gets an internal error. A reader cannot retrieve a next page or
identify which writes to avoid repeating from that error.

The existing owner test in `platform/owner/test/code-door.test.ts` proves
that a large return value with one landed write yields the documented
refusal and keeps that write. The staged B26 source at `658e0bb6` corrects
the units and documents how the owner handles a large summary. Neither
demonstrates delivery of the summary at the outer door.

## Offline comparison

The comparison exposed the existing private fitter in a disposable bundle
and passed its actual output to both public answer functions. Inputs were
synthetic completed journals using ordinary collection tool names. No
script ran and no collections were written. Timing, VM throughput and the
frequency of these journals in production were unmeasured.

| Synthetic journal | Main | Combined build | Result seen by both transports |
| --- | ---: | ---: | --- |
| One group, small value | 140 bytes | 140 bytes | Complete result |
| One group, large value | 314 bytes | 325 bytes | Refusal with complete summary |
| 949 ordered groups, small value | 49,912 bytes | 49,912 bytes | Complete result |
| 950 ordered groups, small value | 50,139 bytes | 50,150 bytes | Internal error |
| 951 ordered groups, large value | 50,191 bytes | 50,202 bytes | Internal error |

All four combinations of tree and Node 22.23.5 or 24.22.3 gave the same
outcomes. Each also checked 1,728 combinations of ordinary text, controls,
quotes, multibyte characters, and paired and unpaired surrogates. The
shared counter matched native JSON size on strings and nested objects.
Character-boundary fitting matched native sizes for every tested budget
from two bytes through two bytes beyond the full string. Changing the shared
counter would leave the delivery problem intact.

## Decision before implementing

Choose what a caller can rely on once the ordered account of writes exceeds
a single public response. Keeping all writes available permits a bounded
first answer only if there is a way to retrieve the rest. Such a design
needs a receipt owner, access rules, retention and retry semantics. Ending
a script before its next write instead changes execution semantics and
requires reserving space before admitting each write, including calls
already in flight. An oversized return cannot undo completed writes.

Keep the shared transport ceiling and the ordered account of completed
effects in the acceptance case. Exercise the chosen behavior through a
public tool call, including an interrupted script, concurrent outstanding
calls and a caller deciding whether to retry. A change to the refusal's
wording or an exemption in one transport cannot settle this dependency.

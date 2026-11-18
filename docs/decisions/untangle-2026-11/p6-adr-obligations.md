# p6-adr-obligations as built (2026-11-18)

P6, d3 §B8 proposed refusing “owed” text in accepted ADRs. ADR-005 gives
queued work to an active change and protects an accepted decision's body.
Apply that rule in the existing builder review: a decision can cite a probe
or unfinished implementation, and its outstanding action belongs in the
active tasks. A word ban would require editing old decisions and would
catch completed work while missing a new task written without the word.

At `f934871a`, 32 of 37 ADR files began their Status section with
“Accepted”. Five of those contained the word “owed”, seven times:

| ADR | What the text records |
| --- | --- |
| 023 | Two references to the old stack's S8 owner-session custody work. These are part of the old architecture's history. |
| 034 | Live engine and connector probes. The tasks track resume and sandbox transforms. The existing stage tasks now measure real-turn CLI memory on the pinned template and record its size to assess S1 and distinguish a scheduled run's ability to read mail from webhook acceptance. |
| 035 | The stage tracks R2 latency, puts in flight and isolate memory. The founder's AES-GCM decision superseded this ADR's proposed SSE-C probe; stage item 2 proves the chosen per-owner encryption. |
| 036 | Step 20 tracks unread mail and step 5 tracks collection and conversation history. Untangle 3b deleted the old 50,000-entry ledger receipt table and kept each fact on its authoritative row (`limits-audit.md`). |
| 037 | The Status line records the archive work's completion in the owner memory leg. Decision 4 still calls for production R2 timing per edited chunk; the stage's live-numbers task already asks for it. |

The two upstream issues remained open on November 18. One reports missing
custom connector tools until a follow-up in a scheduled task. The other
reports missing custom connector tools in a webhook-triggered cloud routine.
Those reports do not establish TinyAssets' live result. The stage rehearsal
records each result from its own trigger.

- https://github.com/anthropics/claude-code/issues/106037
- https://github.com/anthropics/claude-code/issues/102388

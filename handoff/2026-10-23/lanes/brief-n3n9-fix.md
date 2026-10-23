Fix round on the N3/N9 specs, PR #5240 (worktree C:\Users\Jonathan\Projects\wf-n3-n9-spec). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-n3n9.md
Resolve every finding by deciding:
1 HIGH: remove or modify every spec that still requires the old voice bridge, in this PR, so one voice path remains.
2 HIGH: spending for realtime and media: how an open-ended stream reserves against caps (e.g. a bounded time slice
  reserved and renewed), what happens when a cap is hit mid-stream (graceful stop with a receipt), media metered per
  call; free-only defaults unchanged.
3-4: one storage rule shared by N3 and N9 (state which: committed media stored before references are published;
  partial frames ephemeral), N3 declares it builds on N9; say exactly what is kept from speech input (transcript, audio,
  or both) and default to the least.
5: define one call: a duplex session is one parent allocation with one receipt per model turn inside it (or decide
  otherwise and say why); interruption cancels the current turn.
6: source selection follows Direction 4: use the strongest connected source that supports realtime; if the strongest
  source lacks it, say so visibly rather than silently dropping to a weaker source; no hidden fallback.
7: drop owner-elected raw capture saving. 9: mark the planned tests as new. Ignore 8 (dates are UTC, correct).
Push to the same branch. Keep it short.

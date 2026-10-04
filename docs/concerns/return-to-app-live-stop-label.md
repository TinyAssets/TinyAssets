# Return-to-app Stop attribution: live trigger not reproduced

Founder report, 2026-10-04: a resent turn ended with "you stopped this turn"
without the founder pressing Stop, around return to the desktop/phone app.

Branch `fix/return-to-app-reliability` prevents an unexplained `TurnInterrupted`
exception from being recorded as an owner Stop: the final server payload now
requires that exact live turn's `requested()` flag. Regressions cover transport
cuts, silence, visibility changes, ordinary connection failure, unexpected
`TurnInterrupted`, and cancellation without a Stop request. The app sends Stop
only from the Stop button or an unhandled Escape; no visibility/disconnect/send
handler was found that requests it. Router and LiveTurn cancellation conversion
also already require `requested()`.

The original production trigger remains unproven. If it recurs after deployment,
correlate the saved failure reference and server owner_stopped log with the
/app/turn/interrupt request, addressed agent, and browser key/button event. Do not
infer a user Stop from a dropped response or exception class alone.

Cross-family review (Claude via peer-agents): no floor findings; one correctness
finding, duplicated founder bubble during active-turn recovery. AGREE: recovery
now passes the existing bubble into showActiveTurn, with a Chromium assertion
that exactly one founder bubble remains. No further review round requested.

The optional unread report was inspected: conversation_attention.py counts
founder messages unread by the agent, not replies unread by the founder. Viewing
the app is intentionally not that agent's read receipt. No literal unread badge
was found in app.html; the reported 177 badge's exact rendering source remains
unidentified, so no counter reset was added.

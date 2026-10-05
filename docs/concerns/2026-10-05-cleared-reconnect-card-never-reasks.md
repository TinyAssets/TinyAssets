---
severity: P2
title: Clearing the LinkedIn reconnect card means the agent never asks again
filed: '2026-10-05'
summary: the founder cleared "Reconnect LinkedIn — the stored token was rejected" and the agent answered that it won't ask again, the same clear-means-forever shape #4477 fixed only for patch intake
---

# A cleared reconnect card never comes back

On 2026-10-04 at 23:50 PDT the founder cleared the card "Reconnect LinkedIn:
the stored token was rejected". At 23:52 tiny replied that it is cleared and it
won't ask for it again, so LinkedIn posting and metrics stay blocked.

Clear is not "don't ask again". The muted list holds only "don't ask again".
#4477 gave patch intake a way back after a clear or decline. Reconnect and
connect asks need the same treatment: a cleared ask may be raised again when the
need recurs, and the user can reconnect from the connections controls at any
time. Generalize the way back to every ask kind, not one kind at a time.

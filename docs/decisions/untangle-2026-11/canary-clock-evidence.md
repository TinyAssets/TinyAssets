# Canary clock evidence review (2026-11-18)

Before arming the watchdog, establish how it will recognize a successful dispatch by
the doors' ten-minute clock. Step 13 first has the founder dispatch the same workflow
manually with `full`. X5's hourly watchdog must be able to tell those events apart.

`doors/src/canary-clock.ts` sends the contract's dispatch request and returns on a 204.
It writes no durable receipt of that acknowledgment. The hourly job in
`.github/workflows/platform-canary.yml` runs with `--clock` once its token is configured.
On the doors side, `deploy/canary.ts` selects the newest `workflow_dispatch` for the
configured workflow and branch, reads its creation time, and accepts an age no greater
than 30 minutes. Both producers meet that filter. A negative age passes too.

On main `f934871ad` and the local combined tree `c78a8d858`, five offline cases per
tree exercised the normal canary with simulated GitHub responses. A five-minute-old
manual dispatch and a five-minute-old clock dispatch produced the same passing result.
No run and a 40-minute-old manual run both failed with code 14. A creation time ten hours
in the future passed. These are simulated histories, without a running Cron Trigger.

The deploy harness on each tree also ran a fresh dark deploy without the token, routes,
and a second dark deploy with the token. It wrote the doors' previously missing token,
preserved all nine routed entries, and left the existing lifecycle webhook enabled.
Handback returns the platform canary to watching the old side, where the clock check
waits. These behaviors do not require a new deploy mode.

## Evidence to settle

A successful human dispatch demonstrates that the workflow can run. If the design uses
a source-owned receipt, tie it to GitHub accepting the dispatch, and determine how the
independent watchdog can still read its time if the clock stops. Confirm the failure
behavior when that evidence is missing or stale. The current Worker records no such
receipt. If the design uses a labeled workflow run, both producers need distinct normal
paths and the watchdog needs a queryable indication of the clock path. A freely chosen
title, actor, or dispatch input on its own does not authenticate who sent a request.
Document the guarantee that the chosen evidence can actually support, including the
starter manual dispatch. Preserve the existing GitHub permissions.

Before arming, check these cases through the normal watchdog. A newer manual dispatch
must neither satisfy the clock check nor hide a genuine recent tick. Only an actual
clock observation in the preceding 30 minutes, including the exact boundary, passes.
Missing, malformed and future times fail. Check the handback side separately. The
founder's first live clock verification follows an independently observed doors tick.

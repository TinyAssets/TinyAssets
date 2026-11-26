# An optional template move shares capacity with sandbox renewal

- [x] On a team limited to two running sandboxes, start two owners. Pause the
  second during its real renewal and have the synthetic provider decline its
  resume. The ordinary slot queue still holds one place for that owner.
- [x] Start another run in the first owner's existing sandbox. With the team
  count present or absent, the main behavior preserved in 152d3100 launched
  the run in the original sandbox: two cases passed. The same two tests on
  PR #5857 at aa40cc0a launched on a third sandbox, while the second owner's
  place was reserved. The fake accepted the extra allocation. This test
  establishes use of the reserved place, not loss of the renewing job.
- [ ] Reconcile optional replacement with the same capacity rule ordinary
  starts use. A replacement can be deferred and the start served on the
  original. Verify a reservation that appears during the capacity check and
  a control with genuinely spare capacity before accepting the move.

The first test draft left the reservation in the shared fixture after a failure.
Each case now returns its own reservation during teardown.

At 81168176 the ordinary sandbox suite passed all 174 tests. On current main
d662e63e with the staged sandbox changes and #5857, 200 passed and 11 failed.
Two failures are the new reservation cases. Nine earlier failures cover lost
create replies, move failures, and a waiting owner missing a notification.
Full Node 24 platform types passed on both trees.

A separate local Worker replay held the replacement while it was receiving
the copy. The synthetic provider enforced its limit by counting running boxes
at each create and resume. With limit two the other owner stayed paused
during the copy and resumed after the move finished. With limit three it
resumed during the copy. Both observations passed on aa40cc0a. The test
exercised recovery after the delay. No permanent job loss was observed.

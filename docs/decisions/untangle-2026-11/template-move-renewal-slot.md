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

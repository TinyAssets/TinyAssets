# A sandbox start can leave an earlier run waiting

- [x] On `d662e63e`, three local Worker tests used the real Owner, sandbox control and
  slot queue with a synthetic provider. After capacity returned, a different conversation
  for the same owner started. Its earlier run stayed waiting and received no notification
  when another slot freed.
- [x] With two owners waiting in order, the first owner's sign-in took a slot. Only the
  second owner was notified and its run started. The first owner's earlier run stayed
  waiting. In the control, notifying the first owner before the separate start let all
  three runs start. Notifications arrived for the first owner and then the second.
- [ ] Preserve a waiting run's opportunity to start when its owner obtains a slot for
  another purpose. Confirm both the existing wait and the next owner's turn before
  adopting a change.

The ordinary Sandbox Worker regression starts a second authority of the same owner
and then frees a slot. The expected notification never arrived before the test's
five-second deadline. On the branch based on `38ecea52`, 163 Sandbox Worker and guard
tests passed, this one failed, and the full Node 22 platform typecheck passed.

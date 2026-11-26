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

### Two owners and one available sandbox

- On `d662e63e`, runs for A and B waited in that order. A second conversation of A
  acquired the only sandbox when the synthetic provider reopened.
- The local test used the real queue to put A behind B before delivering B's first
  notification. This sets up the proposed rotation without changing queue code.
- Three head notifications reached B. On each retry B still needed a new sandbox and
  waited again. A's earlier run stayed waiting throughout.
- One additional notification to A let both conversations launch on the same sandbox.
  B retained the next turn for a new allocation. The replay exercised three retries;
  starvation if A's sandbox keeps renewing is an inference beyond that observation.

- [ ] Reconsider compatible waiting runs when their owner's sandbox becomes usable while
  preserving the oldest waiting owner's turn for a new allocation. Moving A to the back
  and notifying only B leaves A's runnable work asleep in this replay.

The new ordinary Sandbox Worker control passed on Node 22 (one selected). It directly
retries the earlier run, confirms both launches on one sandbox and checks B's turn.
The separate local Owner replay above exercises delivery of the notifications.

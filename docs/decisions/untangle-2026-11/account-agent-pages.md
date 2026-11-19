# Account directory paging as built (2026-11-19)

The Agents screen, the room selector and Connected apps consume the same paged owner directory. Each advances with the cursor the owner supplied and appends rows to the mounted form.

- [x] At `f853c3d1`, completing a directory read enabled Post while a real room-post reply was held. A second regression showed an unnamed connection as a truncated client ID after its null-vendor directory record arrived.
- [x] At `c8c7b0c1de60ba2367f89e4f2d958cd406eae3c7`, the action owns its disabled state through the reply. Directory reads control its visibility when there are rooms. The directory retains the name for the null vendor, and a successful pager retry clears its own error. Both browser regressions failed on the prior source and passed on this commit. Sign-in and starter setup also passed. The tests deliberately controlled the page boundary and delayed the post reply.
- [x] A separate Chromium journey created 320 agents through the local app's normal file routes. With the starter agent, the actual directory returned 310 owners on the first page and 11 owners plus 6 apps on the second. Agents kept the unfinished name. Rooms kept the selected recipient and unsent message. Connected apps kept the policy edit. All three consumed the real continuation. A first attempt stopped on an incorrect test selector for the room dropdown. The corrected journey passed.
- [ ] Integrate with the independently staged Rooms mail reader and board pagination, exercise the joined screens, and repeat after the coordinator's merge batch.

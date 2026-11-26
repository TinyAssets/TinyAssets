# Two ordinary starts can use a renewal's reserved place

- [x] With a three-sandbox limit, one outside sandbox and one paused
  renewal, just one place is available for ordinary starts. Hold one
  provider create in flight and start another owner. Both starts succeed
  on main d662e63e and on the capacity-refusal branch at 1c3aed85.
  The same test without an imposed overlap passed on 1c3aed85.
- [x] The local Worker test checks the ordinary run entry, the shared slot
  queue and the provider requests. Different owner objects serialize their
  own requests. Both can read the same reservation and capacity counts
  before either provider allocation becomes visible. This case uses no
  template move. The provider can accept both allocations within its
  physical ceiling while the renewal is paused.
- [ ] Account for competing platform placements before letting a new one
  consume the last unreserved slot. Include ordinary creates and resumes,
  a reply lost after allocation, and restart. Re-reading the same count or
  counting only allocations whose replies arrived leaves a gap. Preserve
  a renewal's existing place and FIFO for ordinary starts. Coordinator
  integration must also check #5857's optional replacement.

At 267144df the full sandbox Worker and guard suite passed 170 tests. Its
one failure was the new two-owner regression. Full Node 24 platform types
passed. The same regression on main d662e63e also returned two successful
starts against one available place.

A separate main replay enforced the provider's physical ceiling in the
synthetic create and resume handlers. All eight diagnostic cases passed.
Overlapping create/create, create/resume, resume/create and resume/resume
all delayed the paused peer until a contender released its sandbox.
Sequential creates or resumes left the place free for the peer. Raising
the limit from three to four let the peer resume during both overlaps.

One ordinary start can also take the held place without competing with
another ordinary start. Its turn reports zero holds. While the provider's
count is in flight, a peer reserves its running sandbox and pauses. The
count then shows a free place that belongs to the peer. The regression
requires the contender to wait at a limit of two with one external sandbox.
At a limit of three the extra place is genuinely free and the start succeeds.

- [x] The first fixture stopped at sign-in. With the synthetic peer signed
  in, the full ordinary sandbox Worker and guard run passed 171 tests,
  including the spare-capacity control. The existing two-start regression
  and the new single-start regression failed. Full Node 24 platform types
  passed.
- [x] Four separate Workerd diagnostics on main d662e63e enforced the
  physical limit for both create and resume. At two, each lone contender
  took the peer's place, the peer stayed paused and its reservation stayed
  held until the contender released. At three, the peer resumed while
  the contender was running. Every case observed zero reservations before
  the count and one while it was held. All services and owners were local.
- [ ] The shared placement decision must cover a renewal starting during
  the count as well as two overlapping placements. Both run and sign-in
  enter through the same path. Compare the optional move in #5857, which
  can keep serving from its existing sandbox when no replacement fits.

A successful launch reply can also precede a stale provider count. At
[86cb5033](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/handlers/sandboxes_list.go)
the running-total header counts the result of the team list before pagination.
The [team list](https://github.com/e2b-dev/infra/blob/86cb50338fe3bd73c10b13270aaa576b48f5d582/packages/api/internal/orchestrator/team_list_snapshot.go)
can reuse an earlier snapshot when the team's lag allowance permits it.
Zero disables reuse. The deployed setting remains unverified.

- [x] Await the first owner's successful launch before starting the next
  owner. With the same list response served twice, the second owner also
  started and consumed the renewal's place. A fresh response made it wait.
  The full offline sandbox suite passed 172 tests. This regression and the
  two previously recorded races failed. The first run also saw an extra
  give-back in another fixture. Teardown now erases the synthetic peer
  through its normal entry point, and that case passed on the rerun.
- [x] Eight separate main d662e63e Worker replays used a synthetic provider
  enforcing the physical limit at both create and resume. At three places
  the cached count delayed the peer with all four sequential combinations
  of create and resume. The peer recovered after one contender released.
  Both fresh-count controls and both four-place controls let it resume.
- [ ] The shared allocation design on this branch must account for an
  acknowledged placement that a later listing still omits. Serializing
  platform starts only until the first launch reply leaves this case open.

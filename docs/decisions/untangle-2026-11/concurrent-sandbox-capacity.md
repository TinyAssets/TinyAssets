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

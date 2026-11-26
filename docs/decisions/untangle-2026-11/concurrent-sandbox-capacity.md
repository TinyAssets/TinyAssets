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

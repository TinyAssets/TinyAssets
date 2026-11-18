# Gateway cancellation waits as built (2026-11-18)

Three gateway tests on `f934871ad` each implemented their own timer loop to see whether
the fake provider saw its request cancelled. An arrival check read the same provider
record through the shared workerd wait. All four now read the provider's record by request
ID. The three cancellation checks retain two seconds of scheduled polling. Time spent in
the observation itself is additional. The arrival check retains its original bound.
See `test-waits-2.md` for the shared wait and its clock.

Both affected gateway workerd suites passed all 50 cases. Forcing the provider's cancellation
field to false made all three affected cases fail on their own IDs. Restoring the field made
the same three pass again. The independent source review traced all eight consumers of the
test support module to workerd. The egress refusal case uses the status host to check routing
and the doors suite uses a separate fake.

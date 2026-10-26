## 0. Repairs (plan slice S0)

- [x] P0-2 Refuse websocket scopes (#5315).
- [x] P0-6 Zero idle tail (#5316).
- [x] P0-1, P0-4, P0-5 Restored event tests; callbacks through broker egress; deliveries on the tick (#5317).
- [ ] P0-7 Admission against a memory budget (#5319).
- [x] P0-8 Multiplexed relay (#5320).

## 1. Architecture on record (plan slice S1)

- [x] ADR-023 to ADR-031 merged as proposals.
- [x] ADR-024 to ADR-029 and ADR-031 accepted; ADR-023 and ADR-030 deferred; ADR-023's context corrected; rows S3 and S8 say where the loop and serving run.

## 2. Placement (plan slice S2)

- [x] `execution` on every source record; the `free` field deleted (#5322).

## 3. Authority leaves the box (N10)

- [ ] Action rules and approvals, effector consents and outside-client grants move to broker per-owner stores. Only protected owner sessions write them, and the box reads a projection.

## 4. Cross-owner state on the trusted side (ADR-025)

- [ ] Trusted front: the router answers registered cross-owner MCP requests and forwards the rest to the box. The events door folds into it.
- [ ] Ledger: broker message ledger and agent registry; mailbox read, ack and 1:1 reply. Live: an API-key agent reads and replies, and time to reply is recorded.
- [ ] Collections: broker collection store; published app rooms answered on the trusted side; `app_room`'s own compare-and-swap deleted.
- [ ] P0-3: a full-by-default grant mode on the moved outside-client authority, then the host claim settings and the claims probe.
- [ ] `mailbox.message` events. Live: a ChatGPT agent wakes on a message, reads it and replies.

## 5. Box cost

- [ ] Metering receipts (N12): box-awake intervals, peak memory and relay bytes.
- [ ] Measure what the box's app server loads at start and while awake, then slim it (lazy imports, unused services).
- [ ] Revisit ADR-023 and ADR-030 with the receipts.

## 6. On top

- [ ] Rooms, status and onboarding (plan slice S4).
- [ ] Task board on the collection store (plan slice S5b).
- [ ] The plan's later slices (S6 to S10).

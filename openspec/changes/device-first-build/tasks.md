## 0. Repairs (S0)

- [x] P0-2 Refuse websocket scopes (#5315).
- [x] P0-6 Zero idle tail (#5316).
- [ ] P0-1, P0-4, P0-5 Restored event tests; callbacks through broker egress; deliveries on the tick (#5317).
- [ ] P0-7 Admission against a memory budget (#5319).
- [ ] P0-8 Multiplexed relay (#5320).

## 1. Architecture on record (S1)

- [x] ADR-023 to ADR-031 merged.
- [ ] ADR-023 to ADR-031 accepted; ADR-023's context corrected; rows S3 and S8 say where the loop and serving run today.

## 2. Placement (S2)

- [ ] `execution` on every source record; the `free` field deleted (#5322).

## 3. Trusted front

- [ ] The router answers registered MCP tools and forwards the rest to the box; the events door folds in.

## 4. Ledger, collections and outside agents

- [ ] S3a: broker message ledger and agent registry; mailbox read, ack and 1:1 reply on the trusted front. Live: an API-key agent reads and replies, and time to reply is recorded.
- [ ] S5a: broker collection store; published app rooms answered on the trusted side; `app_room`'s own compare-and-swap deleted.
- [ ] P0-3: a full-by-default grant mode; outside-client authority moves from the box to the broker; then the host claim settings and the claims probe.
- [ ] S3b: `mailbox.message` events. Live: a ChatGPT agent wakes on a message, reads it and replies.

## 5. Serving leaves the box (ADR-030)

- [ ] Owner tools move to the trusted front one at a time, touching owner files only through `BoxProvider`.
- [ ] `owner_daemon`'s app-server path deleted; the box runs the slim harness.

## 6. Loop worker (S2a, ADR-023)

- [ ] `ExecutionOwner` constructed once at the process entry; S35 guard.
- [ ] Box tool door and remote `BoxProvider`.
- [ ] `loop-worker` deploy unit; journal and leases in the trusted area; resume without replay. Live: kill a worker mid-turn and a fresh one finishes it.

## 7. On top

- [ ] S4 rooms, status and onboarding.
- [ ] S5b task board.
- [ ] S6 to S10 and metering, as the plan's build-slice table orders them.

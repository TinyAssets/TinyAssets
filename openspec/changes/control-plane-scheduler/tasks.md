# Tasks

- [x] 1. Inventory every clock-driven loop and classify it
      (`tests/control_plane_timer_inventory.py`, ratchet test).
- [x] 2. Owner lease seam with the single-process adapter S8a replaces
      (`control_plane/lease.py`); the owner tick fires nothing without it.
- [x] 3. Trigger table + fire ledger in `.control_plane.db`, `(trigger_key,
      due_at)` fence, lost-claim settlement.
- [x] 4. Coalescing: single flight on the previous run; missed windows collapse
      and are counted.
- [x] 5. Engagement-decayed cadence policy: one default, deploy override,
      owner override per command center; engagement from the owner's message.
- [x] 6. Wake seam: a fire calls the registered run-path handler; unregistered
      kinds never fire.
- [x] 7. Platform-state-only test (audited opens during the tick).
- [x] 8. No-timer-in-a-box test (inventory has no `box` entry; jails die with
      their call).
- [x] 9. Metrics: lag, coalesced, decay states, outcomes
      (`python -m tinyassets.control_plane metrics`).
- [x] 10. Dormant-duty measurement and restart double-fire tests.
- [ ] 11. Cross-family refute (gpt-6-astra); fold P0/P1.
- [ ] 12. Sync the delta into `openspec/specs/user-owned-automations/spec.md`
      and archive on land + deploy.

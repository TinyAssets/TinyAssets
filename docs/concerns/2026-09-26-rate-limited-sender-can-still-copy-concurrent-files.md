---
severity: P2
title: A rate-limited sender can still cause one file copy per in-flight request
filed: '2026-09-26'
summary: the per-sender check now runs in the copy's pre-flight transaction, so the sequential loop is blocked, but the byte copy happens after that transaction commits, so N concurrent senders can produce N copies where 1 was allowed and the refused ones are not cleaned up. Costs the receiving owner storage, never authority; closing it needs a capacity reservation spanning the copy
---

# A rate-limited sender can still cause one file copy per in-flight request

**Filed:** 2026-09-26
**Verified:** 2026-09-26, code trace on `c2c11fe0` (PR #4018)
**Severity:** P2

## Source (verbatim)

Cross-family review of PR #4018 (Codex `gpt-5.6-sol`, 2026-09-26), finding 1:

> **P1 — Rate-limited file deliveries still copy and retain receiver-owned bytes.**
> `api/deliveries.py:412` calls `_transfer_files` before the rate check at `:469`.
> Copying commits custody objects before returning (`run_file_capture.py:317`). A
> sender already at the limit can vary occurrence IDs and cause additional copies;
> acceptance then refuses without cleaning up those committed objects. No receiver
> run ticket is spent, but storage and transfer work are consumed.
>
> **Smallest complete fix:** reserve per-sender capacity before copying, finalize it
> at acceptance, and release capacity/clean up custody on refusal. An early count
> alone does not close concurrent-copy races.

## What shipped, and what is left

PR #4018 added the per-sender check to `_transfer_files`' own pre-flight
transaction as well as to acceptance. That pre-flight holds the same two writers
acceptance holds (the author store, then the runs store), so a **sequential**
sender past its limit now copies nothing at all — the loop-a-script case is closed.

The residual is exactly the race Codex named. The byte copy
(`run_file_crossowner.copy_owned_custody_file`) runs **after** that transaction
commits, by design: acceptance holds both writers together, and copying underneath
them would open a second writer on both from one thread. So two senders arriving
concurrently can both read an under-limit count, both release, and both copy. The
bound is therefore *requests in flight*, not the rate window.

Concretely: with `sender_rate_limit=1` and N concurrent requests, up to N copies
can land where 1 was allowed. The subsequent acceptances refuse all but the first,
and the refused copies' custody objects are not cleaned up.

## Why it was accepted for now

Lead decision 2026-09-26: acceptable for an MVP, residual stated in the PR body and
in [receiver design](https://github.com/TinyAssets/TinyAssets/blob/f934871ad44b9a198070d55de517be85d8e107f6/openspec/changes/archive/2026-09-26-open-receivers-to-any-user/design.md) §4.
Mitigating factors:

- It needs genuine concurrency against one receiver; the obvious abuse shape
  (a loop) is fully blocked.
- `engine_admissions` already bounds concurrent requests per universe, so N is not
  unbounded.
- It costs the receiving owner storage, not authority: nothing is accepted, no run
  is reserved, no attribution is forged.

## What would close it

A per-sender capacity **reservation** spanning the copy: reserve inside the
pre-flight transaction, finalize at acceptance, release on refusal or crash. That is
a new lease with its own recovery path, which is why it was not attempted inside
#4018. Note that a mere early count does not close it — the reservation has to
outlive the transaction that takes it, which is the same shape as the existing
unbound custody lease.

## Where to look

- `tinyassets/api/deliveries.py` — `_transfer_files` (the pre-flight transaction and
  the check inside it), `_enforce_sender_rate_limit`, `_accept_output`.
- `tinyassets/storage/deliveries.py` — `sender_window_count`,
  `SENDER_RATE_WINDOW_SECONDS`.
- `tests/test_open_receiver_file_rate_limit.py` — proves the sequential case,
  asserting the receiver-owned `run_file_objects` count is unchanged after a
  refusal. A test for the concurrent case does not exist.

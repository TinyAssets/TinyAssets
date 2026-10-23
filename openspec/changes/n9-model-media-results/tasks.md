## 1. Result and storage

- [x] 1.1 Extend `ModelResult` with ordered typed parts while keeping its text projection; normalize only from the connected source contract. Verify mixed output and malformed-part tests.
- [x] 1.2 Stream media bytes into owner-box storage and expose owner-scoped references. Verify another owner cannot retrieve a part and shared services hold no owner-media path.

## 2. Receipt and callers

- [x] 2.1 Bind each call's emitted part references, metered media usage and outcome to exactly one C3 receipt, including cap exhaustion, partial failure and cancellation. Add `test_media_receipt_per_call` with two calls and distinct usage IDs.
- [x] 2.2 Update rendering and `ta` consumers to handle typed parts without another model path. Verify `call-sites`, targeted tests through `python scripts/linux_oracle.py -- <files>`, and Ruff.

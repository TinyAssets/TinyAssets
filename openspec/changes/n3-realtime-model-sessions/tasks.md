## 1. Broker and source contract

- [ ] 1.1 Extend the existing broker stream with bounded duplex frames or declared upgrade, per-direction credit, cancel, revocation, fence and renewable spending reservations. Verify mid-stream cap exhaustion, no direct box egress and no leaked credential with targeted transport tests.
- [ ] 1.2 Resolve audio/video/text support from each connected source's declared or discovered contract; reject unsupported media before capture or dial. Verify with capability and owner-authority tests.

## 2. One owner execution path

- [ ] 2.1 Add streaming mode to owner-box `model_call` and sessions, with one parent allocation and one C3 receipt per turn through S3 interruption and completion. Add `test_realtime_uses_s3_model_call`.
- [ ] 2.2 Replace `realtime_voice.py`, `/voice/session`, browser speech turn queue, Voice-side `converse` callback, legacy capability kind/validator/storage/tool example and public Voice configuration in one cutover. Add `test_no_realtime_loop_beside_s3`; verify `call-sites` and `opt-in-flags` at zero old paths and run `node --check` on edited JS.
- [ ] 2.3 Verify capture teardown, transcript-only input history, N9 committed output storage, uncertain disconnect and barge-in through targeted session and UI tests; run targeted tests through `python scripts/linux_oracle.py -- <files>` and Ruff.

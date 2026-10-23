## 1. Broker and source contract

- [ ] 1.1 Extend the existing broker stream with bounded duplex frames or declared upgrade, per-direction credit, cancel, revocation and fence checks. Verify with targeted transport tests for no direct box egress or leaked credential.
- [ ] 1.2 Resolve audio/video/text support from each connected source's declared or discovered contract; reject unsupported media before capture or dial. Verify with capability and owner-authority tests.

## 2. One owner execution path

- [ ] 2.1 Add streaming mode to owner-box `model_call` and sessions, carrying the C3 allocation and one receipt through S3 interruption and completion. Verify with `test_realtime_uses_s3_model_call`.
- [ ] 2.2 Replace `realtime_voice.py`, `/voice/session`, browser speech turn queue and Voice-side `converse` callback in one cutover. Verify `call-sites` and `opt-in-flags` plus `test_no_realtime_loop_beside_s3` at zero old paths; run `node --check` on edited JS.
- [ ] 2.3 Verify capture teardown, owner-box history, uncertain disconnect and barge-in through targeted session and UI tests; run targeted tests through `python scripts/linux_oracle.py -- <files>` and Ruff.

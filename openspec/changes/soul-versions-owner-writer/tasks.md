## 1. Repair and proof

- [ ] 1.1 Audit daemon visible writers and route remaining bypasses through owner publication. This lane is implemented; subscriptions, bid execution-log and auto-ship content remain with #4597.
- [x] 1.2 Add runtime owner-custody guard for common requests and periodic maintenance. Checks every recorded owner center after each periodic body; fails on the outstanding companion writer.
- [ ] 1.3 Prove lossless migration recovery and real tools on a fresh production backup. Recovery passed; chat tools remain blocked by the mapper refusal handed to #4599.

## 2. Deliver

- [x] 2.1 Run Linux oracle, affected tests, ruff, structural guards, plugin build and hygiene.
- [x] 2.2 Rebase onto merged companion fixes, sync spec, commit explicit paths and push a non-draft infra-change PR. #4600 is rebased onto merged #4599; #4597 remains open and blocking acceptance.
- [x] 2.3 Complete one cross-family correctness review and resolve findings.

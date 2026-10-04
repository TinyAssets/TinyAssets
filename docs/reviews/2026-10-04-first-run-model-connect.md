# First-run model connect review

One cross-family Claude round via peer-agents, read-only, floor/correctness only.
Original verdict ADAPT. No PR requested; this lane is commit/push only.

- F1 copied launch link could collect a recipient's provider key: **AGREE**. Launch now requires the matching protected owner session; callback rechecks it. System-browser login returns only to that same owner's flow. Tests cover unauthenticated and foreign-owner launch, foreign poll/cancel, and missing callback cookie.
- C1 unchecked automatic confirmation shape: **AGREE**. Match request id and bind_model_access action before answering, as existing hosted setup does.
- C2 setup failure and later retry both recorded: **DISAGREE_EVIDENCE** on duplicate execution. conversation_store.record_failure records the rejected attempt with effects=none; record_exchange_turns records the eventual answer. Keeping both is the existing retry history contract. The live SPA uses echoed=true, and Chromium asserts one founder bubble and one model reply.
- C3 failed exchange cannot reuse consumed flow: **AGREE** on required coverage. Added failed-exchange test proving one attempt and consumed recovery, never uncertain code replay.
- C4 cancellations consume pending slots until TTL: **AGREE**. Cancel deletes the matching owner/home-bound hosted flow. A test cancels and restarts more than MAX_PER_OWNER times.

Browser harness handoff after repeated load timeout found an inherited phone MutationObserver loop: writing unchanged rail-head text triggered its own observer indefinitely. Idempotent text assignment fixes it; phone, desktop and simulated native-browser tests pass.

No live provider credential, review key, production deployment, or PR was used.

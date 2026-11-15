# process-limits-kernel as built (2026-11-15)

Two rules found on 2026-11-15 were only in the session's briefs. This PR adds them to the repo's untangle skill (`.agents/skills/untangle/SKILL.md`, "How this project uses it", and its `.claude` mirror), so every builder and reviewer on any engine reads them (AGENTS.md Fact 16).

1. **The kernel guard runs whenever a diff touches `platform/*/src`.** `npx vitest run --root ci guards/kernels.test.ts` lives in `ci`, so a workspace's own tests and guards don't run it. Builders missed it on #5603 and #5611, and the merge queue caught each, at the cost of a CI cycle.
2. **Measured limits.** A change that brings a measured time or size within about 20% of its ceiling (a watchdog, a budget, a quota) lands with its fix, never before it. A reviewer who finds one blocks. #5604 brought the owner 2/2 shard to 8 s under its 10-minute watchdog, and the next slow runner turned main red for every PR (#5609 split the shard).

## The check on past drift

- **Kernel guard:** the merge queue runs it on every PR, so main can't hold a breach. It passes on main at `a315f1024` (6 of 6). The two misses cost a cycle each and left nothing behind.
- **Measured limits:** #5615 (`untangle/budgets-ci-2`) re-derives every test budget and watchdog from CI's own reports after the #5609 split, against this rule. It finds the contract leg's budget within 20% and fixes it there, and no `platform-sizes` shard within 20% of its watchdog. That PR is this rule's drift check. Nothing else measured against a ceiling has changed since.

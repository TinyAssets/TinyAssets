Fixed all five review findings and pushed PR #5232 on `conv/c3-tail`.

**Head SHA:** `236ebe91144d48f90044f764b88608371cea96d1`

Bound workflow calls now leave fallback to the run’s candidate loop. Request-carrier fallback requires proven unsent attempts with a known failure class and cools abandoned sources. I corrected S1, expanded the S13 guard, and removed the dead router constructor. Each behavior was covered by a failing-first targeted test.

**Validation:** 103 targeted Linux oracle tests passed; Ruff, `git diff --check`, and commit hooks passed. The PR description is updated. Nothing remains undone.
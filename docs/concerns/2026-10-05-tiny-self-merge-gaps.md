---
severity: P2
title: tiny cannot take a PR to merged on its own when main moves or the file is large
filed: '2026-10-05'
summary: asked to take #4481 through to merge alone, tiny reported five gaps; it can open and push small PRs but needs a lead session for busy or large files
---

# tiny cannot take a PR to merged on its own

The founder asked tiny (the founder's command-center agent) to take #4481
through merge alone, to find the gaps. Its own report on 2026-10-05 at 00:07 PDT,
read from the desktop app thread:

1. **No real checkout.** It edits through the GitHub contents API, one file per
   commit. It cannot bring main into its PR branch, so when main moves it rebuilds
   its edits by hand. That is what stalled #4481. Smallest fix on the user side:
   GitHub's update-branch endpoint, `PUT /repos/{owner}/{repo}/pulls/{n}/update-branch`,
   through the user's existing GitHub connection.
2. **Large files.** `app.html` is about 590 KB. It edits by exact find-and-replace
   on passages it cannot see whole, so any nearby change on main breaks the edit.
3. **It cannot run the test suite.** It learns about failures one CI round at a time.
4. **The OpenRouter reviewer is refused** with "inference usage authority
   refused". See 2026-10-05-openrouter-review-inference-authority-refused.md.
5. **Platform deploys cut its runs off partway.** Its guards prevent half-writes,
   but it has to restart the run.

Gaps 1-3 are the platform capability called "persistent boxes" in the pi gap
review: a per-universe workspace with a real git checkout and a test runner. Gap
1 alone also has the narrow update-branch fix above. Gap 5 is
deploy-kills-in-flight-turns.

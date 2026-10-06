---
severity: P3
title: A return to the app while a command-center screen holds focus may not reset chat to latest
filed: '2026-10-05'
summary: if keyboard focus is inside the command-center iframe when the user switches away and back, the main page may receive no blur/focus, so chat may not land on latest; seen only with simulated events, the real desktop sequence is unverified
---

# Chat return while the command-center iframe holds focus

**Found:** 2026-10-05, in #4465's final (round-3) Claude review and independently in
a root probe (`queue_chat_focused_frame_return_probe.py`).

When keyboard focus is inside `#ui-frame` (the command-center screen) and the user
switches to another app and back, browsers normally send no blur or focus events to
the main page. The chat's follow-latest return detection (`app.html` ~1997-2020)
therefore may not run, and the chat stays where the reader left it instead of
landing on the latest message. Nothing jumps; the reader just isn't moved to latest.

**Evidence quality:** this was reproduced only with SIMULATED events (an outer
blur/focus with `document.activeElement` still the iframe). A real OS/Electron
alt-tab could not be exercised in the headed Playwright harness, so the actual
desktop event sequence is **unverified**. The gap predates round 3 and is neither
data loss nor cross-user.

**Possible fixes:** have the frame report its own blur/focus to the parent over the
existing bridge, or check `document.hasFocus()` on visibility/resume. Verify on the
real Electron desktop app before closing.

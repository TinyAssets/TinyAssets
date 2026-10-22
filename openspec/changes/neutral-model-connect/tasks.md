## 1. Implement
- [x] 1.1 Publish all installed source choices without primary.
- [x] 1.2 Render a neutral full picker and cancel stale flow on source choice.
- [x] 1.3 Correct desktop and phone layout.
## 2. Verify and deliver
- [x] 2.1 Run targeted server, JavaScript and layout checks plus ruff.
- [x] 2.2 Sync spec and prepare Lane U PR with file eligibility and head SHA.

Round 2 validation: 12 payload/controller regressions, 18 affected heavy instruction-surface checks, 10 hosted-persistence checks and 5 universe-path guards passed in the Linux oracle. Five real Chromium cases passed: 390, 1366, 1920 and 2400 pixels, plus the legacy primary-only payload. They exercise visible alternatives, stale cancellation, key/endpoint choices, callback completion without another tap and direct authorize navigation. All network traffic is local or intercepted. Ruff, node --check and OpenSpec validation passed.

Screenshots (dialog and app at each width): `C:/Users/Jonathan/AppData/Local/Temp/lane-u-round2/connect-{dialog,app}-{390,1366,1920,2400}.png`. The legacy-payload screenshot uses `1366-legacy`. Lead reviewed the final diff. No production calls or deployment.

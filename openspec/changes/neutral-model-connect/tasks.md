## 1. Implement
- [x] 1.1 Publish all installed source choices without primary.
- [x] 1.2 Render a neutral full picker and cancel stale flow on source choice.
- [x] 1.3 Correct desktop and phone layout.
## 2. Verify and deliver
- [x] 2.1 Run targeted server, JavaScript and layout checks plus ruff.
- [x] 2.2 Sync spec and prepare Lane U PR with file eligibility and head SHA.

Validation: 10 new payload/Node/geometry regressions passed; touched existing tests passed. The 18-test affected heavy instruction-surface file passed. The 10-test hosted persistence file passed in the Linux oracle (two Windows cases require its broker socket). Ruff and node --check passed. Local Chromium renders at 390 and 2400 pixels fit and center as specified. No production calls or deployment.

# Automatic source-health MVP — live evidence

**Not completed:** the short proof below was contradicted by real-owner use on
the same release: sign-in failures16:10,16:19,16:26PDT, with repeated requests
answered by Codex16:11,16:21,16:27. PR3862 corrects the five-minute expiry;
its deployment and long-gap ordinary app verification are not established here.

September 15, 2026, production https://tinyassets.io/mcp/app, original
subscription owner through the owner's requested Chrome-extension conversation.
This is returning-user proof, not a fresh identity or first-contact test.

- Runtime PR3859 merged normally as cee95ccbde4cacee0ef293bcb487302cc4d7e4db.
- Fable5.1 approved exact reviewed head e33ca3a2 with no blocking findings:
  https://github.com/Jonnyton/TinyAssets/pull/3859#issuecomment-5688986615.
- `gh run view 35031497127 --log --job 104590809987`: required regression gate
  passed22:53UTC,18117 passed,54 skipped, zero NEW failures. Existing quarantined
  failures/errors remain; this is not a claim of zero total suite failures.
- Image build35033281537 succeeded. Deploy35033587054 succeeded23:00:30UTC;
  its `python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp
  --assert-handles` and `python scripts/deployed_sha.py --url
  https://tinyassets.io/mcp --assert-contains cee95ccbde4cacee0ef293bcb487302cc4d7e4db`
  both passed using the constrained canary principal. Image digest:
  sha256:5168c0cfd810a53089b11a28b69beaa7f4a1c71109b5efbc449857ab6b88cfd9.
- Ordinary rendered test: saved Automatic was inspected before deployment and
  never changed. Refreshed idle app after deployment; sent `Hello, are you
  there?` at16:01PDT. At16:02PDT it produced the provider sign-in notice with
  truthful uncertain-effects warning. The failed turn was not replayed.
- Sent a different new message, `What can you help me with?`, at16:03PDT without
  a temporary choice or saved-default change. It completed at16:03PDT with a
  substantive reply and `Answered by codex · Model not reported`. The footer
  remained `Next message: saved default` throughout. This proves narrow fresh-turn
  recovery; it does not prove successful Claude sign-in or the reported model ID.

No operator workflow, credential, permission or brain edits were made. The exact
independent `Retest your workflow checklist` request was sent at16:05PDT; its
outcome is separate from this narrow routing acceptance. At that moment no
subsequent owner-initiated use was visible; later failures are recorded above.
This MVP's five-minute expiry was insufficient. The correction retains unresolved
hints without timer expiry, but process restart or bounded eviction can lose them.

Specification validation: `openspec validate agent-model-selection --type spec
--strict` passed September15 in the Windows spec-sync worktree. The canonical
specification and matching active model-selection delta retain unrelated text.

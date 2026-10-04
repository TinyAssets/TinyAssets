# Native picker display retention correction

2026-10-04, PR #4431: retain the last provider-scoped catalogue for an advisory picker while refreshing. This is the requested behavior change, so the old expired-display test is updated in this same lane instead of left red in a concern. Revocation and future observations still remove the source. Display-only plans cannot authorize activation; execution still validates fresh metadata and exact current custody.

Independent Claude review of 6f33afe8e9 found that dropping native snapshots from display plans also dropped source warnings and timestamps. Keep the snapshots for projection, and bypass only native age expiry in explicit display checks after the current owner/member/custody checks. The default snapshot check and HTTP path remain strict. Returned timestamps retain their actual age.

Restoring expired native timestamps exposed the desktop picker deadline: using that source timestamp as the current read deadline would immediately disable the retained choices again. Native catalogue age is now separate from the bounded five-minute picker-read lifetime; HTTP source expiry still limits that lifetime. The executable JS regression selects a retained native choice and refuses the otherwise identical expired HTTP case.

The source-format example uses a neutral provider name to satisfy the existing channel ratchet. No vendor counter or CI threshold was changed.

Evidence on Windows/Python 3.14, 2026-10-04:
- Before correction: native model-options API + refresh regressions: 2 failed, 18 passed (missing warnings; old expired-display expectation).
- Initial correction plus channel ratchet: 40 passed.
- API/refresh/picker/native-authority files before the UI deadline follow-up: 106 passed.
- Final native model-options API + executable JS picker tests: 81 passed, including future-observation refusal, native expired display retention, and HTTP expired display refusal.
- Final independent review and hosted protected CI remain required. No deployment or rendered live-user acceptance is claimed.

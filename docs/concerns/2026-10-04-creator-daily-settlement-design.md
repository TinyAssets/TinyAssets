---
severity: P2
title: Creator daily payout direction conflicts with monthly settlement design
filed: '2026-10-04'
summary: 'PR 4466 shape review found daily TINY payouts only in proposal prose while monthly close, maturity and founder-signed settlement remain normative. Resolve the daily accrual versus settlement cadence before implementation.'
---

# Creator settlement cadence needs a consistent design

Round-1 cross-family review of PR #4466, finding 1(c): `creator-revenue-share/proposal.md` quotes daily payouts, while design section 3, spec founder-signature requirements and tasks still use monthly close. Monthly revenue accounting alone does not define daily payable accrual or daily settlement.

Follow-up owner: creator-revenue-share. Decide daily settlement versus daily accrual with a stated settlement threshold, explicitly account for maturity, gas and founder signing, then align proposal/design/spec/tasks. Do not claim daily payouts implemented or infer permission to weaken treasury signing. This round's requested fixes cover approval policy, connection splitting, third-party activation and T1 criteria; the unresolved payout decision remains tracked here.

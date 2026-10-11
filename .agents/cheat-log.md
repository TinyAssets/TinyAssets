# Substrate intervention ledger

Use this file only for current, explicit interventions that bypass an ordinary
user-authored workflow. It does not authorize a privileged platform loop,
hidden automation, automatic task dispatch, repair, filing, merge, or deploy.

Historical entries from the retired operating model are preserved at
`cheat-log.md (archived in git)`.

Format per entry:

- Header: ISO timestamp — agent id — commit/PR reference — short title
- **Justification:** one of
  `{cowork-codex-coordination-agreement, review-feedback-fast-loop, host-directive}`
- **What it is:** brief description and exact scope
- **Substrate gap that forced it:** what must change to make it unnecessary
- **Primitive left behind:** reusable artifact or `none`
- **Retire condition:** when this intervention class becomes unnecessary
- **Strictly-faster-than-alternative bar met?:** yes / no / pending / N/A

The retired `loop-uptime-maintenance-skill` is not a valid justification.

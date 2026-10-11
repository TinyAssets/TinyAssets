## ADDED Requirements

### Requirement: Disk pressure cannot silently disable tools
The deploy SHALL prune excess daemon images before pulling and refuse a new pull without 10 GiB free and usage below 80%. Retention SHALL preserve running/container images, one rollback and explicit pins (plus the newest predecessor while the canary receipt is stale), accept digest-pulled untagged images and release evidence larger than 64 KiB, and reclaim dangling layers without pruning volumes. Incomplete cleanup SHALL be visible. Authenticated canary health SHALL alert at 80% and page at 90% via the uptime alert path using the independent disk-pressure label, without triggering outage restart triage. Unknown disk measurements SHALL NOT page. Exact cached immutable images SHALL NOT require a second pull reserve; missing candidates SHALL be checked under the deploy host lock before pulling.

#### Scenario: Repeated digest deployments
- **WHEN** old digest-pulled daemon images accumulate and containment metadata exceeds 64 KiB
- **THEN** retention removes excess images while preserving running, rollback and cutover images

#### Scenario: Pressure escalation
- **WHEN** measured disk usage reaches 80% or 90%
- **THEN** uptime issues warn at 80% and become page eligible at 90%

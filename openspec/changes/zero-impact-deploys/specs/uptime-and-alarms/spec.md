## ADDED Requirements

### Requirement: Deploy traffic proof precedes ingress rollout
The deploy-during-traffic harness SHALL be the first implementation task, recording a red baseline reproducing HTTP 520 and a cut-off turn on wait/recreate. Every production ingress slice SHALL pass the same traffic oracle before shipping, including independent key moves, old-owner stragglers, resource pressure, rollback and browser recovery. Post-deploy probes alone SHALL NOT satisfy this gate.

#### Scenario: Ingress candidate lacks traffic proof
- **WHEN** an ingress change has no red baseline or passing candidate traffic evidence
- **THEN** production rollout is blocked even if health and public canaries pass

### Requirement: Schema maintenance remains explicit
Schema-changing cutovers SHALL follow PLAN.md:875 and target-architecture D11 declared maintenance windows, with exclusion across frontends, owners, boxhostd, Litestream and backup workers. Only already-compatible schemas SHALL use the zero-impact overlap/rollback path; maintenance or forced interruption SHALL NOT be reported as zero-impact.

#### Scenario: Candidate needs an exclusive schema migration
- **WHEN** a candidate requires a schema-changing cutover
- **THEN** it is excluded from compatible overlap and routed to declared maintenance rather than hidden behind gateway acceptance

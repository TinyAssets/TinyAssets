## ADDED Requirements

### Requirement: Accepted conversation intent is live across surfaces
The app and fenced connector conversation reads SHALL expose accepted pending owner messages with stable turn identity and live state before replies exist, and reconcile them with terminal pairs without duplicates.

#### Scenario: Another surface observes a running turn
- **WHEN** an owner sends from one window and the turn is accepted
- **THEN** another open window and connector conversation/status reads show the message and pending state without refresh
- **AND** terminal completion replaces pending state with one founder/reply pair
- **AND** other owners and agents cannot read the message

### Requirement: Open shells upgrade without losing drafts
The shell SHALL prohibit stale cache reuse on load, identify its served content version, and automatically upgrade open tabs after a version change while preserving unsent composer drafts within the same owner/home/agent.

#### Scenario: Deploy while a draft exists
- **WHEN** the server serves a new shell version while an idle tab has an unsent draft
- **THEN** the tab loads the new version and restores the draft without sending it
- **AND** unavailable storage defers automatic navigation

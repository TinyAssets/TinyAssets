# user-owned-automations (delta)

## ADDED Requirements

### Requirement: Every scheduled fire is the execution owner's, from platform state
The daemon SHALL fire automations and control-plane triggers from one owner tick that runs only while the execution owner lease is held, and that tick SHALL decide due work from platform state only: the automation and trigger stores, the owner's stored timezone and run status. It SHALL NOT open any path inside a command center's own directory to decide, list or measure a fire.

#### Scenario: A process without the owner lease
- **WHEN** the owner tick runs in a process whose owner lease is not held
- **THEN** it starts no automation run and fires no trigger

#### Scenario: A command center's files say otherwise
- **GIVEN** a command center whose directory holds files describing a cadence
- **WHEN** the owner tick fires its proactive trigger
- **THEN** no file under that command center's directory is opened

### Requirement: No timer lives in a box or jail
Every clock-driven loop in the platform SHALL be classified as a control-plane duty, a call-scoped wait, a client-side loop, or a loop scheduled for deletion; none SHALL run inside a command center's box or jail. A jailed process SHALL NOT outlive the platform call that started it. A box SHALL be woken only by a control-plane fire.

#### Scenario: A new periodic loop
- **WHEN** a change adds a loop that waits on a clock without classifying it
- **THEN** the inventory test fails

### Requirement: A trigger due instant fires at most once
The control plane SHALL claim each fire by recording `(trigger_key, due_at)` in the same transaction that advances the trigger, only if the trigger is still at the revision the decision was made on, and SHALL NOT fire a claimed due instant again. A claim whose handler never ran because the owner process died SHALL be recorded `lost_on_restart` by the next owner and SHALL NOT be replayed.

#### Scenario: Two owners either side of a restart
- **WHEN** two owner processes evaluate the same trigger at the same instant
- **THEN** exactly one fire is recorded and one wake handler call is made

#### Scenario: Death between claim and wake
- **GIVEN** a claimed fire whose handler never ran
- **WHEN** a new owner process starts its first tick
- **THEN** the fire is recorded `lost_on_restart` and the next fire waits for the next window

### Requirement: At most one pending fire per trigger
A trigger whose previous fire's run is still live SHALL wait rather than fire again, and windows that elapse meanwhile SHALL collapse into the one pending fire, with the number collapsed recorded on the trigger.

#### Scenario: A run outlives three windows
- **WHEN** a proactive run stays live across three cadence windows and then ends
- **THEN** one fire follows and the trigger's coalesced count rises by the windows folded into it

### Requirement: The proactive cadence decays with owner engagement, as a policy
A command center's proactive wake SHALL follow a cadence policy that defaults to every 4 hours inside 08:00 to 22:00 in the owner's clock while the owner interacted within 7 days, daily after 7 days without interaction, weekly after 30, and back to the engaged cadence on the owner's next message, and SHALL wait 30 minutes after the owner's last interaction. A wake SHALL start only inside active hours, even when it was owed earlier. Each agent of a command center SHALL have its own proactive trigger. The values SHALL come from one default, overridable for the deploy by `TINYASSETS_PROACTIVE_CADENCE` and for one command center by its owner; a malformed value SHALL be refused, not ignored. Only the command center's owner SHALL count as engagement.

#### Scenario: A dormant account
- **GIVEN** an owner who has not interacted for more than 30 days
- **WHEN** the owner tick runs for a week
- **THEN** the proactive trigger fires once

#### Scenario: The owner returns
- **WHEN** a dormant owner sends a message in their command center
- **THEN** the cadence returns to every 4 hours inside active hours, starting 30 minutes after the message

#### Scenario: Owed in the evening, noticed at night
- **GIVEN** a wake owed at 20:00 in the owner's clock that the owner tick first sees at 23:00
- **WHEN** the owner tick runs
- **THEN** the wake starts at 08:00 the next morning, once

#### Scenario: A visitor's message
- **WHEN** someone other than the owner messages the command center
- **THEN** the cadence state does not change

### Requirement: A fire calls the run path through one registered handler
A control-plane fire SHALL call the wake handler registered for its trigger kind, which starts work on the run path and returns a run id or a decline reason, and the fire SHALL record which. A trigger kind with no registered handler SHALL NOT be fired or claimed. A declined fire SHALL spend its window.

#### Scenario: Mechanism ahead of its consumer
- **GIVEN** enrolled proactive triggers and no registered proactive handler
- **WHEN** the owner tick runs
- **THEN** no fire is claimed or recorded

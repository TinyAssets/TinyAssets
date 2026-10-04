## ADDED Requirements

### Requirement: Requests appear inline at the point of need
The bubble thread SHALL render one revision-aware card per pending request beside its originating turn, with the requesting agent identified. Connect SHALL show plain-word scope and Connect / Not now; ask-first approval SHALL show a summary, editable draft, Approve / Edit / Deny and once / this task / always. Duplicate connect entry points SHALL focus this card; the rail SHALL be read-only history. Failure SHALL retain the card and draft with Try again / alternative / skip.

#### Scenario: Request arrives while the owner is on a phone
- **WHEN** an agent needs a connection or owner-rule approval
- **THEN** the pushed request renders in its thread without a 15-second wait or a rail-only chip
- **AND** the collapsed bubble shows that it is waiting for the owner

#### Scenario: Edit and choose a scope
- **WHEN** the owner edits an approval draft
- **THEN** the card displays the validated new preview before Approve, with once as the default and the exact wider rule scope visible

### Requirement: Owner events are live and recoverable
The app SHALL use the authenticated SSE contract in design.md for request and turn-status delivery, preserving the owner door's complete, identity-gated reads. Reconnect SHALL replay events or obtain a complete snapshot without duplicating cards or losing drafts. No fixed request/status poll SHALL drive this surface.

#### Scenario: Disconnect, replay and account switch
- **WHEN** the stream disconnects and later reconnects or the owner switches accounts
- **THEN** stale state is labeled with Retry, replay restores the matching owner's cards/status, and previous-owner data is never delivered into the new session

### Requirement: Sign-in preserves the conversation and credential custody
Connect SHALL launch the existing bound OAuth flow in a popup or system in-app browser instead of replacing the thread. The trusted completion path SHALL deposit credentials and resume the original request without needing a parent-page relay. Tokens, secrets and authorization codes SHALL NOT enter agent context, transcript or event payloads.

#### Scenario: Sign-in succeeds after the parent page closes
- **WHEN** the owner completes valid provider sign-in
- **THEN** the server finishes the bound connection, publishes its safe result and wakes the initiating agent to continue the saved task

#### Scenario: Provider failure or blocked popup
- **WHEN** sign-in fails, is cancelled or cannot open
- **THEN** the same card remains pending with retry, available alternative and skip; no connection success or extra provider coverage is implied
- **AND** available key deposit remains private to the card and vault

## MODIFIED Requirements

### Requirement: Working state is the universe's, and waiting lines are ordered last
The app SHALL show server-reported activity regardless of originating surface. Write-gated `get_status` SHALL return progressing/since/journal state and, once a round starts, step/model/wait duration, without prompt or owner. The model identifier SHALL match replies' Answered by line. Live status SHALL be pushed, not polled. Queued messages SHALL remain after the preceding reply, marked queued until their turn starts.

Stale rows beyond the coordinator cap SHALL NOT appear active; unreadable status SHALL NOT appear idle. Startup SHALL settle rows not executed by the current boot to their journal-derived terminal/uncertain state without inventing success. Boot ownership SHALL reflect process state (unfinished turns it created or turns created after boot), never age alone.

#### Scenario: A step waits a long time on its model
- **WHEN** a model step waits
- **THEN** status names its step, model and elapsed time; after three minutes Try another model selects only the next message's model without abandoning this reply

#### Scenario: A turn this page did not start
- **WHEN** another surface starts a turn
- **THEN** pushed status shows activity, duration and that it started elsewhere until idle

#### Scenario: A reload during a live turn
- **WHEN** history lacks the still-running turn
- **THEN** the same history read reports its activity and the indicator appears

#### Scenario: A row no client can still verify
- **WHEN** the turn exceeds the cap, its journal is unreadable, or the stream fails
- **THEN** status visibly reports stale/unreadable with Retry, without falsely clearing or indefinitely claiming activity

#### Scenario: An answer given while an earlier turn is running
- **WHEN** a request is answered before the earlier reply arrives
- **THEN** that reply precedes the queued answer, whose queued mark clears only when its turn starts

#### Scenario: A deploy recreated the daemon mid-turn
- **WHEN** the current boot is not executing a progressing row
- **THEN** startup settles it from the journal, preserves uncertainty and stops showing it as working

#### Scenario: A turn the current boot is running, older than this boot's start
- **WHEN** startup encounters a live turn this boot created
- **THEN** it leaves that turn working regardless of its timestamp

### Requirement: The chat with an agent floats over the command center
The app SHALL present thread/inline requests, request history, model bar, composer and status in a floating cloud above the stage, draggable/resizable/collapsible by pointer, touch or keyboard. Without saved placement it SHALL fill the stage for owners without a layout and start as a corner bubble with a layout. Placement SHALL persist per owner, agent (default main) and viewport (phone below 760px, otherwise wide) in owner-ui-preferences with device fallback. Cloud/bubble SHALL remain wholly visible after resizing; dragging SHALL NOT also open it.

The bubble SHALL remain reachable over broken/custom layouts as emergency control and conversation with every agent, retaining agent selection and addressed Stop. It SHALL show pushed working/waiting-for-owner/reconnecting/failed/idle status even collapsed, and indicate replies received while collapsed.

#### Scenario: A new owner signs in
- **WHEN** no layout or placement exists
- **THEN** the cloud fills the stage

#### Scenario: A command-center layout is active
- **WHEN** a layout exists without saved placement
- **THEN** the bubble appears in the stage corner

#### Scenario: The owner placed it before
- **WHEN** the owner reloads
- **THEN** their owner/agent/viewport placement is restored regardless of layout

#### Scenario: The window shrinks
- **WHEN** the stage shrinks
- **THEN** cloud/bubble is moved and resized as needed to remain wholly visible

#### Scenario: A bubble is dragged
- **WHEN** the owner drags the bubble
- **THEN** it moves without opening

#### Scenario: A custom layout fails
- **WHEN** the layout breaks or covers the stage
- **THEN** the bubble remains reachable for agent selection, conversation and addressed Stop

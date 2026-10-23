## ADDED Requirements

### Requirement: Only the owner can view and control their box
The interface package SHALL expose live browser and desktop takeover through `BoxProvider.view` on the owner door. The router SHALL authenticate the caller, select that caller's admitted box and forward the view stream without reading or storing frames or input. The box SHALL authenticate the forwarded channel and authorize the owner session before capture or control.

#### Scenario: Owner opens a live takeover
- **WHEN** an authenticated owner opens the view while their box is asleep
- **THEN** normal lifecycle routing wakes that owner's box and the view shows its browser or desktop
- **AND** no shared component reads or stores the stream

#### Scenario: Foreign or stale view request
- **WHEN** a caller substitutes another owner or box, or reuses a view after admission is withdrawn
- **THEN** the box view and input are refused without a frame or action from the other box

### Requirement: Private takeover input stays in the box
The box SHALL deliver password, 2FA and CAPTCHA input only to its focused browser or desktop target. Private input SHALL NOT appear in agent observations, transcripts, logs, saved view frames or shared stores. The box SHALL suspend agent control of that target during owner takeover and release it only after takeover ends.

#### Scenario: Owner completes a challenge
- **WHEN** the owner types a password or challenge response in the live view
- **THEN** the box sends it only to the intended focused target and never returns its value to the agent or shared layer
- **AND** the agent cannot observe or control the target until takeover ends

#### Scenario: Target or connection changes
- **WHEN** focus, document or box admission changes during private input
- **THEN** stale input is refused and the owner must reestablish a current view

### Requirement: View availability is truthful
The interface package SHALL show an unavailable or retryable state when the box or view transport cannot serve takeover, without claiming that a password, 2FA step or CAPTCHA completed.

#### Scenario: Stream fails during takeover
- **WHEN** capture or transport fails before completion
- **THEN** the owner sees the interruption and a fresh takeover requires current authentication

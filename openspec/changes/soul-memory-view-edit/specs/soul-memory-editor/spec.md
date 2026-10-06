## ADDED Requirements
### Requirement: Owner personal-file editor
The app SHALL expose Soul, Identity and Memory in Account, preserving full Markdown and supporting single-memory deletion. Reads SHALL be limited to the signed-in owner home; saves SHALL require that owner's protected session and reject stale whole-file replacements.
#### Scenario: Owner edits
- **WHEN** the owner saves a loaded document or deletes one memory item
- **THEN** only that home changes, history permits Undo, and reloading shows the saved content.
#### Scenario: Foreign or stale request
- **WHEN** a foreign home, mismatched protected session, linked file or stale digest is supplied
- **THEN** the operation fails without overwriting content.
### Requirement: Clean memory and forgetting
Fresh accounts SHALL contain no seeded personal facts or founder data. The agent SHALL use its existing file editor to remove matching memory lines when the owner says forget X, preserving unrelated lines and reporting the actual result.
#### Scenario: Fresh account
- **WHEN** an account is provisioned beside an existing personalized account
- **THEN** its memory is empty and soul/identity contain no other owner's facts.
#### Scenario: Forget a fact
- **WHEN** the owner asks to forget a remembered topic
- **THEN** the agent removes matching lines from MEMORY.md and preserves unrelated memories.

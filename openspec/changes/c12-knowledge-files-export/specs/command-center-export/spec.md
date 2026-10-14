## ADDED Requirements

### Requirement: Standalone chat runner
The export SHALL contain a Python-standard-library runner with no TinyAssets
account dependency. It SHALL read the selected agent's instructions and offer
only read, write, edit and bash to the owner's configured chat-completions
endpoint. A turn SHALL have an explicit model-call bound and fail visibly on
malformed responses or exhausted bounds.

#### Scenario: Export works without the platform
- **WHEN** the runner is copied into a folder with agent instructions
- **THEN** it can complete a tool-using turn against an owner-configured endpoint
- **AND** it does not import TinyAssets or contact its services.

### Requirement: Local owner consent
The runner SHALL refuse file paths outside the export root, symlink components,
credential files and policy/runtime writes. Bash SHALL require per-command
terminal consent unless the owner explicitly configures allow; deny and absent
terminal input SHALL not execute. The runner SHALL not start schedules implicitly.

#### Scenario: Unattended command lacks consent
- **WHEN** a model requests bash with the default policy and stdin cannot provide approval
- **THEN** no process executes and a refusal is returned to the model.

#### Scenario: Model tries to change its rules
- **WHEN** a file tool targets run/rules.json or runner code
- **THEN** the tool refuses the operation.

# Universe Custom Agents

## Purpose

An owner's agents beyond the main one: what they are, how they are named,
addressed, started and retired. An agent is a directory of editable files;
the platform keeps no table, catalogue or editor of agents.

## Requirements

### Requirement: An agent is a directory of files
The platform SHALL treat every `agents/<name>/` directory of a command center that holds an `AGENTS.md` or a `settings.yaml` as one of the owner's agents, with `AGENTS.md` as its instructions and `settings.yaml` as its behaviour settings (the same keys the root settings take), and SHALL read what an agent is from nowhere else. The main agent is the command center root and is never one of these directories.

#### Scenario: The owner's agent writes a new agent
- **WHEN** a file `agents/<name>/AGENTS.md` is written into the command center with the four tools or landed by a package install
- **THEN** `<name>` appears in the agent roster with the next read, answers on its own thread under that name, and shares the command center's brain

#### Scenario: A settings file that cannot be read never falls back
- **WHEN** an agent's `settings.yaml` is malformed
- **THEN** listing or addressing that agent fails visibly rather than answering with default settings

### Requirement: The agent block names an agent
`settings.yaml` MAY carry an `agent` block with `id` (one path-safe name other than `main`; the durable address, defaulting to the directory name), `name` (what the roster shows, defaulting to the directory name), `retired` (boolean) and `configuration` (a mapping the platform does not read). An address SHALL have exactly one reading: an id claimed by two directories answers for neither.

#### Scenario: A migrated binding keeps its address
- **WHEN** the C8 migration lands a former agent binding as `agents/<name>/` with `agent.id` set to the binding id
- **THEN** the agent is addressed by that id, its conversation thread, activities, wakes and requests continue under it, and the SQL binding row is gone

### Requirement: Addressing resolves inside the owner's command center only
`agent_id` SHALL resolve to `main` or to one directory of the addressed command center, by directory name or `agent.id`; an unknown name, a retired agent, an id of another command center, or a binding row (the serving provider, a conversation-design installation) SHALL be refused by name before any model runs, with one message that does not say which refusal applied.

#### Scenario: A binding row is not an agent
- **WHEN** a conversation-design installation's binding id is given as `agent_id`
- **THEN** the turn is refused as "no agent of yours" and the roster does not list it

### Requirement: One starter
Every agent SHALL start through `control_plane/session.py` `start(agent_ref, input, parent)`: the owner's chat turn (and the wakes and owner answers that are chat), an agent node or activity step of an admitted run, and a child session one agent hands another with `ta agent run`. The starter SHALL resolve the agent from its files once and pass its instructions to the executor; an activity of a named agent SHALL speak as that agent.

#### Scenario: An agent hands work to another agent
- **WHEN** an agent calls `ta agent run --json '{"agent": "<name>", "input": "..."}'` from a full serving-owner launch outside an activity
- **THEN** an activity of `<name>` is queued on the owner's normal budget with that input, and the caller receives its activity id
- **AND** the same call from inside an activity, from an outside launch, or for an unknown agent is refused

### Requirement: The commons catalogue is not an agent store
Published systems and packages SHALL remain the only rows of the definition catalogue (`commons_catalog`); publishing, importing, staging or exporting an agent definition SHALL NOT exist, and a published system SHALL carry no chat agents. An agent travels as the `agents/<name>/` files of a package.

## ADDED Requirements

### Requirement: Thin-loop turns call the model only through the broker
When the thin loop is selected, a served turn on an HTTP model protocol SHALL
call the model only through the credential broker
(`resolve_exact_scoped_proxy`). No model credential SHALL be held by the loop
or passed to the box, and the loop SHALL NOT execute model output: it SHALL
only route parsed tool calls by name.

#### Scenario: a cancelled turn cancels its box execution
- **WHEN** a thin-loop turn is cancelled while a box tool runs, including while
  the box's reply to starting it is still outstanding
- **THEN** the execution the box accepted is cancelled in the box and the call
  is journaled as unknown

### Requirement: Box tools are forwarded to the turn's bound box by op_id
The tools `read`, `write`, `edit` and `bash` SHALL execute in the turn's
command-center box through the `BoxProvider` contract, on a handle bound once
at turn start for the turn's owner, command center and turn, and never looked
up per call. Each call SHALL carry an `op_id` derived from its journal
position. A lost reply SHALL be resolved only by asking again with the same
`op_id`; an outcome still unresolved, including a timed-out execution whose
end the box does not confirm, SHALL be recorded as unknown, the turn SHALL
hold, and the operation SHALL NOT be re-issued. Writes through the box tools
SHALL be ordered, so that an `edit` refuses rather than overwrites when another
write through the box tools changed the file after the edit read it.

#### Scenario: a lost reply runs once
- **WHEN** the reply to a box tool's `start_exec` is lost
- **THEN** the loop asks again with the same `op_id` and the box runs the
  command once

#### Scenario: an unknown outcome holds the turn
- **WHEN** a box tool's outcome cannot be resolved
- **THEN** the journal records the call as unknown, the turn ends
  `held_tool_unknown`, and no further inference or tool call is made

#### Scenario: a granted box tool with no box is refused
- **WHEN** the thin loop is selected, a turn is granted a box tool and no box
  provider is configured
- **THEN** the turn is refused before its first inference and no tool runs
  anywhere else

#### Scenario: the founder's mid-turn message rides on a box result
- **WHEN** the founder sends a message while a thin-loop turn works, and the
  turn's next tool call is a box tool or an owner read
- **THEN** the message is appended once to that tool's result, exactly as the
  engine route appends it to an engine tool's result

### Requirement: Owner reads are served by the loop and never reach the box
The tools `history` and `activity` SHALL be answered by the loop, read-only,
for the turn's owner and command center only, through the same domain reads
and identity gates as the owner door. They SHALL NOT be forwarded to the box
and SHALL take no parameter naming an owner or command center.

#### Scenario: history is the founder's own
- **WHEN** a turn reads `history` for a command center its owner no longer
  holds as founder home
- **THEN** the read is refused and nothing is disclosed

### Requirement: Other served tools keep their engine route and gates
Every served tool other than the box tools and the owner reads SHALL keep its
existing engine route, so the owner's rules and the auto-review continue to
gate consequential actions where they are enforced today. With the thin loop
not selected, every turn SHALL behave exactly as before this change.

#### Scenario: switch off is today's path
- **WHEN** `TINYASSETS_AGENT_LOOP` is unset
- **THEN** the four tools are served by the engine route and no box is bound

#### Scenario: two edits never silently lose one
- **WHEN** several edits of one file run concurrently, each having read the
  same bytes
- **THEN** every edit that reports success is present in the final file, and
  every other edit reports that the file changed

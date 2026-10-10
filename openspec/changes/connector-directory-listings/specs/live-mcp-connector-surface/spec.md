## ADDED Requirements

### Requirement: Conservative directory safety metadata

The server SHALL describe the aggregate side effects of each canonical handle and SHALL advertise explicit read-only, destructive, idempotent and open-world hints. A handle that can overwrite, delete, publish, or delegate external actions SHALL NOT be labelled safe solely because some of its operations are reads or previews. Annotations SHALL NOT replace authorization.

#### Scenario: Directory scans canonical tools

- **WHEN** a directory retrieves `tools/list`
- **THEN** the seven canonical handles remain unchanged
- **AND** `write_graph`, `write_page`, `run_graph`, and `converse` advertise `readOnlyHint=false`, `destructiveHint=true`, `idempotentHint=false`, and `openWorldHint=true`
- **AND** `read_graph`, `read_page`, and `get_status` remain read-only
- **AND** write descriptions disclose their material side effects

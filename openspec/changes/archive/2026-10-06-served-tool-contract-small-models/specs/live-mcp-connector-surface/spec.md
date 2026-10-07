## ADDED Requirements

### Requirement: Served JSON arguments accept the value or its text
The served engine `write_graph payload_json` and `run_graph inputs_json` arguments SHALL accept a JSON object or array as the value itself, and SHALL keep accepting its JSON text. A string whose content is itself an encoded object or array SHALL be unwrapped once. Every form SHALL reach the handler as one canonical JSON text, so the handler has a single validator. The public connector's argument types SHALL be unchanged.

#### Scenario: an object payload builds
- **WHEN** a served turn calls `write_graph target=branch operation=create` with `payload_json` as an object whose `prompt_template` contains a newline
- **THEN** the branch builds and the stored template keeps the newline verbatim

#### Scenario: the string form keeps working
- **WHEN** a caller sends the same spec as JSON text
- **THEN** the branch builds exactly as before

### Requirement: Served refusals are errors
Every served engine tool result that refuses the call SHALL be returned with `isError: true` and its refusal text unchanged. A refusal is a JSON object with a truthy `error` or `errors` and no `status`, or a `status` of `rejected`, `refused` or `error`. A result that describes something read or made, such as a failed run's record, SHALL NOT be flagged, and neither SHALL the result of a file or shell handle (`read`, `write`, `edit`, `bash`), whose text is arbitrary content. A refusal larger than the result ceiling SHALL still be flagged, and SHALL be bounded. A JSON parse failure SHALL name the decoder message, the line and column, and a bounded excerpt, and SHALL say that the object can be passed instead.

#### Scenario: malformed payload text
- **WHEN** `payload_json` text contains an unescaped newline inside a string
- **THEN** the result has `isError: true` and names the line, the column and the character

#### Scenario: reading a failed run
- **WHEN** `read_graph target=run` reads a run whose status is `failed`
- **THEN** the result has `isError: false`

#### Scenario: reading a file that looks like a refusal
- **WHEN** `read` returns a file whose content is `{"errors": [...]}`
- **THEN** the result has `isError: false`, and the content is unchanged

### Requirement: An unwired node list runs in order
When a branch create spec gives two or more nodes with no edges or conditional edges at all, and either no entry point or one equal to the first node, staging SHALL chain the nodes in the order listed and SHALL report this as a notice. Any edge, any conditional edge, or an entry point other than the first node SHALL be validated exactly as written.

#### Scenario: two nodes, no edges
- **WHEN** a spec lists nodes `gather` then `write_up` and gives no edges
- **THEN** the branch builds with the edge `gather -> write_up`, the entry point `gather`, and a notice naming the order

#### Scenario: partial wiring
- **WHEN** a spec lists three nodes and gives one edge
- **THEN** no edge is inferred, and the unreachable node is reported

### Requirement: A running run's read waits briefly
The served `read_graph target=run` SHALL re-read a run that is `queued`, `running` or `resumed` for up to 10 seconds, and SHALL answer as soon as the run leaves those states. Any other payload SHALL be returned on the first read.

#### Scenario: a run that settles inside the window
- **WHEN** a run completes 3 seconds after the read begins
- **THEN** one tool call returns the completed record

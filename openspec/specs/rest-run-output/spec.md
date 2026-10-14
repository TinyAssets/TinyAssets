# REST run output

## Purpose
Allow an authorized API-key client to retrieve workflow results through the same
bounded run-output reader as MCP, without a file-read route or new capabilities.

## Requirements

### Requirement: Bounded authorized result retrieval
`GET /api/command-centers/{cc}/runs/{run_id}/output` SHALL require the existing
`read_graph` grant for that command center and agent `main`, and SHALL call the
same `read_graph(target="run_output")` path as MCP. Existing run ownership,
command-center binding, key revocation and outside-client authority SHALL apply.
An existing run bound to another command center SHALL return 404 under this
selector; naming an ungranted command center SHALL return 403.

#### Scenario: Read a result
- **WHEN** an admitted caller supplies `field_name=result`
- **THEN** the response SHALL contain the existing run-output envelope with
  status, chunk, encoding, Unicode-code-point offsets and next_offset.
- **AND** omitted field_name SHALL return the existing 64-field catalog page.
- **AND** output_offset SHALL default to 0 and output_max_chars to 8192, with
  output_max_chars bounded to 1..32768; numeric parameters accept at most ten
  decimal digits. Offset units SHALL be fields for a
  catalog and Unicode code points for a field.
- **AND** unknown/duplicate query parameters, non-ASCII-decimal integers,
  negative offsets and out-of-range sizes SHALL return 400.
- **AND** this endpoint SHALL expose no arbitrary filesystem selector.

#### Scenario: Accepted is not completed
- **WHEN** a run is still queued or running
- **THEN** the response SHALL retain its actual status; retrieval SHALL neither
  wait for nor claim completion. Callers SHALL poll status with bounded backoff.
- **AND** clients SHALL concatenate chunks using next_offset and parse JSON
  text once after the last chunk, without duplicating the optional whole value.

#### Scenario: Persisted output survives client restart
- **WHEN** the HTTP client reconnects after output has been persisted
- **THEN** the same authorized route SHALL read the persisted result and offsets.

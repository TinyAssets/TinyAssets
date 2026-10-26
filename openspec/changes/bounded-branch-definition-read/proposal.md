# Proposal
## Why
The owned Roundtable adapter is 27,857 characters, but both model doors return only 19,438 with no source continuation. A complete node page is not a complete definition.
## What Changes
Add lossless bounded JSON chunks on the existing branch read using field_name="@definition", with a digest and explicit continuation. Keep authorization and foreign-content provenance.
## Capabilities
### Modified Capabilities
- live-mcp-connector-surface: complete bounded branch definition reads.
## Impact
engine_read_views, universe_server and engine_mcp_server; no new handle, grant, stored state or execution. Separate draft PR off deployed main; overlapping wrapper files in convergence #4937/#4939 must carry this fix forward, without changing those branches here.

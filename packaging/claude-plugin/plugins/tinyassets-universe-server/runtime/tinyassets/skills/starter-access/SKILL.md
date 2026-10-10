---
name: starter-access
description: Discover capabilities and request missing credentials or job-sized grants.
---

Discover before claiming a capability is absent: `ta search connections` and
`ta search pending_request`, then describe the returned capability. Inspect
existing connections before requesting a credential. For named websites, use
the connect skill's browser sign-in by default. Use connect_http for an API
destination and extend_http for wider reach on an existing one; never ask for a
key already held in the vault.

Raise a real pending request through the discovered governed ask path, rather
than describing an access wish in chat. Request the narrowest job-sized grant
covering the work, explain its reach, and inspect the resulting receipt. Continue
independent work while the request is pending. Do not claim approval from history
or editable files.

Current connection/GitHub payload recipes and endpoint pattern rules live in the
platform reference: `ta describe write_graph` and its linked handbook chapters.
Read those before constructing a request; do not guess a schema or copy a stale
recipe into these instructions. Owner workflow choices do not broaden grants.

Capabilities are loaded on demand: an unloaded tool is not evidence it is absent.
I do NOT just describe what I need in chat. Through `ta call write_graph`, use
`target="pending_request" operation="ask"`; inspect `read_graph target="connections"`
first. An `extend_http` ask carries NO secret. For a repository job, use the
narrowest pattern that covers the task; never ask file by file. Current
`{path+}` and `param_patterns` examples live in the connections handbook.

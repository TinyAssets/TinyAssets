# Public definition reads: metadata first, complete bodies on request

This repairs observation of a published definition without changing publishing,
installation, private-stage authority, or the complete owner-door API.

- Connector `read_graph target=agents` returns summaries: id, author, name,
  description, tags, component count/kinds, and a package summary when present.
  Package metadata precedes UI bodies, which are never inlined by default.
  `limit` is a row maximum (1..100); encoded size may yield a smaller page.
  `output_offset` is a nonnegative integer in the filtered definition catalog.
  Follow `next_offset` until null. Offset is applied after author/query/tag filters.
- `target=agent agent_definition_id=<id>` returns a metadata summary and component
  catalog with exact keys, kinds, preview names, serialized sizes, and selectors.
  `output_offset` pages component indices in sorted key order. Legacy `graph_id`
  selection remains supported. Summaries explicitly identify themselves as such;
  names/tags and package needs may be previewed with counts, never substituted
  for a claim that all bodies or all connection names were returned.
- `field_name=<exact component key>` returns its complete JSON as Unicode chunks.
  Concatenate `chunk` strings and decode once; `output_offset` is a Unicode code
  point index. `output_max_chars` (1..32768) is a maximum, reduced for JSON escaping
  and transport headroom. `next_offset=null` is the end, including a read exactly
  at the end. Invalid types, negative offsets, missing components and offsets
  past the end refuse explicitly. A caller may reread earlier valid offsets.
- `field_name=@definition` supports lossless reading of the entire legacy
  definition, including portable definition and provenance, by the same protocol.
- Served `read_graph` stays pinned: it still refuses public agents/agent targets.
  Its existing public handles `browse_commons kind=agents|packages` and
  `read_commons_shape agent_definition_id=...` expose corresponding paging and
  component selectors. Other browse kinds reject a nonzero offset. Existing
  own/foreign provenance envelopes remain; private stages stay on their owner gate.
- Package browsing validates the package tag AND component kind before applying
  row offsets. It preserves version, size, file count and bounded needs previews,
  with agent/connection counts and a `package` component selector for all details.
  `publication_kind=command_center` requires both actual tag and package component;
  branch-ref-only definitions report `workflows`, other definitions `system`.

Numeric list offsets address the current filtered catalog in its existing stable
created-at/id order. Newly published definitions can change those indices; this
is not a retained catalog snapshot or a claim of transaction isolation across calls.
Individual published definitions are immutable, so their component chunk reads
are stable. Storage/raw custom_agents APIs and owner-door results remain complete.

The OAuth schema comes from the registered `universe_server.read_graph` signature
through `describe_signature` and `_OAuthFunctionTool`. It already declares
field_name/output_offset/output_max_chars; tests read the actual FastMCP tools list
and invoke the registered tool. This does not refresh an external client's cached
schema or claim that its currently cached tool list has changed.

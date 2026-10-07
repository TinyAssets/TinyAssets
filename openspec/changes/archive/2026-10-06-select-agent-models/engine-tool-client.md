# Private HTTP engine-tool client: next bounded slice

September10,2026. Proposed task2.4 implementation boundary, not an activated HTTP
agent loop. Reuse the shared owner-bound route from644d6d74 and installed FastMCP
3.2/MCP transport. Do not import engine_mcp_server and mutate its global identity.

## Contract

One async client context belongs to one verified caller actor/graph and one route
generation (the route includes the supervisor's per-server secret). Capture the
canonical root once, read through read_engine_mcp_route, and never take a URL or
bearer from model output, tool arguments or caller-supplied transport settings.
Re-read the captured root before every list/call and refuse if flag, allowlist,
owner, graph, port or secret differs. Do not reconnect and replay an operation on
a replacement route. This is consistency, not fresh inference permission: the
existing pinned server still enforces each tool's real authority.

Use the existing FastMCP Client with StreamableHttpTransport and a fixed internal
HTTP-client factory. Disable redirects and environment proxies (trust_env=False),
provide only this route's fixed Authorization header, and never forward outer
request headers/auth even though FastMCP's default transport collects them.
No OAuth, sampling, elicitation, model-initiated roots, task execution or new
background tool jobs. Use call_tool_mcp to preserve MCP content, structuredContent
and isError without coercion or a second tools/call. Readiness discovery must use
the same session to populate its output-schema cache. The underlying MCP client
still validates successful structured results; a received-then-rejected result
therefore becomes unknown, never not_sent or a reason to repeat the tool.

List the actual server's schemas and intersect with SERVED_ENGINE_MCP_TOOLS and
the caller's explicitly supplied enabled_tools subset. A missing requested tool,
duplicate tool name, malformed schema or incomplete/looping pagination refuses
readiness; do not claim full parity from a partial list. No tool name outside that
exact validated discovered subset may dispatch. Server descriptions/results are
untrusted content, never connection or permission instructions. Keep opaque
argument values intact; do not reinterpret actor/graph fields as routing.

Transport errors carry a fixed secret-free code, not raw exceptions/URLs/headers.
Errors before sending tools/call are not_sent; after invoking it, unavailable
results are unknown, never permission to replay. An MCP isError response remains
an actual received tool result, not a transport exception or automatic retry.
No wrapper retries. Installed MCP's SSE reconnection is GET response resumption,
not another tools/call POST; cover this transport claim in the boundary tests.
Cancellation propagates without replacement; the future caller MUST durably
record tool intent before invoking this client and preserve unknown outcomes.
The client does not own or invent that journal, launch allowance or cost receipt.

Client/session/HTTP cleanup is bounded best-effort: FastMCP exit can time out or
raise while its session task remains alive. Capture results before context exit;
report only a fixed cleanup-status code, never erase a result already received
and persisted by the caller. No global client pool, no
secret-bearing repr, no shared mutable state across universes. Runtime selector
must still hold HTTP full-agent eligibility until tool loop, per-inference
admission/accounting and durable continuation are integrated and proven live.

## Source evidence and proof boundary

September10 installed-library signature/source inspection: FastMCP3.2.0
StreamableHttpTransport.connect_session merges outer Authorization with explicit
headers and passes follow_redirects=True to a custom factory. Override both at
our factory instead of relying on defaults. Client.call_tool_mcp returns the raw
MCP CallToolResult; call_tool does extra result/schema handling. The MCP transport
has GET/SSE resumption paths; verify no effectful POST reissue under failure.
These local sources are exact dependency evidence, not future-version guarantees.

Tests use synthetic route records and in-process fake MCP/HTTP endpoints only,
never the owner's live engine route, secret or workflows. Cover exact transport
construction, redirects/proxies/outer-header isolation, intersected discovery,
cross-owner/stale-route refusal, preserved structured/content/error results,
one dispatch on failure/cancellation and cleanup. Include a real protocol fixture
to avoid proving only a fake Client, and run Windows plus actual Docker Linux.
One cross-family shape/basic-safety review precedes code. No public handle,
permission/storage change or live provider/model selection in this slice.

## Shape review disposition, ADAPT411s

Independent Claude source review confirms GET-only SSE resumption and no POST
retry, real ambient-header forwarding in FastMCP, fixed-factory necessity and
canonical owner pin. Keep FastMCP's session-monitoring guard instead of inventing
a raw session wrapper. Factory accepts **kwargs and discards all inbound headers,
auth, redirect and timeout choices in favor of its fixed private configuration.
Set an explicit timeout and a no-op server log handler; no untrusted notification
text goes through the default daemon logger.

Applied corrections: same-session discovery primes output-schema validation;
validation after an executed tool is unknown. Exit is bounded best-effort, not
guaranteed resource destruction; cleanup errors cannot replace a received result.
A supervisor respawn may reuse the port/secret while invalidating the MCP session:
route equality is not liveness. Session-terminated errors refuse without reconnect
or replay. Inner-client cancellation is distinct from outer HTTP cancellation,
which drains its shielded executor. No change to the shape or downstream journal,
inference allowance and full-agent eligibility gates. Full review is in
output/engine-tool-client-shape-result.md; no extra hardening round requested.

Implementation safeguards: reject nonlocal schema references before handing a
schema to MCP's output validator, which may otherwise fetch a remote reference.
This changes no tool argument content. An ambiguous transport/validation failure
or inner cancellation makes the session unusable for further calls; it does not
reconnect automatically. The caller's durable journal remains required across
sessions/processes. Received MCP isError results are preserved, not ambiguous.

## Implementation review and proof, September10 03:23UTC

Exact5406c3e4f57cf61d9af627d3b78b4acc98be3058 independently reviewed by Claude:
APPROVE339s,45 tests reproduced on Windows. Full final artifact:
output/engine-tool-client-implementation-result.md. No gating findings. Nongating
notes: fake connect-failure cleanup differs from real SDK failure status; already
dead sessions conservatively become unknown; discovery errors use a generic code;
the direct no-op log test is tautological and actual callback wiring is source-
verified. No additional review/hardening round required for those notes.

Author verification on exact runtime/tests: Windows Python3.14,227 passed/
3 existing symlink skips16.58s; actual Docker Linux Python3.11,230 passed/0 skipped
12.27s. Commands: pytest -q tests/test_engine_tool_client.py
tests/test_engine_mcp_routes.py tests/test_engine_mcp_server.py
tests/test_engine_mcp_hardening.py tests/test_provider_sandbox.py
tests/test_codex_cli_compat.py tests/test_mirror_parity_gate.py --tb=short -rs;
Linux uses scripts/linux_oracle.py -- with that same group. Ruff/diff/precommit
passed;408 plugin mirrors/import. Tests use only synthetic routes/protocol peers.
No live caller/deployment, HTTP agent loop, journal or new inference eligibility.

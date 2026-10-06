# L7 next slice: authenticated ingress acceptance (harness only)

Decision recorded before implementation: extend the production HTTP app factory
with an explicit optional ingress adapter; normal startup passes none. The
harness keeps this frontend alive while the execution origin's listener closes.
No environment flag, deployment configuration, tunnel route, or app default is
changed. Existing MCP calls retain their transport/result semantics.

The candidate client explicitly negotiates `X-TinyAssets-Ingress: accept-v1` on
POST /mcp with a JSON-RPC converse call. Only this receipt-aware protocol may
receive HTTP 202. Its result is durable acceptance, never turn completion.
`receipt-v1` looks up the same principal/center/client_send_id without resending
work. The first adapter supports explicit graph_id, main agent, typed message,
and canonical UUIDv4 client_send_id. Unsupported arguments fail before storage.
It stores the exact request body; retries must preserve that body (including the
JSON-RPC id). Other adapters and production app negotiation remain future work.

Authentication stays in AuthContextMiddleware. The adapter derives the principal
from its request context, never a payload field; current home/admin authority is
held using conversation_run_admissions.authorized_scope across each journal
operation. Provisioning is explicit and separate; admission policy is a required
pure callback. Payload reads are bounded, and storage failure cannot acknowledge
acceptance. No executor, owner lease or competing pump is introduced.

Proof: retain the 520 RED control and previous direct-journal component proof;
add a real HTTP/browser candidate using the production app factory, authenticated
bearer boundary, real home/ACL rows and independent journal. Kill the execution
origin, accept while its listener is closed, replace the frontend process, then
read the same receipt and import twice into one existing canonical admission.
Exercise anonymous/foreign/revoked access, conflict, malformed input, oversize,
and failed commit. Keep long-turn continuity explicitly RED. Production quota,
custody/deletion, S8b pump, complete app recovery and Compose continuity remain
outside this bounded slice; full task 1.1/2.1 stay incomplete.

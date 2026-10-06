# HTTP agent inference protocol: next task2.4 boundary

September10,2026. Proposed pure translation boundary, not a live executor or a
permission/storage change. Shared owner-bound tool transport is deployed; private
HTTP tool client5406c3e4 is built and independently approved. Next connect inference
messages to those authorized tool requests without treating a text completion as
an agent. This proposal requires cross-family shape review before implementation.

## Verified source constraints

Feature protocol_encoders.py supports only prompt/system text and decodes only
nonempty assistant text. ApiKeyHttpProvider._complete_sync explicitly refuses
selected HTTP models with engine_mcp_enabled. discovery_protocols.py accepts only
simple text messages and four top-level fields before adding bounded price policy.
None of those guards may be weakened by this pure codec slice. No runtime caller
is added until its durable journal, per-inference admission and cost-bound request
shape are integrated. Existing text-only call behavior remains unchanged.

External primary documentation inspected September10,03:32UTC:
- [OpenRouter client tools](https://openrouter.ai/docs/guides/features/tool-calling):
  requests carry function schemas; model tool calls are executed by the client;
  follow-up messages include the assistant call and correlated tool results.
  Tool schemas must accompany follow-up inference too.
- [Reasoning preservation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens):
  assistant continuation can include reasoning or reasoning_details, and the
  returned sequence must be preserved rather than rearranged. These are opaque
  continuation data, not instructions, display content or permission evidence.

## Proposed bounded implementation

Add a private pure agent-chat codec for the existing openai_chat wire protocol,
covering OpenRouter and compatible endpoints without model/vendor names. Do not
change the legacy encoder/decoder or make anthropic_messages falsely tool-ready.
The latter retains its existing text-only eligibility until its own wire codec
is integrated. No public handle, URL discovery, credential, network or SDK call.

Use a detached immutable response record containing optional text, ordered tool
requests and a JSON snapshot of the assistant continuation. A tool request carries
the provider call id, exact tool name and strict JSON-object arguments. Response
decoding validates the entire tool-call batch before returning any request:
nonempty unique ids, function type, valid names/argument JSON, no duplicate keys,
NaN or non-object arguments. No dispatch happens in the codec. Unknown custom
tool types, incomplete/truncated calls or incompatible response shapes fail with
fixed diagnostics rather than executing a partial batch or fabricating text.
Terminal assistant text is distinct from a response awaiting tool execution.
Tool-only responses with null content are valid. Refusals and incomplete replies
must not be called normal completed answers. Optional usage remains unknown when
missing/malformed; model identity uses existing reported_model validation.

The request builder accepts trusted prompt/system/model, the caller's actual
validated discovered tool schemas, and completed assistant/tool exchanges. It
never accepts top-level provider/routing/URL/header/plugin overrides. Every
request carries the current exact tool schemas; no provider tool execution or
server-side plugins. Preserve each pending tool id and every completed result
exactly, in order. Reject missing/duplicate/orphan results rather than inventing
them. Canonical MCP results (content, structuredContent and isError) are JSON
tool-result content, not promoted into user/system instructions. Tool availability
and fresh authority remain enforced by the existing tool client/server, not by
anything the model supplied.

Keep assistant reasoning fields in an opaque, detached JSON snapshot for the
same source's continuation, never in UI/log text. Do not promise signatures work
across another model/provider: cross-model continuation requires an explicit
reviewed conversion policy and the durable completed-result journal. This codec
does not retry inference, reconnect tools, select models or replay effects. Do
not silently strip an unsupported content block to manufacture compatibility.

## Gates to review before code

Resolve the exact response record and request-builder inputs against the smallest
future journal seam; avoid a second mutable transcript store. Determine finish-
reason/refusal semantics and safe handling of optional reasoning blocks, unknown
metadata and malformed tool batches. Preserve uploaded/user text verbatim, no
truncation as a recovery strategy. Test round trips from returned assistant calls
and exact MCP results, detached data, no mutation, full-batch refusal, error/usage
semantics and unchanged legacy codecs. Synthetic fixtures only; actual authorized
tool execution and free-only accounting are subsequent integration acceptance,
not proven by codec tests. Do not expose an enabled HTTP model picker yet.

## Applied shape review ADAPT300s

Recovered the complete reviewer text from this exact dispatch's local session
record (the stop-hook had replaced its final stdout with a shorter recap), saved
in output/agent-chat-protocol-shape-full.md. No inferred approval or new review.
Use a separate agent_chat_codec module, not ENCODERS or ProviderResponse; the
latter remains a terminal-answer projection only. No runtime caller is added.

Pin the eight cases before code: exactly one choice; reject top-level/per-choice
errors; message.refusal is a separate non-completed stop; calls with stop or
tool_calls can proceed but length+calls rejects the entire batch; text length,
content_filter and unknown finish reasons are non-completed; empty-string
arguments and missing/blank ids are rejected, never repaired. Reject non-text
MCP content in this text-wire slice rather than serialize base64 as usable text;
that is an explicit compatibility limit, not proof all future tools are text.
Project exactly content/structuredContent/isError, excluding envelope metadata
and extras. Text block annotations survive, unrelated block metadata does not.
Assistant continuation allows role/content/tool_calls/reasoning/reasoning_details
only; record dropped keys and refuse continuation if any had nonempty values.
Terminal text may survive unrelated metadata, but nonempty legacy function_call
or audio fields hold even a text-bearing stop; they are unsupported content, not
metadata to silently discard. No hidden tool can dispatch.

Tool names use the protocol's1–64 ASCII identifier contract; arguments are strict
JSON objects with no duplicate keys or nonfinite numbers, preserved as exact wire
strings. Descriptions are bounded at64Ki characters, never truncated. Do not set
strict on definitions. Whole batches validate before any request is exposed.
Frozen records store JSON snapshots, not mutable provider objects. Both source_ref
and requested_model are explicit trusted correlation inputs so even matching model
names cannot carry opaque continuation across different connections. This adds
the source distinction required by the reviewed same-source continuation rule.
The future journal owns persistence; the codec owns no transcript store.

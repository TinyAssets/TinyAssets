# Bring your own model

Hosted TinyAssets calls a model you connect. The app is at
[https://tinyassets.io/app](https://tinyassets.io/app). The public connector is
[https://tinyassets.io/mcp](https://tinyassets.io/mcp). This page is that
connection. It describes the hosted app reaching a model you supply.

Checked against the code and the tests named below on 2026-10-11. Anything
not exercised against a live server is marked TODO.

## The generic path

A model connection is an HTTPS endpoint, a credential, a model id, and a wire
shape. The wire describes how the request is built. It does not name a vendor.
The two installed shapes live in `tinyassets/providers/dialects/`:

| Shape in the app | Document | What it speaks | Agent tools |
|---|---|---|---|
| Chat messages (most APIs and servers) | `chat_messages.json` (stored alias `openai_chat`) | OpenAI Chat Completions. Default path `/v1/chat/completions`. | Yes. `tests/test_unify_connection_uses.py` asserts `agent_codec_for("chat_messages")` is installed. |
| Content blocks | `content_blocks.json` (stored alias `anthropic_messages`) | Anthropic Messages. Default path `/v1/messages`, plus the `anthropic-version: 2023-06-01` header. | No. The same test asserts `agent_codec_for("content_blocks")` is `None`. |

`tests/test_unify_connection_uses.py::test_bundled_dialects_resolve_by_structure_and_stored_alias`
asserts those are the only two dialect names. A third name fails as
`UnknownDialect`. Adding a vendor does not require a new Python provider when
the server already speaks one of these two shapes.

The executor is `ApiKeyHttpProvider`
(`tinyassets/providers/api_key_http_provider.py`). It sends the body through
the credential-blind outbound proxy. The path you grant is the path it calls:
`tests/test_api_key_http_provider.py::test_a_custom_granted_path_is_the_path_called`
posts to `https://api.example.com/custom/chat` when that path is the grant,
instead of rewriting it to `/v1/chat/completions`.

Registration of a provider definition is a candidate, not authority
(`tinyassets/providers/definition.py`). The router
(`tinyassets/providers/router.py`) serves only the provider the command
center's owner authorized, on that owner's credential. `FALLBACK_CHAINS` in
that file is a catalogue of built-in executor names. It is not a live fallback
onto a platform model. `ollama-local` is in that catalogue and cannot serve a
command center: `OllamaProvider` declares `credential_source = "host_process"`
and talks to `http://localhost:11434/api/generate`, and
`tinyassets/providers/owner_binding.py` refuses host-process credentials.

## Connect it in the app

Open **Connect a model**, then **Other ways to connect**.

- **API key** and **Your own server** are the same form
  (`connectEndpoint` in `tinyassets/onboarding/app.html`). The second label's
  help text says a server you run must be reachable over HTTPS, for example
  through a tunnel, and that TinyAssets cannot reach localhost.
- Paste the full `https://` URL the model answers on, including the path
  (`https://example.com/v1/chat/completions` or another path you control).
- Paste a bearer API key and the model id that server expects.
- Choose **Chat messages** unless the server is an Anthropic Messages endpoint.

The form checks, before anything is sent: `https` only, no username, password,
query, or fragment, a model id, and a non-empty printable key of at most 4096
characters. It then asks you to confirm. The key is cleared from the page and
stored in the command center vault. The confirmation records
`auth_scheme: "bearer"`, the host and path you pasted, wire
`chat_messages` or `content_blocks`, `tools: true`, `context: 32768`, and
`billing: "flat"`.

The same screen also offers installed presets. Those are data on the chat
messages wire, not separate executors: Groq, Google AI Studio (the OpenAI-compatible
Gemini base `https://generativelanguage.googleapis.com/v1beta/openai`), Mistral,
and Cerebras (`tinyassets/providers/free_source_presets.json`). OpenRouter is
the guided key card (`tinyassets/providers/acquisition_presets.json`) and its
catalogue document. Hugging Face sign-in is installed and marked unavailable
(`available: false`).

ChatGPT subscription sign-in is a separate device flow
(`/app/openai/device/start` in the same page), not this form. Codex and Claude
Code are the only subscription CLI executors
(`tinyassets/providers/provider_resolver.py`). They run on the machine that
hosts the command center.

## Servers that match chat messages

These are the same path when the server speaks Chat Completions, accepts a
bearer token, and is on a public HTTPS URL. No vendor module is required.
Which of them succeed against a live server was not run for this page.

| Server | Use this URL shape | Status |
|---|---|---|
| OpenAI Chat Completions | `https://api.openai.com/v1/chat/completions` | Matches the installed chat-messages body. TODO: live call. |
| OpenRouter, LiteLLM, and other OpenAI gateways | The gateway's chat-completions URL | OpenRouter's guided card is installed. A gateway you paste yourself uses the generic form. TODO: live call through a gateway other than the OpenRouter card. |
| Google Gemini | The OpenAI-compatible base in the Google AI Studio card, plus `/chat/completions` | Native `generateContent` has no dialect. TODO: live call through the compatibility base. |
| Ollama | The OpenAI shim, path `/v1/chat/completions`, on a public HTTPS tunnel | Native `POST /api/generate` is the host-only `ollama-local` executor, which a command center cannot use. TODO: live call through a tunnel. |
| llama.cpp server, vLLM, LM Studio | Each server's OpenAI-compatible chat-completions URL, on a public HTTPS tunnel | No dedicated module. TODO: live call. |
| Anthropic Messages | Path `/v1/messages`, shape **Content blocks** | Text dialect only. An agent turn that needs tools will not select it (`executor_unsupported` in `tinyassets/providers/model_policy.py` when the dialect has no tool codec). The form still sends the key as `Authorization: Bearer`. The broker can place a secret in a named header (`auth_scheme` `header` in `tinyassets/storage/outbound_connections.py`); this form does not offer that. TODO: whether the live Messages API accepts Bearer. |

## What the hosted app can reach

The outbound proxy allows HTTPS only
(`_parse_canonical_https_url` raises `outbound url scheme must be https`).
After DNS, the address must be globally routable.
`tests/test_outbound_ssrf_driver.py::test_nonglobal_ip_literals_are_refused`
refuses loopback (`127.0.0.1`, `::1`), private ranges (`10.0.0.0/8`,
`192.168.0.0/16`, `172.16.0.0/12`), link-local metadata
(`169.254.169.254`), and similar non-global addresses. Redirects and
environment proxies do not bypass that check.

A model on your own machine is reachable when you publish it at a public
HTTPS name (a tunnel is the usual way) and connect that name. The hosted app
does not dial `localhost`, a LAN address, or plain HTTP. There is no reverse
connection and no desktop relay: `tinyassets/desktop/` is a window on the
hosted app. `openspec/changes/private-network-attach/` is a proposal for a
later owner-network route. It has no product code.

## Tools, streaming, and context

The form declares tool support and a 32768-token context. It does not ask the
server. The comment above `ENDPOINT_CONTEXT` in `app.html` says that number is
a bound for the model use, not a measurement.

Agent requests on chat messages always set `"stream": true`
(`tinyassets/providers/agent_inference.py`). The decoder accepts a JSON body
or a folded event stream. A server that rejects `stream` or `tools` fails the
call. There is no probe that retries without tools. `model_policy` will refuse
a model whose declared `tools` is not true when the turn needs tools, and will
refuse a dialect that has no tool codec. A weaker local model that accepts the
connection and then returns an error is a failed turn.

TODO: the exact sentence an owner sees when a small model returns 400 because
it does not implement tools or streaming. The capacity path keeps the HTTP
status and a scrubbed body (`ApiKeyHttpProvider._capacity_detail`). A protocol
mismatch raises `ProtocolDecodeError` and fails loud.

## Security of a tunnel

A tunnel hostname is a public endpoint. Put a real secret on it. The connect
form requires a key and sends it as a bearer token, so an open server that
rejects `Authorization` will not connect through this form. The key is stored
in your command center's vault, not in the chat.

The SSRF checks above are what stop the platform from being aimed at loopback
or cloud metadata. Leave them in place. A private path to a machine that is
not on the public internet is the unpublished `private-network-attach`
proposal, not an exception for `localhost`.

## Methods this form does not cover

The broker already has more connection machinery than the model form shows.
These are gaps in the generic model path, not missing vendor modules:

- **Auth.** The form always uses bearer. The broker also implements `none`,
  `basic`, `header` (a named header such as `x-api-key`), `oauth2`, `oauth1a`,
  and `url_secret` (`_SUPPORTED_HTTP_AUTH_SCHEMES`). A local server with no key
  cannot be saved from this form.
- **Extra headers.** Constant headers can be set on a connection
  (`connection/configure` in `tinyassets/engine_mcp_server.py`). This form does
  not collect them.
- **Other bodies.** OpenAI Responses (`/v1/responses`), Gemini
  `generateContent`, and Ollama `/api/generate` are not dialect documents.
- **OAuth on an arbitrary model host.** Generic OAuth exists for a connect ask
  that carries discovered OAuth (`tinyassets/connection_oauth/`). This form
  does not start it. Subscription CLIs stay the closed pair Codex and Claude
  Code, running on the host that serves the command center.
- **MCP as a model.** User MCP servers are not a wire dialect and are not a
  `connection_type` (`_KNOWN_CONNECTION_TYPES` is empty-or-`http`). MCP in an
  extension manifest is a separate catalog entry
  (`tinyassets/extension_capabilities.py`).
- **A model that only listens on your LAN.** Refused by the address check.
  TODO: no live tunnel (Cloudflare, ngrok, Tailscale Funnel, or similar) was
  connected during this check.

`openspec/specs/provider-routing/spec.md` still describes role chains that
end at `ollama-local`. The router docstring and `owner_binding.py` are the
live rule: a command center is served by the owner's connected provider, and
the host's local Ollama is not that provider.

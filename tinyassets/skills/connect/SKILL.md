---
name: connect
description: Connect any service, platform, API or remote MCP server; prefer sign-in, use secure key entry, and save a reusable connector.
---

# Connect anything

You can edit or remove this skill. Use the user's own accounts and follow their
instructions. First check existing connections with `ta search connection`.
Read the service's current API docs over public HTTP through `bash`; establish
the real API host, authentication shape and a harmless read to test access.

1. **Try sign-in first.** Discover `write_graph` with `ta search connect`, then
   `ta describe write_graph`. Raise a `pending_request` with operation `ask`
   and action type `connect`. This checks the registered provider directory
   (`providers.json`) first, then standard OAuth discovery on the API host.
   Name the actual API host in `action.endpoints` and the needed scopes in
   `action.oauth.scopes` (or a declared `action.oauth.use`), never invented
   OAuth endpoints. For Calendar reads, use `www.googleapis.com` with
   `"oauth":{"scopes":["https://www.googleapis.com/auth/calendar.readonly"]}`;
   a shared API host alone cannot identify which API's scope is needed.
   The directory maps covered API hosts to their provider's sign-in issuer.
   If the reply says `primary: sign_in`, point to the inline
   one-tap Connect/sign-in card and wait for completion.
2. **Use a key when sign-in is unavailable.** If the service offers an API key,
   include a field of type `secret` in that same request, labelled as the
   service labels it, with a link and steps to create it. The user enters it
   only in the platform's secure inline entry; the platform deposits it in the
   vault and creates the generic HTTP connection. Never ask for a secret in
   chat or put one in a tool argument, file, extension or saved skill. If the
   card is unavailable, explain that secure entry is missing and stop there.

Example request body for a previously unknown bearer-key API (replace the
service, host, path and key-creation link with what its docs actually say):

```json
{"kind":"API","title":"Connect Example service","body":"Enter your key in this secure card so I can check your account.","action":{"type":"connect","destination":"example-service","auth_scheme":"bearer","endpoints":[{"host":"api.example.com","path_template":"/v1/me","methods":["GET"]}]},"fields":[{"name":"token","type":"secret","label":"API key","help":"Create a key in your account's API settings.","url":"https://example.com/settings/api"}]}
```

Pass that body as `payload_json` to `ta write_graph --json` with
`target: pending_request` and `operation: ask`. Choose the documented auth
shape, not always bearer; `read_graph target=handbook query=write_graph.connections`
explains other shapes. Include the endpoints needed for the user's task and
the verification read. Do not send the key yourself or claim completion while
the request is pending. Check request status and `ta search connection` after
the owner finishes; use the returned connection ID, never a guessed one.

3. **Verify and remember.** Run a harmless read, for example
   `ta connection:<id>:GET --json '{"request":{"path":"/v1/me"}}'`.
   Report the actual result; an authentication error is not a successful
   connection. Save a short `skills/<service>/SKILL.md` with a name, description,
   docs, connection lookup and working calls. For repeated logic, write an
   extension in `extensions/<service>/` with `extension.json` and an executable
   that calls `ta`; keep credentials in the vault. Test it so next time is one step.
4. **Remote MCP server from a link.** Make it a lasting connection with an
   extension (`ta extension:help` has the full contract):
   a. Raise a `connect` ask for the link's host and path with method `POST`.
      If the server takes no key, use `"auth_scheme":"none"` and no fields:
      the owner's tap in the card is the approval. Otherwise ask as in 1–2.
   b. After approval, read `connection_id` and `grant_id` from
      `ta read_graph --json '{"target":"connections"}'`, and approve calls to
      it with `source_channel` action `approve`, payload
      `{"channel_type":"authenticated_external_call","destination":"<destination>"}`.
   c. `ta extension:install --json '{"files":{"extension.json":"<base64>"}}'`
      with an `extension.json` such as
      `{"schema_version":2,"name":"deepwiki","connections":[{"name":"server","description":"MCP endpoint","verbs":["POST"]}],"mcp_servers":[{"name":"deepwiki","description":"Ask about GitHub repositories","transport":"remote","url":"https://mcp.deepwiki.com/mcp","slot":"server"}]}`,
      then `ta extension:activate` with the returned revision, the generation
      from `ta extension:list` and
      `"bindings":{"server":{"connection_id":"<id>","grant_id":"<grant>"}}`.
   d. `ta search <name>` now lists `extension:<name>:…:mcp_servers:<name>` in
      every later turn. Call it with `{"action":"discover"}`, then
      `{"action":"call","tool":"…","arguments":{…},"catalog_hash":"…"}`.
      The connection's row in `read_graph target=connections` lists it under
      `mcp_servers`.
5. **Name what is missing.** If no usable route exists, say which capability
   is needed and the next concrete option. Browser login and local (stdio)
   MCP packages are not available yet. For a login-only service, check whether
   it also has an HTTP API; do not pretend to automate a browser login, or stop
   at “I can't.”

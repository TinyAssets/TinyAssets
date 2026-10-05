---
name: connect
description: Connect any service, platform or API; prefer sign-in, use secure key entry, and save a reusable connector.
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
   Supply the needed OAuth scopes/use from the docs, never invented OAuth
   endpoints. If the reply says `primary: sign_in`, point to the inline
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
4. **Name what is missing.** If no usable route exists, say which capability
   is needed and the next concrete option. MCP server attachment and browser
   login are not available yet. For an MCP-only or login-only service, explain
   that gap and check whether it also has an HTTP API; do not pretend to attach
   MCP, automate a browser login, or stop at “I can't.”

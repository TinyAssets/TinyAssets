- [x] 1 Key store: mint (secret once, digest stored), list, revoke, resolve.
- [x] 2 Each key is an outside client with exact grants; revoke retires it.
- [x] 3 Auth middleware: `/api` accepts only keys; keys never reach OAuth.
- [x] 4 REST routes over the handle functions with per-route grant checks.
- [x] 5 Owner-session key management route `/app/api-keys`.
- [x] 6 Worker route and proxy for `/api`; fact 11 and architecture map.
- [ ] 7 Live: confirm the outside switch state, deploy, mint a key, call each
      route once against tinyassets.io.
- [x] 8 Needs You credential request (owner review, create with scopes, copy once,
      request-bound recovery and revoke), plus Account key list/revoke; local tested.
- [ ] 9 Website `/developers`: document `/api` once task 7 passes.

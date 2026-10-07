I've read the diff and the new files, and I'm checking one last behavior: whether the new pump checkpoint changes non-git streams.

**VERDICT: APPROVE**

I found no floor or correctness defect that blocks the fold-in. This was a code read only: I ran no tests and edited nothing, and I relied on your report of 174 passing Linux tests.

**Floor checks, all of which hold:**
- **The token never reaches the box.**
  - The box only gets `GIT_CONFIG_*` `insteadOf` rewrites. Each points to an opaque 128-bit route on `ta-git.invalid` (`tinyassets/git_egress.py:183-194`).
  - Requests from the box that carry `authorization`, `proxy-authorization` or `content-encoding` are refused (`git_egress.py:97-99`). None of the box's headers are forwarded upstream.
  - The credential is resolved only in the broker (`broker/git_http.py:147-161`). The response passes through `BrokerStream` with `sensitive` scanning, and any non-200 is refused before it streams (`git_http.py:179-183`).
- **Grant pins and exact scopes hold.**
  - Routes come only from active, enabled extension revisions. `service.connection()` checks every declared verb against the ceiling and requires `match.grant_id == pin.grant_id` and the same incarnation (`extension_capabilities.py:121-141`). Because of that, the grant taken from `current[...]` at `extension_git.py:261` is the pinned grant.
  - The narrowed `replace(view, scopes=...)` copy is only checked on the box side. The broker checks again against the real ledger: verb in `resource.scopes`, an exact (method, target, upload) pair, the host, and the incarnation (`git_http.py:82-98, 121-130`).
  - Pull versus push is decided by the exact target (`?service=git-receive-pack` is a write).
- **No cross-user effect.**
  - Routes are keyed by `(id(proxy), secret)`, and there is one proxy per universe root.
  - The broker OPEN runs `authorize_exact` (principal, grant owner, command center, connection) and repeats it on every checkpoint (`server.py:404-405, 436-439`).
  - The route dies with the launch: `live.clear()`, the route is dropped and its sockets are shut down (`git_egress.py:195-202`). The fence generation is also checked per request.
- **Failing to set up git doesn't take bash down.** Every setup failure, including `PermissionError` (an `OSError`) and `ExtensionError` (a `ValueError`), turns into a printed message on stderr (`extension_git.py:217-219`).

**Findings, none blocking:**

1. **`tinyassets/broker/server.py:539` and `:548`: the git-only checks now run on every stream type.**
   - What changed: the response pump now calls `_checkpoint` on every loop, and its wait is capped at 0.1s.
   - Before, the fence check ran only around sends. Now a fence rotation during a non-git response (for example, a long inference stream) ends it as `cancelled/fenced` with `side_effect_state: unknown`. Before, the response would have finished.
   - The 100ms polling also runs on every stream. For git streams it repeats an SQLite authority query while `stream.wake` is held.
   - Fix: wrap both changes in `if stream.authority is not None` (or check `hasattr(upstream, "check_authority")`) so streams that aren't git keep their old behavior. It's an honest outcome rather than data loss, but it widens the lane past git.

2. **`tinyassets/extension_git.py:233-243`: one broken binding turns off git for every extension.**
   - What happens: if `service.connection()` raises for any one active extension (say, a changed incarnation), the whole launch falls back to "Authenticated git unavailable".
   - Failing closed is correct, but the message doesn't say which binding caused it. Naming the extension in the message would help.

The prior remote review's deferred-frame issue isn't repeated here. Out-of-order frames are refused explicitly: DATA before HEAD, and HEAD before the upload ends (`git_client.py:32-33, 49-50`).


Dispositions: AGREE with limiting authority polling to git streams; changed the
pump's checkpoint and wait interval to preserve other stream behavior. AGREE
that one broken binding conservatively disables launch git routes; keep the
visible fixed diagnostic and no credential-bearing fallback. No floor findings.

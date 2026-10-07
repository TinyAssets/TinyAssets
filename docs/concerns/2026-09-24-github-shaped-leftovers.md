---
severity: P2
title: GitHub-shaped leftovers
filed: '2026-09-24'
summary: 'after GitHub became an ordinary connection: the repository-spec automation still needs a GitHub pipe no surface creates, 4 stranded pipe rows in prod, and a declared `git_host` can only be set by remove + reconnect'
---

# GitHub-shaped leftovers after "GitHub is an ordinary connection"

**Filed:** 2026-09-24
**Verified:** 2026-09-24, against `origin/main` 4abd0ede plus branch `claude/github-via-owner-connection`
**Severity:** P2 (nothing here sends a platform credential; each item is a GitHub-shaped path or table that should go)

## Source (verbatim)

Founder, 2026-09-24, relayed by the coordinator: *"GitHub is NOT special; it is just another
connection. Do NOT build a GitHub-specific owner-credential path, fail-closed message, or
'Connect GitHub' request. Instead, CUT the GitHub-specific machinery [...] A user who wants their
universe to use GitHub connects it like any service through the generic `connect` action, and
their own workflows call it through the generic authenticated external call."*

## What the branch removed

- `open_auto_ship_pr`, `tinyassets/auto_ship_pr.py`, and `credential_vault.resolve_github_token`
  (a GitHub-only PR opener reading a GitHub-only vault record).
- The hidden legacy `community_change_context` tool, which sent the daemon's own `GH_TOKEN` with
  every GitHub read (`GH_TOKEN` is set in the running daemon, checked by name on 2026-09-24).
- The WorkOS Pipes GitHub setup (`write_graph target=connection operation=connect|reconcile`,
  `tinyassets/workos_pipes.py`). Nothing resolved its `workos-pipes://github/<actor>` credential
  reference, so every connection it created could never dispatch.
- `TINYASSETS_GITHUB_OUTBOUND_VIA_CONNECTION` from `apply-daemon-env.yml`: no code has read it
  since `outbound_channel_adapter.py` was deleted.
- `FORGE_GIT_HOSTS` and `PROVIDER_PIPE_HOSTS`. A connection now carries an optional,
  owner-declared `git_host` (set on the connect ask); git uses it, else the connection's own
  endpoint host. **Consequence:** the founder's existing GitHub connection (endpoints on
  `api.github.com` only, no `git_host`) now clones from `api.github.com` and gets a 403 until the
  founder removes it and reconnects with `git_host: "github.com"`.

## What is left, and what would close it

1. **The `repository_spec_delivery` cloud automation still requires a GitHub pipe as its
   destination.** `user_owned_cloud_automation.py` (`connection_class == "pull-request-writer"`,
   `provider == "github"`) and `api/cloud_automations.py` require a grant that no surface can
   create any more. The hint now says so (`connection_action.status = "unavailable"`); its sibling
   `provider_action` already pointed at the fleet-era `bind_provider` operation, which
   `universe_server.py` says was removed. **Close:** delete the automation family, or rebuild it
   as a user graph over an ordinary connection. Either choice touches storage and a public
   operation, so it needs its own change.
2. **Four stranded pipe rows in production.** `/data/outbound.db` holds 4 active
   `pull-request-writer` / `github` / `workos-pipes:` connections next to 7 `http` ones (counted
   2026-09-24, no values read). `read_graph target=connections` still lists them as `connected`,
   but they cannot dispatch. **Close:** a host-run revoke, or a list projection that marks a
   connection with no resolvable credential.
3. **`git_host` cannot be changed in place.** Setting it on an existing connection is a remove and
   reconnect (a re-deposit with a different `git_host` is refused as a conflict), because it
   moves where the owner's key is sent. **Close,** if the friction proves real: an owner-answered
   `extend_http` that sets `git_host` under the same compare-and-swap as other extensions.
4. **Platform token still in the daemon.** `GH_TOKEN`, `TINYASSETS_GITHUB_PUSH_CAPABILITIES` and
   `TINYASSETS_GITHUB_OUTBOUND_VIA_CONNECTION` are still in `/etc/tinyassets/env`, and
   `auth/provider.py` `vend_github_destination_secret` has no callers. The platform-token removal
   is a parallel lane; this branch leaves it alone.
5. **Smaller GitHub-shaped leftovers:** `Dockerfile` still copies `PLAN.md` for the removed review
   reader; `evaluation/patch_notes.py` has a `github_pr` evidence kind;
   `webhook_inbound.py` passes through `x-github-*` headers; the `vcs` vault credential type has no
   reader now; the `TINYASSETS_SLACK_/TWITTER_OUTBOUND_VIA_CONNECTION` flags are just as inert as
   the GitHub one was; `openspec/changes/archive/2026-10-06-channel-agnostic-outbound/` still describes the deleted
   adapter.

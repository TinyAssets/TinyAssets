# Tasks: rename-universe-to-command-center

C0 is copy and is already approved by the founder. Design approval gates C1 and
later.

## C0. Copy

- [ ] 0.1 (Partly shipped in #4182: Switch command center, Welcome, commander.,
      header, sign-in and connections copy.) App: rename every user-visible "universe" in `app.html` and
      `app_ui.js`. "Switch UI" becomes "Switch command center", and the empty
      thread becomes "Welcome, commander.". Keys and ids stay.
- [ ] 0.2 Agent voice: served guidance, `api/prompts.py` server instructions and
      `control_station`, persona/seed text, and the `universe_tools.py`
      self-description. Rewrite sentences rather than swap words, so the
      engine block stays within the 30,000 ratchet (D5).
- [ ] 0.3 Website pages, `llms.txt`, plugin display name/description,
      store-listing copy in the repo, and native strings (Android channel text
      with an always-update `ensureChannel`, iOS mic string, mobile/desktop
      loading pages, D9). Rebuild the plugin mirror.

## C1. MCP surface (clean cutover, D3/D4)

- [ ] 1.1 One authority, `tinyassets/command_center_names.py`. It derives every
      name by rule, refuses retired names and values naming the new one, maps
      current values to the handlers' names until the cutover, and respells
      responses (keys, error codes, presented actor ids). A person's content
      is left verbatim.
- [ ] 1.2 First-party readers switch outright: the website read contract and
      its baked snapshot, `mcp_tool_canary.py`, the app's `Owner.read` targets,
      and the bridge's `whoami()` (production holds 0 stored bundles that use
      it).
- [ ] 1.3 Server: `command_center_id` on read_page / write_page / get_status,
      the middleware innermost on both servers, direct callers migrated,
      `get_status` at `schema_version` 3, and the default agent republished
      under a new definition id.
- [ ] 1.4 Rename `meet_universe` to `meet_command_center` and sync the
      `live-mcp-connector-surface` delta.
- [ ] 1.5 Tests: every refusal names its replacement, the advertised surface
      contains no retired name, the edge respells, content stays verbatim.
      Each test is mutation-checked.
- [ ] 1.6 Post-deploy evidence: `mcp_public_canary.py --assert-handles` green,
      `deployed_sha.py --assert-contains`, and a rendered `ui-test`
      conversation that calls with a retired name and gets the pointer.

## C2. Living docs

- [ ] 2.1 `docs/architecture.md` (Names, in place), README, skills, `docs/reference`,
      `openspec/specs`, and the two capability dir renames, updating every
      reference. Dated records are untouched.

## Later

- [ ] 3.1 C4a: the layout guard and the migration-aware `deploy_fail_safe`, as
      their own image, running in production for at least one day (D7.2).
- [ ] 3.2 Open and run `command-center-cutover` (D10/D11): codemod, storage
      and id migration (`u-` to `cc-`), and external records, in one freeze
      window you open and the measured quiet window. The dry run, backup,
      verification and rollback in D7 apply. Delete C1's edge translation after.

Now the relevant code.

Reading the package store and hook runtime to check whether the pre-U1 slice can actually run exact bytes; then I'll write up findings.

**VERDICT: ADAPT.** The direction is sound: one manifest, one content-hashed revision, generation fencing, inert imports and U1 owning admission. But the pre-U1 slice as written depends on runtime pieces that don't exist, and three authority sources are left undefined. All findings are against `openspec/changes/one-extension-unit/`. I reviewed it myself, read-only, with no sub-agents and no tests run.

### Floor and correctness findings

1. **The jail can't reach "exact bytes" without changing the launcher, which the proposal rules out.**
   - Task 2.4 (`tasks.md:12`) and design.md § *Execution boundary and U1 handoff* run tools in today's jail "with exact bytes". But the package blob store sits outside every command-center folder "so no agent environment reaches it" (`tinyassets/command_center_packages.py:18-21`).
   - The jail only mounts workspace entries, and agent harness dirs are mounted writable (`tinyassets/universe_tools.py:374-381`).
   - `ta_cli` today runs straight from `/u/extensions` and `/u/agents/<id>/extensions`, which the agent can edit (`tinyassets/ta_capabilities.py:90-93`, `tinyassets/ta_cli.py:61,124`).
   - Pinning a revision therefore needs a per-launch read-only copy of the installed revision mounted into the jail. That changes `universe_tools` mount construction, while `proposal.md` Impact says "no … privileged launcher changes".
   - Without that mount, the spec scenario "Editing an installed package → revision unchanged" (`spec.md:8-10`) is false at dispatch time.
   - **Fix:** either allow the read-only mount explicitly (with Linux-oracle proof), or narrow 2.4 to "unavailable until mount/U1".

2. **Revocation checks can't run inside the jail; only the daemon can enforce them.**
   - `ta_cli.py` is the jailed, agent-controlled client. "Every dispatch … rechecks revocation" (design.md § *Lifecycle and permissions*) does nothing if `ta_cli` does the check, because the agent can run the materialized executable directly from bash.
   - Before U1 that is not an escalation, since the authority equals the launch. But the spec promises "no contribution dispatch" after revoke (`spec.md:16-18, 23-25`).
   - **Fix:** state that the fence is enforced (a) by which revisions get mounted at launch, and (b) daemon-side for every socket, UI and workflow effect.
   - Also state that a revoke landing mid-launch doesn't unmount code already mounted for that bash invocation before U1. Otherwise the spec overclaims.

3. **Hooks have no executor or authority source before U1.**
   - Hooks fire on lifecycle events that happen outside any bash invocation.
   - The ta socket exists only for one bash launch, and only when `launch_tools()` includes `bash` (`ta_capabilities.py:193-210`). No daemon may execute package code (design.md § *Execution boundary and U1 handoff*).
   - So no jail exists for a hook, and nothing defines which grant it runs under. Starting one is a launcher change and a new authority decision.
   - The prior lane hit the same blocker and recorded it in `docs/concerns/harness-control-runtime-dependencies.md`.
   - **Fix:** move hooks out of 2.4 and into the U1-gated 2.7, or spec the hook launch's authority explicitly. My recommendation is a separate launch with the grant of the triggering turn, and none when there is no turn.

4. **UI card calls can't go "through ta's capability gate".**
   - That gate is `JailBridge`, a socket that lives for one bash launch (`ta_capabilities.py:134-190`).
   - A card's callback comes from the owner's browser, where there is no launch, so the design has no defined authority for it.
   - **Fix:** name the daemon-side path (owner session plus installation generation check), or report cards unavailable in the pre-U1 slice.

5. **Two authorities decide whether an extension is enabled.**
   - `harness_settings` already parses `extensions.enabled` and `starter.hooks` from the workspace-editable settings file (`tinyassets/harness_settings.py:146-175`). K1 adds control-plane activation state.
   - The design says settings "remain their own work" but never ranks the two.
   - **Fix:** settings may only narrow; they can never activate or reactivate a revoked or other revision. Add a negative test.

6. **The install source and visibility checks are unspecified, which is a cross-user floor risk.**
   - `ta install` could take working files or a package/blob digest; the design doesn't say which.
   - If it accepts a digest, it must enforce ownership or visibility (`blob_owned`, `command_center_packages.py:1216`) and go through the existing install pin and owner consent with `scan_install` review. Otherwise a ta call could install another author's private blob, or probe whether it exists.
   - If the source is working files, the daemon must read them as untrusted (`O_NOFOLLOW`, open one component at a time, as `write_new_file` does).
   - **Fix:** state both, and add a foreign-digest negative test to 2.2 / 2.6.

7. **The revision identity doesn't match the existing blob format.**
   - The design hashes a canonical manifest plus a path/byte map per extension, but it reuses a store whose blobs are whole-package JSON validated by `check_blob` (`command_center_packages.py:996-1028, 1248`).
   - It needs to define the blob kind or format for an extension revision, how quota is charged (`store_blob` records `author_id`), and how a recipient's revision relates to the author's package digest after `plan_install` lands the files into the writable `agents/<slug>/` tree (`command_center_packages.py:1562-1628`).

8. **Activation authority for delegated calls is only half defined.**
   - "Research/delegated callers cannot mutate" relies on `ExecutionContext.delegated_authority`. Today `dispatch` refuses only `research` and `check_authority()` failures (`ta_capabilities.py:70-75`).
   - **Fix:** 2.2 should name the exact predicate (`delegated_authority == "serving-owner"` and `approval_id is None`, or whatever is intended) and test an outside-agent or addressed-agent launch being refused.

### Feasible pre-U1 slice
- Do now: v2 validation, revision digest, owner-bound lifecycle state with generation and conflicts, discovery that reports per-contribution availability, and inert sharing with local bindings.
- In-jail tools and commands can ship only if finding 1's read-only revision mount is allowed.
- Hooks, cards, scoped connections and MCP should report explicit "unavailable" results until U1. That matches the design's own "no fake connected/success" rule.

### Lane collisions (reported, not worked around)
- **`starter-agent-out-of-plumbing` task 1.3** (the starter hook loader the settings toggle uses) is a named dependency in the harness concern. K1 neither folds it in nor lists it as a dependency.
- **`command-center-recipient-updates` / the L5 executable-update policy:** the concern says L5 keeps executable recipient updates. K1 says "code updates invalidate activation" and supersedes the harness extension tasks. Ownership of executable updates is now ambiguous. `command_center_update_policy.POLICY` must not be widened implicitly.
- **Bookkeeping is missing from `tasks.md`:**
  - Mark the superseded tasks in `command-center-harness-control`, `saved-agent-connectors`, `connect-anything-ladder` and `command-center-agent-templates`.
  - Rewrite or delete `docs/concerns/harness-control-runtime-dependencies.md`, which still assigns K1's work to other lanes.
  - Without this, `openspec_flow.py audit` will keep showing the work queued twice.

VERDICT: ADAPT

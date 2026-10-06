Verifying the integration-dependency claim: checking whether install records link the binding id to its roster slug.

I found no floor or correctness defects in the task 1.1 slice. Deferring task 1.2 is justified, and I found no safe existing integration that was missed.

I read the diff, `tinyassets/harness_settings.py`, `tests/test_harness_settings.py`, the change's `design.md` and `tasks.md`, and the integration points the design cites. I ran no tests and changed nothing.

**What you asked me to check**

- **Strict YAML** (`harness_settings.py:27-40`, `:123-130`): passes.
  - Byte, alias and depth limits come from `load_untrusted_yaml` before parsing.
  - Every mapping, nested ones included, goes through `_mapping`, which rejects duplicate or non-string keys. The `<<` merge key arrives as a literal key and is rejected as unknown.
  - Odd YAML tags can't sneak through: `!!set` and `!!omap` fail the shape checks, timestamps fail `_text`, and booleans fail the `type(...)` checks. Unsafe tags such as `!!python/...` are refused by `SafeLoader`.
  - Overflowing, non-finite and negative loop values are rejected.
- **Immutable revision snapshots** (`:75-99`, `:180-185`): passes. The snapshot types are frozen, selections are tuples, `raw` holds the exact bytes, and `revision` is the SHA-256 of those bytes. The test at `tests/test_harness_settings.py:137-149` shows a snapshot already read does not change when the file is later edited.
- **Source model binding removed on publish, refused on import**: passes.
  - `classify` (`command_center_packages.py:606-615`) runs `package_settings`, which drops `model.connection` and re-emits validated keys in their original order.
  - `check_blob` (`:1535-1547`) refuses any root or `agents/<x>/settings.yaml` that still names a connection. `narrow_package` goes through `check_blob` too, so that path is covered.
  - `model_need` (`:963-966`) reads the logical `id`.
- **Missing vs malformed** (`:188-203`): passes.
  - Only `FileNotFoundError` falls back to defaults. Any other read failure raises `SettingsError`, including a link or junction, a dangling symlink (refused as a link because the read never follows links), a non-regular file or oversize.
  - Supplied bytes that are empty, `null`, a list, or an unversioned file with extra keys all raise. Slugs that try to leave the root are refused.

**Non-blocking notes**

1. `command_center_packages.py:612-615`: an invalid `settings.yaml` now raises `PackageError`, which stops the whole publish rather than excluding one file the way other `classify` rejections do. That fits "fail loudly" and the clean-cutover stance, and no seeded `settings.yaml` is tracked in the repo. But an existing center whose agent wrote a free-form `settings.yaml` can no longer publish until the owner fixes it. Make sure that error is clear in the publish UI.
2. `:607` and `:1537` match the filename case-insensitively, but `read_settings` reads exactly `settings.yaml`. On Linux, an installed `Agents/x/Settings.yaml` is validated but never read, so defaults apply. That is harmless, since it can only fall back to defaults and never grants anything, but it is a missing-vs-present mismatch worth knowing about.
3. `harness_settings.py:162-174`: loop integers have no upper bound (only Python's roughly 4,300-digit limit), and nothing checks `reserve_tokens` against `trigger_tokens`. The design correctly gives those checks to task 1.2 before anything runs, so this is not a defect in 1.1.

**Integration dependency: deferral is justified**

- **Roster slug:** `addressed_agents.resolve` (`addressed_agents.py:151-180`) returns only `agent_id`, `name` and `instructions`; nothing in it points to an installed folder. `plan_install` (`command_center_packages.py:1612-1618`) creates `agent_slug` on its own. The only other use of `agent_slug` in `tinyassets` is the message at `api/package_requests.py:272`, and `custom_agents.py` has no slug, roster or folder field. So no trusted mapping exists. Guessing `agents/<binding-id>` would silently load defaults for the wrong agent, and adding a mapping here would duplicate the roster/install path the approved design prohibits.
- **Starter hooks:** `starter-agent-out-of-plumbing` task 1.3 (owner of the `starter/hooks.md` loader) is still unchecked, and no `hooks.md` loader exists in `tinyassets/` or `domains/`. Its tasks file also says task 2.3 solely owns the starter consumer wiring. A `starter.hooks` toggle has nothing to switch off until that lands.

The design text claims no runtime activation, and `tasks.md` leaves 1.2 unchecked. I found no lane collision and no stale design assumption.

VERDICT: APPROVE

Lead disposition: AGREE. No floor/correctness findings require changes. The
integration dependency assessment matches the approved D8/D9 and starter lane
ownership, so task 1.2 and all following tasks remain unchecked. Non-blocking
notes are retained above: invalid settings fail publication visibly; Linux reads
canonical lowercase settings paths; executor-specific loop bounds are task 1.2.
The additional numeric/timestamp parser regression cases pass locally and are
covered by the final focused Linux verification recorded in design.md.

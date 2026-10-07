Checking the design dispositions and whether the packaging mirror copies match.

**VERDICT: ADAPT**

I found no floor break. The cross-user, grant-widening, mount-source and erasure checks pass. Two correctness bugs need fixing before merge, because either one can make bash fail for an agent. I ran no tests, as you asked; every finding comes from reading the code.

### Findings

**1. Bash stops working in research turns (and when authority is stale)**
- **Where:** `tinyassets/ta_capabilities.py:163-173` and `tinyassets/extension_capabilities.py` (`materialize` calls `_authority()` first).
- **Cause:** `engine_dispatch` always sets `dispatch.extension_backend`, including for research sessions (`research=is_research_session(...)`). So every `JailBridge.__enter__` calls `materialize`. Its `_authority()` raises `ExtensionError` when the context is research, delegated, has an `approval_id`, or `check_authority()` reports an error. `__enter__` cleans up and re-raises, so the whole `bash` call fails.
- **Before this change:** bash ran in those contexts and only `ta` refused (`research_is_read_only`). Now bash itself fails, even with no extensions installed.
- **Why tests miss it:** `tests/test_ta_capabilities.py:301` uses a synthetic dispatch with no `extension_backend`.
- **Fix:** when there is no lifecycle authority, skip the extension mount (`extension_root = None`) instead of failing the launch.

**2. One bad active revision breaks bash and makes revoke unreachable**
- **Where:** `materialize` / `_enabled` / `catalog` in `extension_capabilities.py`, `ta_capabilities.py:90-98`, `ta_cli.py:105-135`.
- **What fails:** if any active extension can't load, `materialize` raises and every bash for that agent fails. Causes include:
  - a malformed `settings.yaml` (`SettingsError`);
  - a blob that can't be read or no longer validates;
  - a manifest that a later, stricter `parse_manifest` rejects;
  - `resolve()` raising `AgentNotAddressable` for a non-main agent. That is a `LookupError`, not a `ValueError`, so `catalog` doesn't catch it either; it surfaces as the generic "ta request failed". The `agent is None` check in `_enabled` never runs, because `resolve` raises instead of returning `None`.
- **No way out:** on a catalog error, `extension_items = []` also drops the `LIFECYCLE` entries. `ta_cli` then rejects `extension:revoke` and `extension:list` as unknown capabilities, so the owner can't fix it through `ta`. That conflicts with the design's rollback line ("retaining … revoke", design §111).
- **Fix:** always list the lifecycle capabilities, and contain load failures to the one extension (report it, skip its mount).

**3. Mount records are shared and overwritten between bash launches** (medium-low; no grant widening)
- **Where:** `extension_mounts` is stored on the shared `Capabilities` backend.
- **Cause:** each `materialize` resets it, and nothing clears it when the bridge exits. Two bash calls running at once on the same dispatch overwrite each other's set, and stale entries outlive the mount. `catalog` and `call` can then show "jailed" or return a launch path the current jail doesn't have mounted.
- **Why it isn't a floor issue:** the key includes the content-addressed revision and generation, so this gives wrong availability or a failed launch, not wrong bytes or extra authority.
- **Fix:** bind the mount set to the `JailBridge` (one bash invocation).

**4. Collision: the packaging copy is out of sync while I reviewed**
- The working tree changed during the review. `tinyassets/extension_capabilities.py` now has `extension:help` / `HANDBOOK`, but `packaging/.../runtime/tinyassets/extension_capabilities.py` doesn't. The other eight copied files are identical. Sync the copy before pushing; I didn't work around it.

### What passed
- **Cross-user visibility:** `ExtensionStore` takes identity from the authenticated context, never from the manifest or arguments. `load` checks the owner, center and agent row before reading a content-addressed blob, so nobody can probe another user's blobs.
- **Revision TOCTOU:** `transition` checks the expected generation under `BEGIN IMMEDIATE`. Contribution keys carry revision and generation, and `call` resolves them from daemon state.
- **Mount source:** a daemon-private tempdir, a name regex that can't start with `.`, a hex revision, paths checked by `check_blob`, and the path added to `platform_sources` exactly.
- **Grants:** the ceiling is the live capability set intersected with the activation ceiling, and launch is refused if current authority exceeds it. Execution happens inside the same bash jail, with no new grants.
- **Revoke:** the design (§123-125) and handbook only claim that new dispatch is fenced. That matches the code, so there's no overclaim.
- **Invalid manifests:** strict parsing (duplicate keys, non-finite numbers, `$ref`, schema check, missing referenced files).
- **Account erasure:** both new tables are in `OWNER_ONLY_TABLES` by `owner_id`, and blobs keep the existing author-owned erasure.
- **Directory resolver:** it reads only completed install pins, validates the slug and checks the landed files. Its tests cover the evidence mutations.

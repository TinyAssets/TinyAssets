# Tasks: provider-advertised shortlist and per-model effort

Owner: claude-code. One branch, one PR.

- [x] 1. Add a second envelope, `NativeControlProtocol`, over the existing
  metadata transport in `providers/native_jsonrpc_discovery.py`. Request
  framing and response matching move onto the protocol object; the process
  boundary, ceilings and teardown are untouched.
- [x] 2. Carry per-model effort and an unreported-modality floor through
  `parse_model_page` / `NativeModel`. Alias collapsing is a declared protocol
  property (`aliased_rows`), so plain JSON-RPC keeps refusing duplicate ids.
- [x] 3. Register the protocol on `ClaudeProvider` against the CLI's
  `list_models` control request, keyed on `resolvedModel`, with the metadata
  argument vector (never `--bare`, which forces API-key auth). Feature-detect
  an older CLI by its explicit unsupported-method answer
  (`NativeMetadataUnsupported` -> the existing `None` contract), so production
  on 2.1.183 reads `native_enumeration_unsupported` and keeps the provider
  default plus the reviewed list; full effect lands with #4351.
- [x] 4. Emit `claude --effort <level>` from `ModelConfig.reasoning_effort`,
  matching the existing Codex `-c model_reasoning_effort` path.
- [x] 5. `model_policy.Model` carries advertised `effort_levels`;
  `served_model_plan._native_models` carries them from the snapshot and drops
  executor-hidden rows.
- [x] 6. Expose `effort_levels` and the saved `effort` per row in
  `providers/model_options.model_options_document`.
- [x] 7. `ModelPreferences` version 2 with a per-model `efforts` map, version 1
  readable as "no level saved". `ModelPolicy.efforts` + `effort_for`, carried by
  `capture_preference_policy` so a per-turn model override does not clear levels.
- [x] 8. `NativeSelection.effort`, admitted only in
  `NativeDiscoverySnapshot.select` against that model's advertised levels, and
  serialized as version 3 only when a level is set.
- [x] 9. Read the level from storage at launch (`saved_effort_level`) and carry
  it through both `prepare_selected_model` paths; the router sets
  `cfg.reasoning_effort` from the native selection only.
- [x] 10. App picker: an Effort group offering exactly the advertised levels for
  the current model, absent when there are none and in automatic mode. The draft
  document moves to version 2 and preserves `efforts` across model and
  automatic switches.
- [x] 11. Tests: control envelope against a real child process; the advertised
  vs opt-in split; per-model levels; effort to `ModelConfig` through the real
  authority chain; refusal of an unadvertised level; argv; hidden rows; version
  compatibility both ways; UI behaviour. Plus an opt-in test that reads the
  installed CLI (`TINYASSETS_LIVE_CLI_DISCOVERY=1`).
- [ ] 12. Sync the delta into `openspec/specs/agent-model-selection/spec.md` and
  archive, on land.

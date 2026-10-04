# Provider-advertised shortlist, and per-model effort

## Why

Founder, 2026-10-03: "the desktop app should auto grab latest short list from
provider" and "effort level controls should also be avalible".

The reported symptom was a model menu showing `claude-code · opus` resolving to
an older model, while specific current ids read "model access opt-in required"
instead of being normal choices.

The cause is narrower than the stale CLI pin. `ClaudeProvider` declared a native
credential service but **no metadata protocol**, so
`BaseProvider.enumerate_models` returned `None` and the source reported
`native_enumeration_unsupported`. With enumeration dead, the only rows Claude
could contribute came from the reviewed static list, and every row from that
list is an **offer to grant** — correctly carrying
`model_access_optin_required`. Codex was the only executor with discovery wired.
The CLI pin explains the alias resolving to an older model; it does not explain
the opt-in text.

This is the continuation already written down in
`openspec/changes/select-agent-models/native-picker-followup.md`: "Claude
protocol verification remains separate, metadata-only, isolated and bounded...
The transport-agnostic `enumerate_models` override is the existing extension
point."

## What Changes

- Register a metadata protocol for Claude Code against its CLI's own
  `list_models` control request, so the shortlist is the **provider's** answer
  and a newly released model needs no platform release to become selectable.
- Detect support by **asking**, never by a version table. Production pins CLI
  2.1.183, which predates `list_models` (#4351 moves it to 2.1.288); an older
  build answers an explicit unsupported-method error, which reads as
  `native_enumeration_unsupported` and leaves the provider default and the
  reviewed static list working exactly as before. The full effect of this
  change therefore lands with #4351.
- Rows are keyed on the resolved execution id, not the alias. `--model opus`
  means different models under different CLI versions, which is how the
  founder's menu came to show one thing and run another. A saved preference
  must mean exactly one model.
- A second **envelope** over the existing hardened metadata transport. The
  process boundary (owned-snapshot env, byte/page/model ceilings, no stderr
  relay, process-group teardown) is reused unchanged; only request framing and
  response matching differ. A new executor registers an envelope, never a fork.
- Carry the **per-model** effort levels the source advertised onto the catalogue
  and out through `read_graph target=model_options`, so a client renders an
  effort control only where one exists and offers exactly the levels that model
  takes.
- Persist the owner's chosen level per model in `model_preferences`, and carry
  it to the provider invocation as that provider's real setting
  (`claude --effort`, `codex -c model_reasoning_effort`).

Effort is **per model, not per provider**: a live Claude Code catalogue reports
effort on Opus/Sonnet/Fable and none on Haiku, and `claude-opus-4-6` stops at
`high` where 5.x offers `xhigh` and `max`. The vocabularies also differ across
families — Claude Code has `max` and no `minimal`, Codex the reverse — so there
is no shared enum to validate against. The admissible set is whatever the model
advertised, enforced where the catalogue is in hand.

This does not widen the discovery spawn point, grant model access, change who
may select a model, or add a static release table. The access opt-in gate stays
exactly where it is: it still guards reviewed-list and owner-verified
candidates, which remain offers to grant.

## Surfaces this touches

- **Public MCP/API surface.** `read_graph target=model_options` rows gain
  `effort_levels` and `effort`. `write_graph target=model_preferences` accepts a
  policy document carrying `efforts`.
- **Storage shape.** The `universe_model_preferences` policy document moves to
  version 2 with an `efforts` map. Version 1 stays readable as "no level
  saved": the store HOLDS an unparseable row rather than defaulting it, so
  refusing version 1 would wedge an existing owner's picker rather than
  gracefully lose a setting. No migration runs; a row upgrades when rewritten.
- **Stored evidence.** `NativeSelection` gains an `effort` field, serialized as
  version 3 only when a level was chosen, so every stored version-2 row keeps
  parsing.

## Non-goals

- The unjailed metadata spawn. `read_native_catalogue` spawns through plain
  `create_subprocess_exec`; this change adds a protocol declaration to that
  existing boundary and does not widen it. Tracked in
  `docs/concerns/2026-10-02-native-model-discovery-runs-unjailed.md`, which
  arrives on main with PR #4350 — no duplicate filed here.
- The synchronous-discovery delay in
  `docs/concerns/2026-09-16-model-picker-global-discovery-delay.md`. Claude now
  participates in the same per-read refresh Codex already did, which does not
  make that concern worse per source, but does not fix it either.
- An effort control for owner-declared explicit model ids. Without a catalogue
  there are no advertised levels to validate a level against, so offering one
  would be a control the executor never promised to honour.

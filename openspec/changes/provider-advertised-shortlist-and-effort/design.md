# Design: provider-advertised shortlist and per-model effort

## The measured fact this rests on

`claude --help` has no model-list subcommand, so the first instinct — shell out
and parse — has nothing to parse. The CLI does carry a selectable catalogue, and
it is reachable over the typed control stream it already speaks for print mode:

```
{"type":"control_request","request_id":"r1","request":{"subtype":"list_models"}}
```

Its own description inside the binary: "Requests the worker's selectable model
catalog. Fulfills the `caps.modelCatalog` capability." Run against the installed
2.1.288 on 2026-10-02, it answers 12 rows of the shape

```json
{"value":"opus","resolvedModel":"claude-opus-5-5","displayName":"Opus 5.5",
 "supportsEffort":true,
 "supportedEffortLevels":["low","medium","high","xhigh","max"]}
```

That single source answers **both** founder asks, which is why they ship
together rather than as two changes. No Anthropic models endpoint and no SDK are
involved, so Hard Rule 3 is untouched.

## Why a second envelope, not a flag

`NativeJsonRpcProtocol` frames `{"method","id","params"}` with an **integer** id
and reads `{"id","result"}`. The control stream uses a **string** `request_id`,
nests the result one level deeper inside a `control_response`, and interleaves
unrelated session/system lines before the answer. Those differences are
structural, not parametric; expressing them as flags on the JSON-RPC class would
make every field conditional on a mode.

What is genuinely worth sharing is the process boundary, and it is shared
unchanged: the owned-snapshot environment, the 4 MiB / 4096-model / 64-page
ceilings, `stderr` to `DEVNULL`, the sanitized single failure message, and a
teardown that kills the original process group because a POSIX launcher can exit
before the children holding our pipes. Skipping unrelated stream lines is
bounded by the same byte ceiling, so a chatty executor cannot stall a read.

## Three decisions that are not obvious

**Keyed on `resolvedModel`.** Both `value` and `resolvedModel` are accepted by
`--model`, so either would run. The alias is the trap: `opus` resolved to
`claude-opus-4-8` under the pinned CLI and `claude-opus-5-5` under a current
one. That is precisely the founder's "claude-code · opus" showing one thing and
running another. Storing the resolved id makes a saved preference mean one
model forever. The cost is that `default` and `opus` arrive as two rows for one
id, so alias collapsing becomes necessary — declared per protocol
(`aliased_rows`) rather than globally, because where a row *is* a model a
repeated id is a contradiction and must keep refusing.

**An assumed modality floor.** The control catalogue reports no
`inputModalities`. Read literally that is an empty set, and an enumerated model
would then fail the plan's `text` requirement and vanish from the picker — the
feature would ship inert. The floor is declared on the protocol as
`assumed_input_modalities`, and it is a property of the **transport**: this
metadata contract belongs to an executor invoked with text and read as text. A
row that *does* report its modalities is always believed as-is, including when
it reports none, so this never overrides a source's own answer.

**Effort is per model.** The live catalogue settles this: `haiku` reports no
effort support at all, and `claude-opus-4-6` offers `[low, medium, high, max]`
where 5.x offers `xhigh` too. The live Codex catalogue says the same
independently — `gpt-5.5` and `gpt-6-luna` lack the `ultra` their siblings
carry. A provider-wide flag or enum would offer a level the model rejects.
Worse, the families disagree on vocabulary: Claude Code has `max` and no
`minimal`, Codex has `minimal` and `ultra` and no `max`. Any shared enum would
have to refuse one of them. Hence the platform validates shape only, and
membership is checked against the advertised list.

The two sources also disagree about the *shape* of that list. Claude Code gates
on a boolean `supportsEffort` and lists plain level names; Codex omits the
boolean entirely and lists objects (`{"reasoningEffort": "high", ...}`),
implying support by listing anything at all. The protocol names which shape it
is reading (`effort_key` optional, `effort_level_key` for an object entry)
rather than sniffing, so a third executor registers its shape instead of
breaking. A *gated* source that claims support and then names no levels is a
fault; an *ungated* source listing none is a truthful "no control".

## Residual risk, accepted

Codex raised a `DISAGREE_CONCERN` on the modality floor that is worth keeping
visible: a text-based *metadata* transport does not prove every model the
executor *lists* accepts text. The exposure is narrow — a row that reports its
modalities is always believed, and `select()` still requires `text` at launch —
so the uncovered case is a source that reports no modalities at all **and**
lists a non-text model. Both current registrations are safe (Codex reports
modalities; every Claude Code row is text). Engineering around it would mean
inventing a per-model capability the source declines to state, so this is
recorded rather than guessed at. If a future executor reports no modalities and
lists image-only models, the floor is the thing to revisit.

## Where the level is admitted, and why there

`NativeDiscoverySnapshot.select` is the only place holding both the requested
model and the catalogue that advertised its levels, so it is the only place that
can check membership. An unadvertised level **refuses** rather than degrading to
the default: running at the executor's default while the owner saved `max` would
report success for a turn that did not do what was asked.

The level then rides `NativeSelection`, the same validated per-attempt object
that already carries the model id, and the router sets
`cfg.reasoning_effort` from it — replacing any inherited value, including with
`""`. That is deliberate: effort changes what a turn costs, so a caller's
`ModelConfig` must not be able to raise it. For the same reason the level is
**read from storage** at launch rather than accepted as a parameter.

An owner-declared explicit id has no advertised list, so it cannot attest a
level at all; `NativeSelection` refuses that combination in `__post_init__`.

## Two compatibility seams, both one-directional

Preferences move to document version 2. Version 1 stays **readable** as "no
level saved" rather than being refused, because the store holds an unparseable
row instead of defaulting it — refusing version 1 would not lose a setting, it
would wedge an existing owner's picker. Nothing migrates; a row upgrades when
rewritten.

`NativeSelection` evidence becomes version 3 **only when a level is set**, so
every already-stored version-2 row parses untouched.

The client-side seam is the one that could have silently destroyed data: a save
replaces the whole policy document, so a picker still posting a version-1 draft
would clear every saved level on the owner's next model switch. The draft is
version 2, and both `select()` and the return to automatic carry `efforts`
forward. Three UI tests pin that, because the server would have done exactly as
it was told.

## What this does not touch

`read_native_catalogue` spawns through plain `create_subprocess_exec`, outside
the provider jail. This change registers a protocol at that existing boundary
and adds no new spawn point; the exposure is unchanged, and widening or closing
it is separate work. The access opt-in gate also stays exactly where it is — it
still guards reviewed-list and owner-verified candidates, which really are
offers to grant. The bug was never that the gate existed; it was that Claude had
nothing *but* gated candidates to show.

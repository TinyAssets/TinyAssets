# Reply-owned model display

September 10, 2026. Bounded presentation prerequisite for task 3.1, not the
clickable model picker or runtime selection activation.

The existing request-owned WriterExecutionReceipt reports provider/model labels
for the first successful conversational writer response. The app currently drops
that optional execution field, both in renderConverse (typed/queued replies) and
sendVoiceTurn (canonical spoken replies). Display the receipt beside its own
answer through appendMessage's extra-node argument. Do not store a global last
model, assign an old receipt to a new answer, or derive model identity from a
saved preference, configured alias, speech voice or heartbeat.

Use bounded printable labels and textContent only. Missing provider metadata is
explicitly unknown; a known provider with missing/invalid model metadata or any
model_status other than reported shows Model not reported. Preserve opaque
Unicode labels, reject control/format characters, and never interpret markup.
Rendering malformed optional telemetry must not lose the user's answer.

No new endpoint, storage, grants, routing inputs, provider invocation or enabled
selection control. Historical replies without stored receipts remain untouched;
these new local display footers do not survive history reload in this slice.
Saved-default, current-choice, ranked fallback, active-attempt status and the
clickable unpowered control still require the remaining task 3.1 integration.

Verification will execute the actual JavaScript under Node with DOM doubles,
cover typed/spoken/queued answers and malformed telemetry, then run existing
app, voice, receipt and plugin parity tests. No deployed or rendered acceptance
claim yet.

## Independent shape review: ADAPT, 251 seconds

Claude confirmed reply ownership, history separation, spoken-text separation and
the existing extra-node seam. Two evidence findings accepted: the current Node
shim drops universe extras, so record their actual nodes and demonstrate a red
integration test before runtime edits; keep the printable regex compatible with
the harness's function extractor. Reject Unicode C and all separators except
ordinary space, using the receipt's 400/200 code-point bounds. Contradictory
status/model pairs are unknown; invalid provider means both fields unknown.

Display provider receipt labels verbatim, including opaque HTTP definition IDs;
no guessed brand or raw-label tooltip. This is truthful but not the final friendly
connection-name picker. Subscription adapters currently do not populate
reported_model, so their model will be unknown until adapter telemetry is added.
No fabricated model default and no claim that this closes task 3.1.

The peer's final stdout was replaced by its stop-hook follow-up, which referred
to a missing earlier review. Recovered the actual assistant review text from this
dispatch's local session transcript before implementation; no new peer run or
inferred approval. Original final result remains output/answer-model-ui-shape-result.md.

## Implemented and tested locally

September10, approximately02:04UTC. The original app/receipt baseline passed118
Windows checks. With only the test recorder/regression added, both typed and
spoken display tests failed against the unchanged app (empty executionDetails).
After the display change,25 focused receipt-display cases passed. The broader
group passed206 on Windows Python3.14 (26.47s) and206 in the actual Docker Linux
oracle, Python3.11.16/Git2.47.3/bubblewrap0.12.0 (18.51s), with zero skips.

Command: `python -m pytest -q tests/test_onboarding_app.py tests/test_writer_execution_receipt.py tests/test_converse_handle.py tests/test_realtime_voice.py tests/test_mirror_parity_gate.py --tb=short -rs`.
Linux used the same arguments through scripts/linux_oracle.py in native WSL
Ubuntu Docker. All407 plugin mirrors/import probe, focused Ruff and diff checks
passed. Windows emitted one existing FastMCP asyncio deprecation warning.

No production rollout, persisted history receipt, live model choice or full task
3.1 completion is claimed.

## Independent implementation review: APPROVE, 284 seconds

Claude reviewed exact6f0e62734859c13da56eb75735d811d9f6765de7 against678d49bc
and independently ran `python -m pytest -q tests/test_onboarding_app.py --tb=short -rs -p no:cacheprovider`:
124 passed,19.31s on Windows. It confirmed fresh per-reply nodes, inert text,
typed/spoken separation, unknown metadata handling, history separation and mirror
parity. No regression or receipt misassociation found. Full self-contained final
result: output/answer-model-ui-implementation-result.md. No live acceptance.

Nonblocking concerns retained: runtime ICU and Python Unicode versions can
disagree about newly assigned characters, yielding an unknown label; malformed
JSON transported as plain reply text has no receipt and remains unknown. The
non-answer tripwire was already green before this change; only the two typed/
spoken positive display assertions are claimed as red-before-green evidence.

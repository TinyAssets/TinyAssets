# Native picker follow-up to deployed PR3832

September14 2026: owner approved one additional focused Claude Fable5.1 review,
completed357seconds ADAPT. Artifact: docs/reviews/2026-09-14-native-model-list-fable.md.
This consumes that approval. Production d895905e passes the app's six workflow
smoke checks but owner-visible native choices remain provider defaults only.

## Reviewed implementation disposition

Reuse existing ModelAccess, bind_serving_provider and set_serving. Native
setup offers provider default, explicit owner-declared IDs (including default),
or discovered scope when the installed executor declares metadata support.
Explicit and discovered are alternatives, never a merged storage representation.
Keep other sources/cost caps and preferences unchanged. Confirm access changes
separately; a choice never grants execution. No static provider-release lists.
Expose executor enumeration support as advisory installation metadata, not
account availability. Unknown enumeration receives a source-level explanation
while independently authorized default execution stays usable. Declared model
freshness attests current custody, not verified availability.

Claude protocol verification remains separate, metadata-only, isolated and
bounded; no speculative JSON-RPC registration or SDK primary writer. The
transport-agnostic enumerate_models override is the existing extension point.
Do not perform an invalid-ID live turn merely to consume inference: local
failure tests suffice before the real valid owner-selection acceptance. A wrong
model must fail loudly; fallback is governed by existing failure classification,
not an automatic replay on every invalid selection.

## Fresh adjacent app finding

After dispatch, refreshed13:19PDT reply identified missing model_options and
agent-binding reads in the served engine, plus an existing OpenRouter channel
not registered as compute. Local read-only reproduction confirms all three
read targets refuse before routing. This reviewer did not cover that later
engine-exposure correction; do not claim it approved or widen engine authority
under the UI patch. Preserve the app's control of its own setup and projects.

## Delivery state

Continue existing select-agent-models intent on codex/native-model-picker,
branched from deployed d895905e; original worktree preserved with its unrelated
untracked files. Baseline Windows60passes13.75s for test_app_model_picker.py and
test_model_options_api.py. Main specs synchronized locally from deployed deltas;
not archived, because complete native selection and other acceptance are open.
Full next-head tests, review receipt/CI, deploy and rendered acceptance remain.

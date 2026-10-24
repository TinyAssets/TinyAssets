SPEC LANE: N3 (realtime two-way voice and video) and N9 (model_call returns media). Worktree
C:\Users\Jonathan\Projects\wf-n3-n9-spec, branch spec/n3-n9-streaming-media (from main eb582d82d08). Spec only: one
openspec change per primitive (proposal, design, spec delta, tasks); no product code. Registry rows N3 ("a streaming
mode of model_call and sessions over a connected source, not a second loop") and N9 ("model_call outputs typed media
parts with a receipt per call") in docs/architecture.md; read the merged C3 one-model-call work (model_call.py and PR
#5232 if not yet merged), the broker streaming contract change, and the existing browser voice code so the spec
replaces it rather than adding beside it. Place every piece by the target shape: provider credentials stay in the
broker, media bytes are owner data in the box, the realtime transport goes through the broker as the one egress, no
provider-specific code (capabilities come from the source's declared or discovered contract). Name the tests that prove
"no realtime loop beside S3" and "a media receipt per call". Keep it short: decisions, not essays. Open a PR.

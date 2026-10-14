## Contract and current evidence

Authority: Direction, ADR-017, ADR-018, shape-convergence C12 and
universe-agent-harness §4.17. Current base: a1fb3d91062feb1257af11c2cce065ea02dfa7a4.
No open PR owns C12. C6 P2b is pushed through `conv/c6-p2b-4` (bf11f6d5),
not merged; the lead's handoff says the stack is unfinished.

C12 is not merely wiring the curated wiki exporter. It includes daemon brain,
episodic knowledge and derived retrieval; all existing user records need a
migration census before deleting a canonical store. Transactional session,
approval and execution records remain platform state, not knowledge indexes.

## Independent local runner slice

`tinyassets/export_runtime/agent.py` is copied verbatim into `run/agent.py` by
the eventual exporter. It imports only the Python standard library and has no
TinyAssets account, import or network dependency. It accepts a chat-completions
endpoint supplied by the owner. Model output can invoke only read/write/edit/bash.
The agent's instructions and skills remain files in the exported folder.

The runner is local owner code, not the hosted isolation mechanism. File tools
stay inside the export root and refuse symlink components, hard-linked writes,
credentials and policy/runtime files. Bash is a host process, requires explicit
terminal consent for every invocation by default, and is denied when input is
unavailable. Policy is loaded once from the owner's run/rules.json; the model
cannot change it through file tools. The owner can explicitly allow or deny
bash in that file. No schedule starts by invoking the chat runner.

This initial slice proves a bounded chat turn and tools. Schedule execution,
full D1 rules mapping, export/import orchestration and live acceptance remain
explicit tasks, not simulated successes.

## Cutover and export (not implemented in this slice)

1. Finish and deploy C6; bind the owner box once per operation.
2. Enumerate legacy knowledge stores and preserve scoped records, metadata,
   provenance, tombstones and unknown fields in the OKF source representation.
3. Under a verified full backup and paused writers, project all records to a
   staged bundle. Compare source counts and canonical serialized values. Commit
   one all-or-nothing layout transition; never migrate at startup.
4. Change all reads/writes to bundle authority; build indexes from files and
   prove deleting/rebuilding each index cannot lose or resurrect knowledge.
5. Export via the owner door with the shared manifest, preview-bound content
   selection and credential exclusions. Fold the existing OKF exporter into
   that one writer. Private output only; publishing is separate authorization.
6. Prove restore, owner editing, restart, export, local execution without an
   account and import quarantine. Assert the deployed SHA before closing S17.

## Blockers observed in this cloud environment

- `scripts/linux_oracle.py --ci`: base image request to mirror.gcr.io returned
  Forbidden; no oracle pass exists.
- `scripts/deployed_sha.py --json`: tinyassets.io/app tunnel returned HTTP 403;
  live SHA cannot currently be observed.
- Claude CLI absent; no cross-family review receipt can be manufactured.
- Every PR requires a trusted exact-head receipt. Builders may not self-stamp
  one (lead handoff); independent review must be supplied by the review lane.

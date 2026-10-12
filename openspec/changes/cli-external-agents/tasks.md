# Tasks: cli-external-agents (C9 slice, brought forward)

Each task below is one PR, except the combined 1.2 + 1.4 cutover. Every PR deletes the old
path in the same PR, states its measured net
(`git diff --numstat origin/main -- tinyassets deploy ':!**/tests/**'`), and
does not merge if the net is positive. A task marked **Floor** gets one
cross-family `peer-agents` round, with floor and correctness findings
answered `AGREE` or `DISAGREE_EVIDENCE` (AGENTS loop 4). Cell and filesystem
work runs in `scripts/linux_oracle.py`; a skip is not a pass. Done for each
PR means its sha is asserted deployed and one naive-user pass is made
through the app agent.

## 0. Contract

- [x] 0.1 Proposal, design and spec deltas. Verify:
      `npx openspec validate cli-external-agents --strict`.

## 1. Pull requests (in order)

- [ ] 1.1 **Measure and record.** Inventory every importer of the files in
      the design's deletion table, every `cli:*` protocol and
      `subscription_cli` row on a production copy, and the native session
      directories to seed. Record a transcript of each pinned CLI version's
      catalogue and job stream on the production image. Correct the
      deletion table with measured spans. No product code. Verify: the
      table matches `git grep` on the base commit; transcripts are fixtures
      under `tests/fixtures/external_agents/`.
- [ ] 1.2 + 1.4 **Codex package, catalogue, and existing serving execution.**
      **Floor:** credentials and owner authority. One PR. Keep the existing
      Codex binding and model selection semantics. The installed package
      supplies the catalogue and JSON-RPC job contract; the runner materializes
      owner custody in an isolated home, drives the native turn and dynamic
      tools, records catalogue and job receipts, and carries a CLI refresh back
      by compare-and-swap. Delete the Codex executor, app-server codec, launch
      contract, host auth overlay and probes, and Codex's legacy discovery cell
      path in the same PR. Claude Code remains on its current path.
      Verify on a migrated production copy with real owner labels: catalogue
      models and receipt; a real turn through the existing Codex binding with
      native-agent receipt, CLI-reported model and usage; forced refresh carried
      to custody; no remaining auth file; unavailable Codex raises
      `ExternalAgentUnavailable` without a Claude attempt. The net core diff is
      negative and S3/S4 ceilings fall.
- [ ] 1.3 **Selection and loud failure through the one call (D6).**
      **Floor:** authority. Depends on C3 2.1-2.3 (#4629), 2.4 and 2.5.
      Add `agent:<name>` sources, protocol-kind dispatch, `agent:` and
      `<agent>/<model>` references, and explicit cross-default pinning. The
      existing Codex binding already executes through the runner from 1.2+1.4;
      this task changes selection semantics and migrates their authority.
      Verify a Codex-pinned step for a Claude-default owner and owner isolation.
- [ ] 1.5 **Claude Code package; Claude executor deleted.** **Floor:**
      credentials. Depends on 1.4.
      - **Builds:** the `control_stream` transport, `mcp_config` tools,
        `env_from_file`, and the Claude Code package.
      - **Parity gate (audit § 8):** on a production copy, the founder's chat
        on the package path matches today on tools, resume, effort,
        streaming, a long tool-wait and cancel, before the delete lands.
      - **Deletes:** `claude_provider.py`, the remaining per-vendor child env
        and auth overlay in `providers/base.py`, the Claude home and token
        helpers in `credential_vault.py`.
      - **Verify:** the parity gate, then the live chat pass.
- [ ] 1.6 **Vendor names out of protocols, connections and the image.**
      Depends on 1.5. Holds migration M1 (writers stopped, after a full
      backup).
      - **Builds:** `agent:<package>` protocols, M1, and
        `scripts/install_external_agents.py` with the image catalogue check
        (D9).
      - **Deletes:** the remaining Claude `_CLI_PROTOCOLS` and `_CLI_REFS`, the vendor `RUN npm
        install` stanzas and version ARGs in the `Dockerfile`, the old native
        session directories after verified copy.
      - **Guard:** the `vendor-names` ceiling falls by the deleted files;
        no vendor string in `tinyassets/external_agents/` outside
        `packages/`.
      - **Verify:** M1 counts in the deploy log; the image build fails when
        a package's CLI cannot list its catalogue.
- [ ] 1.7 **Sign-in through the package (D10).** **Floor:** credentials.
      Depends on 1.6.
      - **Builds:** the package `login` operation and its owner relay.
      - **Deletes:** `onboarding/openai_device.py` and its routes, only after
        a fresh-account live sign-in through the package path succeeds.
      - **Verify:** a fresh account signs in to Codex from the app and the
        catalogue fills without a platform patch.

## 2. Ship

- [ ] 2.1 **Live acceptance.** `python scripts/deployed_sha.py
      --assert-contains <sha>`. Then, through the app agent as a naive user
      with no platform patch:
      - the founder's agent pins a review step to Codex; it runs on Codex,
        and the run's receipt shows `execution: native_agent`,
        `agent: codex`, `requested_model` as pinned, a `resolved_model` on
        the Codex connection and a non-empty `reported_model`;
      - with Codex signed out, the same step fails with a message naming
        Codex, the model and the reason, and nothing else runs.
- [ ] 2.2 Mark the S3 and S4 registry rows' external-agent part done, point
      shape-convergence C9 at what remains, sync the deltas into
      `openspec/specs/` and archive this change.

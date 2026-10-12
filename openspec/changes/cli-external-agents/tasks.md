# Tasks: cli-external-agents (C9 slice, brought forward)

Each task below is one PR that ships on its own. Every PR deletes the old
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
- [ ] 1.2 **Package contract, runner, catalogue, Codex home (fixes the
      catalogue).** **Floor:** credentials.
      - **Builds:** the `external_agents` manifest kind (D1), the pinned
        registry and the Codex package (D2), the runner's `jsonrpc`
        transport and `run_catalogue` (D3), the agent home and auth
        materialize and write-back with custody compare-and-swap (D5), the
        `CellLauncher` with `/agent-home` and one `external-agent` cell
        kind (D4), and catalogue receipts with derived status (D8).
      - **Deletes:** `role_provider_discovery.py`, the snapshot mount and
        `AUTH_DIR` in `role_provider_cell.py`, `catalogue_refresh_pending`,
        Codex's `native_discovery_protocol` and metadata wiring.
      - **Verify:** on the production image in the Linux oracle, a Codex
        catalogue refresh from an owner-private home succeeds and lists
        models; a second owner's home is not visible in the cell; auth files
        are absent after the job; a refreshed `auth.json` is written back
        and a stale write-back is discarded; a failed refresh shows
        `failed: <reason>` and never `pending`. Live: the founder's Codex
        connection lists models.
- [ ] 1.3 **Selection and loud failure through the one call (D6).**
      **Floor:** authority. Depends on C3 2.1-2.3 (#4629), 2.4 and 2.5.
      - **Builds:** `agent:<name>` sources in the connected set, the
        protocol-kind dispatch in `model_call.call`, `agent:` and
        `<agent>/<model>` references, the `execution: native_agent`
        assertion, `ExternalAgentUnavailable` with its fixed reasons, and
        the receipt fields.
      - **Deletes:** the subscription-CLI branch of
        `provider_for_definition`, `_CLI_PROVIDER_CLASSES` and
        `_cli_provider` (callers move to the runner).
      - **Verify:** a node pinned to `codex/<model>` for an owner whose
        default is Claude runs on Codex; with Codex signed out, the node
        fails with the agent, model and reason, and no Claude attempt is
        reserved; owner B's Codex connection is refused for owner A.
- [ ] 1.4 **Codex jobs; Codex executor deleted.** **Floor:** credentials.
      Depends on 1.2, 1.3.
      - **Builds:** `run_job` over `jsonrpc` with `dynamic_rpc` tools,
        resume through `agent_sessions`, cancel with grace, usage and model
        events, and the session directory seeding for Codex (M1 part).
      - **Deletes:** `codex_provider.py`, `codex_app_server.py`,
        `codex_launch_contract.py`, the Codex auth probes, refresh viability
        and bubblewrap probe in `providers/base.py`, the Codex home helpers
        in `credential_vault.py`, the `codex_home` status probe.
      - **Verify:** a production-copy Codex job runs a served tool, resumes
        on the next turn, is cancelled by a parent stop, and its receipt
        shows `execution: native_agent`, `agent: codex`, a reported model
        and `cli_reported` usage.
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
      - **Deletes:** `_CLI_PROTOCOLS`, `_CLI_REFS`, the vendor `RUN npm
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

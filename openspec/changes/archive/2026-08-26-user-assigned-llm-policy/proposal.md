## Why

A user cannot give their universe an engine. Live production, 2026-08-05:
`converse` returns `held / setup_required` with `missing: ["compute",
"model_access"]` and `setup_paths[0].how = "Engine assignment is not exposed by
the advertised handles."` Zero runs execute on the conversational surface, so
the approved V1 golden path stops before the user ever talks to their agent
(evidence: `2026-08-05-mvp-end-to-end-walk.md (archived in git)`).

Host directive, 2026-08-05:

> "a user through a chatbot like we do ui-testing should be able to assign what
> llm's their universe has access to. the different branches/automations should
> carry the preferred llm and accepted fallbacks if any. a user should be able
> to run more than one automation so we shouldn't have to care what the
> cloud-drain is doing"

**This is not blocked by `retire-mcp-provider-secret-deposit`.** That BREAKING
change prohibits depositing an `llm_api_key` through MCP — a credential crossing
the chatbot/control-plane boundary before a requester-controlled executor can
protect it, with confused-deputy risk for shared-universe admins. It does *not*
prohibit **selecting** which already-authenticated provider a universe may use.
Selection deposits nothing: the credential keeps living in the executor's OS
secret store / subscription auth, and the control plane holds only an opaque
`credential_reference_digest` — the shape `provider_work_enrollment.py` already
uses. That change states outright that "Subscription, VCS, and social credential
custody remain unchanged", and its provider set is exactly `{codex,
claude-code}`, both subscription-backed and both reporting `ok — subscription
auth available` in production today.

Selection is not custody. This change adds the selection surface and adds no
credential-bearing path.

Third, user automations are currently coupled to a maintainer daemon. The
automation created during the walk
(`automation_repo_7a09c311891da0f773aa1a8b024ecd19`) was minted with
`daemon_id: daemon::tinyassets-cloud-drain::8b33ef940c59574e` and sits at
`activation.state: stopped` because that daemon has `runtime_instance_count: 0`.
A user's scheduled work must not wait on the maintainer's OpenSpec drain.

## What Changes

- **Chatbot-reachable provider selection.** A universe owner selects, through
  the advertised canonical handles, which providers their universe may use, from
  providers already enrolled and requester-owned. No secret is accepted at this
  surface; malformed or unenrolled selections fail closed.
- **Selection constrains, it does not merely prefer.**
  `constrain-set-engine-provider-authority` records the precise failure this
  must avoid: `set_engine` wrote `preferred_writer` without constraining
  `allowed_providers`, so a failed user-selected engine could fall through to an
  unchosen provider, consume unrelated quota, or cross a privacy boundary. A
  selection here SHALL narrow `allowed_providers` to the selected set plus its
  declared fallbacks, and nothing outside that set may serve the work.
- **Per-branch and per-automation model policy.** A Branch spec and an
  automation definition each carry a `preferred_provider` and an ordered
  `accepted_fallbacks` list (possibly empty). Empty fallbacks means *fail
  closed*, never *fall back to anything available*. For automations the policy
  lives inside the immutable definition digest so it cannot be mutated into
  fresh authority after admission.
- **Resolution order.** Workflow-level policy overrides the universe default;
  the effective set is always the intersection of the requested policy and the
  universe's enrolled, requester-owned providers. An empty intersection is a
  fail-closed error naming what is missing, not a silent fallback.
- **Automation execution decoupled from the drain.** Requester-owned automations
  are claimed by executors selected on their own provider binding, not by a
  named maintainer daemon. Multiple automations per owner run concurrently
  within declared budgets. `daemon::tinyassets-cloud-drain` becomes one ordinary
  consumer among many rather than the required path.
- **A universe hosts many automations; loop declaration leaves the universe.**
  A universe is the owner's account and storage, not a workflow.
  `loop_branch_def_id` is today a *scalar* set *only at birth*, and the public
  `write_graph target=universe` never passes it — so no publicly created
  universe can declare a loop, then or later. Declaration moves to the
  automation; any universe-level default becomes a mutable set, never a
  write-once scalar. This is the mechanical cause of scheduled execution never
  running: no loop → no project-loop daemon → the worker silently skips runtime
  registration → nothing converges activation.
- **Custom-agent automations are typed and act with the owner's surface.**
  Automations are not one thing. The custom-agent kind is conversational *and*
  agentic within its owner's universe, able to do what the owner could do
  through the connector — including minting other automations, agents, and
  universes. That capability is authority-bearing: derived from the owner's
  bounded grant, never ambient, and confined to the owner's container. Specified
  here, implemented by a successor.
- **Actionable health.** When an automation cannot activate, `health.blocker`
  and `health.next_action` SHALL be populated. Production currently returns
  `state: activation_stopped` with **both fields `null`**, which dead-ends the
  owner in the one state where those fields exist to carry the remedy.

## Capabilities

### New Capabilities
- `universe-provider-selection`: owner-facing selection of which enrolled providers a universe may use, and the per-branch / per-automation preferred-provider + accepted-fallbacks policy that constrains resolution.

### Modified Capabilities
- `universe-custom-agents`: agent bindings gain the workflow-level provider policy and its fail-closed resolution.
- `background-branch-execution-authority`: requester-owned automation execution is claimed on the provider binding rather than a named maintainer daemon, and activation health must report a blocker and next action.

## Impact

- **Code:** `tinyassets/api/universe.py` (selection surface), `tinyassets/providers/router.py` (`allowed_providers` narrowing, fallback ordering), `tinyassets/provider_work_enrollment.py` (enrolled-set lookup), `tinyassets/api/cloud_automations.py` + `tinyassets/cloud_automation_setup.py` (policy in the immutable definition; executor selection), `tinyassets/background_branch_authority.py` (claim on binding, not daemon id), `tinyassets/graph_compiler.py` (branch-level policy plumbed to node execution).
- **Security posture:** unchanged custody. No new credential path; the selection surface must reject any key-shaped input. Must not reopen the `retire-mcp-provider-secret-deposit` boundary, and must satisfy the `constrain-set-engine-provider-authority` finding (constrain, not prefer).
- **Public surface:** the canonical seven handles stay seven — selection rides an existing handle's operation, adding no new advertised tool. The `mcp_public_canary.py --assert-handles` set is unchanged.
- **Dependencies:** `constrain-set-engine-provider-authority` owns the `allowed_providers` boundary and is still open with ~5 `ADAPT` rounds; this change must align with its resolution rather than race it. `activate-requester-owned-cloud-compute-binding` (STATUS row 13) owns the requester-owned enrollment/bind path.
- **Review:** dual-family (latest model of both families) before deploy, per the standing gate for security-adjacent authority changes.

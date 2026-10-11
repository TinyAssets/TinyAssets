# Host directive: user-assigned LLMs, per-workflow model policy, drain-independent automations

**Status:** Host directive, 2026-08-05. Captured verbatim; not yet an OpenSpec
change. Needs `openspec propose` before any implementation.

**Origin:** given while the MVP end-to-end walk was blocked — `converse`
returning `held / setup_required (missing: compute, model_access)` and a created
automation stuck at `activation.state: stopped` with cloud-drain
`runtime_instance_count: 0`. Evidence:
`2026-08-05-mvp-end-to-end-walk.md (archived in git)`.

## Verbatim

> "a user through a chatbot like we do ui-testing should be able to assign what
> llm's their universe has access to. the different branches/automations should
> carry the preferred llm and accepted fallbacks if any. a user should be able
> to run more than one automation so we shouldn't have to care what the
> cloud-drain is doing"

## The three requirements

1. **User-facing LLM assignment through the chatbot.** Choosing which LLMs a
   universe may use is an ordinary user action over the connector — the same
   surface `ui-test` drives. Not a host-only operation.
2. **Per-branch / per-automation model policy.** Each branch and automation
   carries its own *preferred* LLM plus *accepted fallbacks* (possibly none).
   Model choice is workflow-level data, not one universe-wide setting.
3. **Multi-automation and drain-independent.** A user runs many automations
   concurrently, and their execution must not be coupled to the `cloud-drain`
   daemon.

## Why this is not blocked by `retire-mcp-provider-secret-deposit`

An earlier reading of that BREAKING change (mine, in this walk) concluded that
engine assignment must stay host-only. **That conflated two different things:**

| | Prohibited? | Why |
|---|---|---|
| Depositing an `llm_api_key` through MCP | **Yes** | Crosses the chatbot/control-plane boundary before a requester-controlled executor can protect the secret; confused-deputy risk for shared-universe admins |
| **Selecting** which already-authenticated provider to use | **No** | Deposits nothing. The credential stays in the executor's OS secret store / subscription auth; the control plane holds only an opaque `credential_reference_digest` |

The retirement change states outright that "Subscription, VCS, and social
credential custody remain unchanged", and `provider_work_enrollment.py`'s
provider set is exactly `{codex, claude-code}` — both subscription-backed, both
already reported `ok — subscription auth available` by production `get_status`.

**Selection is not custody.** A chatbot-driven *selection* surface is compatible
with the security posture; a chatbot-driven *key deposit* is not.

## What requirement 3 says about the current blocker

The automation created during the walk
(`automation_repo_7a09c311891da0f773aa1a8b024ecd19`) was minted with
`daemon_id: daemon::tinyassets-cloud-drain::8b33ef940c59574e` — a *user's*
automation bound to the drain. Under requirement 3 that coupling should not
exist, so "start the cloud-drain worker" is the wrong fix to chase for a user
automation. The execution layer needs to serve arbitrary requester-owned
automations independently.

## Open design questions for the proposal

- Which handle carries selection? A `write_graph target=universe` operation, or
  a per-branch field in the Branch spec, or both (universe default + workflow
  override)?
- How does fallback interact with `allowed_providers` and the existing
  `_apply_api_key_provider_policy` / `FALLBACK_CHAINS` routing, given
  subscription-only default and `api_key_providers_enabled: false`?
- Where does per-automation model policy live so it stays inside the immutable
  definition digest rather than becoming mutable authority?
- What decouples automation execution from the drain daemon — a per-requester
  executor class, or worker pools keyed by provider binding rather than daemon?
- Interaction with `constrain-set-engine-provider-authority`, which is still an
  open boundary with ~5 `ADAPT` review rounds. Selection must constrain
  `allowed_providers`, not merely record a preference (that change's own
  finding: `set_engine` recorded a preference without constraining, so a failed
  choice could fall through to an unchosen provider).

## Next step

`openspec propose` a change covering all three requirements. Do **not**
implement from this file — it is an idea feed entry, not build authority.

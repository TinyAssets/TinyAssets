## Context

Founder framing (2026-10-04): users build and merge patches on **their own** connected platforms. Issues, pull/merge requests, pushes, merges, and comments are examples of consequential external writes, not a closed list. Agents, tasks, and graphs must share the same gate. The founder connecting the TinyAssets repository is ordinary dogfooding, not a platform privilege.

Baseline inspected: `811b4dd3b18ef369433b48ebeb7a08c7c6be157e`, equal to this worktree's `origin/main`. These are proposed requirements, not shipped behavior.

### Repository evidence

| Evidence | Finding and consequence |
| --- | --- |
| `tinyassets/effectors/authenticated_external_call.py:680-708` | An allowed rule reaches `agent_review.review_refusal` before dispatch. Fix the shared review path, not one destination's issue POST. |
| `tinyassets/agent_review.py:278-366`, `_ask`, `_ReviewPurpose` | Review uses the run's provider as `writer`, fixed system instructions, bounded untrusted evidence, and at most two attempts. There is no independent reviewer selection. `review_off` currently permits some classes to skip review; this proposal removes that escape for consequential external writes. |
| `tinyassets/foreground_run_provider.py:1217-1226`, `_reserve_invocation`, `_ensure_admitted` | The purpose forces `ModelConfig(text_only=True)`. Admission captures owner, accepted source bindings, limits, and credentials. A second provider needs its own admitted child authority; changing a role string or passing a provider override is insufficient. |
| `tinyassets/providers/router.py:1198`; `base.py:1477-1500` | The router and adapter enforce the boolean and reject conflicting tools/session inputs. Keep this restriction; extend eligibility evidence rather than trusting prompt instructions. |
| `tinyassets/providers/api_key_http_provider.py:162,177,235,276,344,574` | Only this adapter advertises text-only; it checks direct calls and encoded requests/responses. Its `family = api:<protocol>` is a transport label, not GPT/Claude model lineage. |
| `tinyassets/providers/claude_provider.py:583-587,600,618-626,757,1268` | Empty `allowed_tools` emits no flag. The ordinary path loads project settings and emits nonempty allow/deny lists. Both direct entry points reject text-only before launch; replacing that rejection with an empty allow list is unsafe. |
| `tinyassets/providers/codex_provider.py:804-806,864-889,898-925` | Native text-only refuses before spawn. Ordinary served work enables a writable workspace and cached web search, suppresses shell features, and disables account apps/plugins. These are not a tool-free profile. |
| `tinyassets/providers/provider_jail.py` module contract | Existing jail limits cross-user access but mounts the owner's workspace read/write and has a checking egress proxy plus an engine relay. An empty network namespace is not absence of network authority. |
| `tinyassets/patch_intake.py:send_patch_request`; `delivery_runtime.py:_work` | Patch requests use native delivery to the configured receiver. `_work` invokes the receiver's saved branch, catches failures, logs them, and stores a generic failed run. `dispatch_accepted_delivery` at approximately 253-285 schedules that work; it does not itself contain a GitHub step. The reported file-then-assess-then-notify ordering belongs to the live receiver graph, which this proposal does not edit or claim to have inspected. |
| `tinyassets/storage/deliveries.py:_SCHEMA`; `api/deliveries.py:READ_ACTIONS`; `engine_mcp_server.py:472-476` | Acceptance and attempt records already live in the runs database. `read_graph target="delivery" query=<id>` reads an individual receipt; no receiver-wide pending intake list exists in these actions. Existing ambiguous started attempts must not be automatically replayed. |

The prior reasoning is in `docs/design-notes/2026-10-03-text-only-effect-review.md`. The current routing spec already permits owner-brought HTTP compute; "subscription default" is an observed default, not permission to use platform compute or force a paid connection.

### CLI and upstream evidence, checked 2026-10-04

Commands actually run locally, help/version only; no model, delegated agent, login, or account operation:

```text
claude --version
claude --help
codex --version
codex exec --help
codex --help
```

Versions: **Claude Code 2.1.289**, **codex-cli 0.160.0**. Help demonstrates flag availability, not successful enforcement in a live launch. Proposed argument combinations below were not executed. Empty values must be passed as actual empty argv elements, not shell quoting that Windows might discard.

**Claude:** help documents `--tools ""` to remove built-in tools; `--mcp-config` with `--strict-mcp-config` excludes ambient MCP configuration; `--setting-sources` selects user/project/local settings; `--disable-slash-commands` disables skills. The candidate profile is a fresh print session with `--tools ""`, a validated `{ "mcpServers": {} }` configuration, `--strict-mcp-config`, `--setting-sources ""`, `--safe-mode`, and explicit non-bypass permission mode. Empty setting-source semantics still need an argv/runtime conformance test. `--disallowedTools` is defense in depth, not an exhaustive future-proof deny-all. `--restricted` alone retains file tools. Safe mode explicitly retains admin-managed settings. `--bare` drops OAuth/keychain authentication, so it is not a subscription repair.

Official [hook documentation](https://code.claude.com/docs/en/hooks#disable-or-remove-hooks) says per-run hook disabling cannot override managed hooks. [Server-managed settings](https://code.claude.com/docs/en/server-managed-settings) can be applied in a noninteractive run. **Inference:** flags demonstrate a native built-in-tool disable mechanism but do not establish an inert complete execution environment under arbitrary subscription policy. Credential bytes do not attest current managed policy. A local empty home and rejecting tool events after execution do not close this gap.

**Codex:** help documents `--sandbox read-only`, `--cd`, `--skip-git-repo-check`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, and generic feature/config switches. Root help documents `--ask-for-approval never`; `--strict-config` validates known fields, it does not suppress settings. No all-tools-off option is documented. Upstream matching-tag [tool registration](https://raw.githubusercontent.com/openai/codex/rust-v0.160.0/codex-rs/core/src/tools/spec_plan.rs) registers apply-patch based on environment/model support independently of the shell feature; [request construction](https://raw.githubusercontent.com/openai/codex/rust-v0.160.0/codex-rs/core/src/client.rs) uses automatic tool choice. Thus shell suppression plus read-only execution is not evidence of tool-free inference. This is stronger evidence than the prior note's 0.153.4 source, but it is still source/help analysis, not a live conformance result.

## Goals / Non-Goals

Goals: retain mandatory review; make cross-family choice owner-bound and auditable; state native guarantees honestly; provide precise recovery; preserve incoming patch requests and notify their receiving owner even when external filing fails.

Non-goals: product implementation in this lane, switching the conversation model, bypassing managed policy, reverse-engineering subscription inference endpoints, borrowing founder credentials, adding destination-specific effectors, automatically rewriting existing user workflows, and semantic duplicate detection in this delivery slice.

## Decisions

### 1. Enforced text-only remains the required tier

Use a vendor-neutral, adapter-verified per-launch capability descriptor with tier, executable/adapter version, configuration digest, proof version, and reason. Owner-authored metadata may nominate a candidate but cannot certify enforcement. The existing boolean remains false unless this descriptor and direct-call checks establish the required contract.

| Tier | Meaning | Eligible for required review |
| --- | --- | --- |
| `enforced_text_only` | Only bounded review text enters inference; no tools, MCP/apps, hooks, subagents, session continuation, or other executable extension can run or gain effect authority. Reject returned tool requests even beside a valid verdict. | Yes, with current owner authority and family evidence. |
| `confined_tools` | Tools may still be registered/invoked, but an independently enforced jail limits their reach. | No. Never set `supports_text_only=True` on this basis. |
| `unsupported` | The launch's tool-free or confinement properties are unproven, incompatible, or stale. | No. Refuse before model launch. |

HTTP retains its existing validated, plain-text encoding contract, including rejecting unsupported source extensions and returned tool requests. Do not strip source cost/privacy controls to make a request pass.

Claude remains `unsupported` for required subscription review until a version-pinned mechanism proves managed policy inert for the entire run before hooks can execute. Test startup policy changes, hooks, built-in plugins, and account tools, not just the model's tool list. No such proof exists in the inspected adapter; changing its boolean is expressly excluded. A future upstream subscription-compatible tool-free mode or a independently proven launch boundary can satisfy this contract without changing selection semantics.

The strongest honest **candidate** Codex containment combines a read-only native sandbox, no approval escalation, fresh empty workdir, no resume, isolated home/config, shell/apps/plugins/MCP/search disabled, and an OS jail with no owner workspace, no engine relay, and no arbitrary egress. Full network denial also blocks inference: an explicitly constrained inference transport must remain. Preventing tool descendants from using that transport or reading subscription credentials requires a separately proven credential/transport boundary. The current shared egress proxy does not establish that separation. Even if proven, surviving patch/utility tools mean this is `confined_tools`, not enforced text-only. Do not use the ordinary bypass-sandbox arguments. No version-specific feature name is assumed valid merely because `--disable` exists.

Alternative rejected: treat "read-only", no observed tools, or a prompt saying "do not use tools" as enforcement. None demonstrates tools are unavailable, and startup hooks can act before a verdict. Subscription-only command centers therefore remain held unless an eligible native executor is subsequently proven. The useful immediate repair is honest routing/remediation and durable intake, not a claimed full subscription unblock.

### 2. Select a different family for the effect, not for the conversation

Derive the producing model/provider and lineage from server-owned invocation receipts for the work that produced the exact action/artifact, not the chat's current model or agent-supplied fields. An effect-only code node must carry the producing artifact/run provenance. Unknown provenance holds for repair rather than being relabeled deterministic. A server-proven deterministic owner-authored operation with no model producer records that fact explicitly and still receives review by an eligible known-family model. If several model families materially authored the proposed work, exclude all of them; this may require a third family.

Families are data in trusted model discovery/provenance, independent of adapter, access method, URL, or user label. Claude reached through HTTP remains Claude; GPT through another gateway remains GPT. Opaque or unverifiable aliases are `unknown` and ineligible. Extend the existing model metadata/receipt seam rather than hardcoding a two-vendor pairing or reusing `api:<protocol>` as lineage.

Enumerate only sources already connected and authorized for this command center and accepted for review data/cost scope. Filter by known different family, current text-only evidence, model availability, privacy, owner budget, and unrevoked custody. Use the owner's accepted review preference first, then existing stable candidate ordering; no provider bootstrap and no global role-chain fallback. A separate review child invocation consumes the existing run/request allowance and the reviewer's own account seat. Do not reuse the worker's account seat for a different provider or deadlock waiting while holding an unrelated seat.

Admission must recheck selected binding, credential generation, model family, and enforcement evidence immediately before dispatch. For capacity/transport failure, at most the existing two total review attempts may choose another already-admitted eligible candidate; no fresh budget or same-family substitution. An enforcement conflict holds immediately instead of trying a weaker profile. Conversation assignment and saved default remain unchanged. This is a review-specific path; do not repurpose the parallel judge ensemble or grant a general provider override to workflow code.

### 3. Bind review to one immutable effect and retain provenance

Extend the server-owned review purpose to bind owner, command center, run, exact destination connection/incarnation, operation, method/path, complete body/artifact digest, producing receipt(s), and rule/consent generation. The current structured action digest covers class/connection/operation/path, while evidence is truncated; it must not authorize a different body sharing that path. Preserve full submitted artifacts verbatim. If the bounded review envelope cannot represent material consequences, hold for inspection rather than pretend a truncated preview was sufficient.

Persist review attempt and outcome beside the run's existing invocation/effect receipts: requested and actual provider connection, executor, model, model family, version/proof digest, producer identities, action digest, timestamps, usage, verdict/reason, and safe failure code. Actual execution must be attested by provider telemetry or a proven enforced model pin; missing identity is explicitly unknown and cannot satisfy cross-family proof. A fallback's actual reviewer replaces no history. These fields are audit evidence, never authority. Project onto the owner-visible run detail; redact credentials and raw provider errors. Persistence failure holds the effect.

At dispatch, recheck current owner/connection/rule authority and the immutable effect binding. `proceed` only clears this review; it never supplies consent or expands a grant. A valid `needs_approval` verdict goes to the existing owner consent flow, bound to this action. Owner approval of an unavailable/malformed/timed-out review does not manufacture a completed review. Changed content or authority invalidates the review and requires re-admission within current limits. Consequential external writes ignore old review-off settings; workspace/read-only exemptions remain.

### 4. Fail closed with an exact connection remedy

Keep `auto_review_unavailable`, but distinguish `no_cross_family_source`, `unknown_producer_family`, `text_only_unproven`, `reviewer_authority_revoked`, `reviewer_identity_unknown`, and transient failure details. Budget exhaustion retains its existing separate stop. Surface an owner-visible run record and one deduplicated pending request per unmet requirement, not one approval request per retry. Remediation must identify the needed model family, access/capability requirement, and the existing Connections action.

Examples derived from live available model metadata (display names/models must not be invented):

- Claude-produced work, only Codex subscription connected: "Nothing was sent. Your Codex connection cannot enforce tool-free review. Connect an authorized GPT-family model through a text-only-capable HTTP connection, or a subscription executor with verified text-only support, for this command center. Your conversation stays on Claude."
- GPT-produced work, only Claude subscription connected: name the existing Claude connection's managed-policy gap, and offer an authorized Claude-family HTTP model with verified text-only support. Do not imply another Claude Code login fixes it.
- An eligible owned source exists but its review grant is missing: name that connection/model and request the grant, not a new credential. If no concrete model is discoverable, report the exact family/capability needed and say no currently verified candidate is available.

Never recommend "approve anyway", turning review off, changing the conversation model, sharing the founder's account, or unconsented API spending. Connecting a new source requires the owner's normal grant, privacy, and spending acceptance. Recheck readiness after that flow; no blind replay of an uncertain external write.

### 5. Intake is durable before filing and has independent outcomes

Use the existing runs database and `graph_deliveries` identity. Add generic `graph_delivery_operations` records keyed by `(delivery_id, operation_key)`, scoped to the receiving owner/command center, with kind (assessment, notification, external effect), receiver snapshot version, input digest, state, run/review/effect receipt references, attempt metadata, safe failure reason, next recovery action, and external reference when known. States distinguish pending, running, succeeded, held, failed-before-send, and outcome-unknown. Acceptance itself is sufficient for the inbox entry, even if no worker or operation row ever starts. Existing records lacking operation detail display `legacy_unknown`, never guessed success.

The owner-authored intake graph persists the request first, then schedules assessment and a deterministic in-app receipt notification independently of filing. Assessment may enrich a later notification; neither requires an issue URL. A review failure becomes the filing operation's held result, not an exception that terminates assessment/notification dependencies. Assessment/provider failure has its own visible result and does not suppress the initial notification. Missing worker/provider capacity must still leave an inspectable received item. This is a reusable receiver/workflow primitive, not a platform-authored patch workflow.

Existing graphs are not silently rewritten. Expose the outcome/dependency primitive and guidance so the owner's app agent can update its own receiver through the ordinary graph authoring path. Prove that upgrade on a normal owner's intake and the founder's intake before claiming this use case fixed. A graph that has not adopted independent processing still gets durable received/failed visibility.

**Where maintainers see pending requests:** add `read_graph target="deliveries"` as the receiver-scoped paginated inbox; filters use receiver, pending/held/failed state, and a stable cursor. Individual details extend existing `read_graph target="delivery" query=<delivery_id>` with operation outcomes and recovery. The command center's receiver detail exposes a "Received requests" view; the patch-intake receiver is labeled "Patch requests" in its owner's view. The existing `pending_requests` rail carries actionable connection/retry holds. Maintainers use the receiving command center's existing authorized admin access; a repository maintainer without that access gains none. Sender receipts remain coarse and reveal no assessment, other senders, receiver graph, or private destination credentials.

Add `write_graph target="delivery" operation="retry_operation"` with delivery ID, operation key, and expected operation version. Receiver authority, cancellation, connection, rules, current review, and consent are rechecked. Retry only the unfinished operation. Successful assessment/notification/filing stays completed; no whole-branch replay as a shortcut. A transaction/claim fence serializes retries. A known pre-send failure is retryable; timeout/crash after dispatch is `outcome-unknown` and requires provider-supported idempotency or read-only reconciliation before another send. If neither is possible, require an owner reconciliation decision; never invent exactly-once semantics. Outbound notifications are consequential writes and get the same review gate; the durable internal notice/inbox is available without that outbound path.

This proposal changes no consent to send to another owner and grants no new cross-user reads. Receiver processing uses that receiver's own credentials and limits, never the sender's, platform's, or a maintainer's ambient login.

### 6. Duplicate coverage is a follow-up, not retry safety

Follow-up scope: before filing, use the owner's authorized platform-neutral search/read operations to retrieve existing issues/requests, preserve candidate references, and let the assessment decide whether existing work actually covers the request. Covered requests link to that item; uncovered requests file; partial or unknown coverage stays explicit. A similarity match alone is not coverage. Search errors are recorded and do not silently drop intake. Race handling and an owner policy for unavailable search belong in that follow-up. Linking an internal record is distinct from posting an external comment, which still requires review. No GitHub search API is embedded in the generic contract.

## Risks / Trade-offs

- Native proof remains unavailable -> be explicit that strict subscription-only writes remain held; support authorized HTTP review and a version-bound native proof path, without lowering the guarantee.
- Family diversity is not a correctness proof -> retain owner rules, consent, exact effect binding, and the strict verdict parser.
- Model aliases and source catalogues change -> invalidate stale family/enforcement evidence; record actual model identity and hold unknowns.
- Another provider may incur cost or see private evidence -> select only accepted review scope under existing privacy and spend ceilings; never auto-connect.
- Notification can itself fail -> durable internal inbox and independent notification outcome survive; external notification uses the common gate.
- Unknown external outcome or concurrent retry can duplicate a write -> preserve per-operation claims, reconcile, and never auto-replay ambiguous attempts.
- Active changes overlap -> coordinate implementation with `select-agent-models`, `connect-cross-user-nodes`, `notify-owner-of-requests`, and `universe-agent-harness`; this artifact lane edits none of them.

## Migration Plan

1. Add review descriptors/provenance and delivery operation storage with owner-scoped projections. Backfill only known facts; old unknown outcomes remain unknown. Align account deletion/retention with existing run and delivery records.
2. Add review-only selection/admission and mandatory external-write enforcement, preserving current fail-closed behavior for unproven adapters. Apply across foreground, background, task, graph, and direct adapter boundaries.
3. Add receiver list and operation recovery, then have the owning app agent adopt independent processing in the intake graph. No automatic edits to user workflows.
4. Future implementation verification: targeted review/admission/receipt/delivery tests plus affected heavy tests and ruff; mutation checks only for cross-user/effect-authority guards. Any jail/process implementation requires `python scripts/linux_oracle.py`; a skip is not a pass. Native conformance must prove no tool/hook/effect before capability promotion. No test suite or conformance launch was run in this proposal session.
5. Before future shipping, one cross-family floor/correctness review under `peer-agents`, deployment SHA assertion, public canary for changed connector surfaces, and a real-user app pass. Prove ordinary user-connected platforms, one non-GitHub destination, both producer-family directions, and an intake filing failure with visible assessment/notification. Sync specs only after implementation; do not archive a proposal as delivered.
6. Rollback disables new reviewer eligibility and operation dispatch while preserving records/inbox reads; it must not disable review, restore review-off bypasses, or replay operations. Restore prior schema readers only after verifying they cannot erase new state.

## Open Questions

For the founder (proposal does not wait for answers):

1. With strict review retained and native proof currently absent, is owner-authorized HTTP review an acceptable interim path, or should release messaging explicitly keep subscription-only external writes unavailable until an upstream native contract exists? This design defaults to the former when already authorized, with no automatic paid connection.
2. Should semantic duplicate coverage be prioritized as the next intake lane? It is deliberately outside this delivery slice.

Engineering evidence still required: a subscription-compatible Claude mechanism that makes managed policy inert before execution; a Codex version/launch contract with no callable tools; production executor version verification and adversarial conformance for either. These are not matters an owner's approval can substitute for.

# Code-only effect review: restrictive execution contract

> Superseded in scope by founder direction, 2026-10-04: writes through an owner's
> own connections do not require platform review. The owner must explicitly
> enable review per action class; unavailable configured reviews still hold
> with their cause. The text-only execution restrictions below remain in force
> for those reviews. Connection ownership, grants, operation scope, consent and
> shared-host isolation remain mandatory and independent of any model verdict.
> See `openspec/specs/http-connections-and-outbound-authority/spec.md`.

The run's transient review purpose binds its owner, run, provider wrapper,
action digest and fixed prompt/system. It admits no ordinary no-prompt call.
The existing receipt reserves at most two attempts per declared external effect
under the owner's accepted invocation/token/cost ceilings. A successful response
without usage retains the router's conservative `indeterminate` accounting: the
full reservation is consumed, never replenished. All current identity,
cancellation, revocation, rules and consent checks remain in force.

`ModelConfig.text_only` is a per-invocation restriction, not a grant. The router
checks the resolved adapter's `supports_text_only` contract before dispatch.
Unsupported execution and conflicting engine tools, agent requests or session
resume raise held authority; they do not trigger capacity/provider fallback.
Adapters also check the restriction at their direct entry points.

HTTP supports installed plain-text request shapes, validated again after source
constraints are applied. Unknown request extensions (including pricing/routing
extensions not covered by that shape) refuse; the implementation never strips
cost controls to get a request through. Returned tool requests are refused even
when accompanied by verdict text. This is intentionally narrower than all HTTP
sources. A future extension requires its own evidence before being accepted.

Native text-only reviews remain unsupported; this is not an end-to-end repair
of native issue filing. The pinned Codex 0.153.4
[request construction](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/core/src/client.rs)
uses `tool_choice: auto`; its
[tool registration](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/core/src/tools/spec_plan.rs)
can retain model-supported patch tools independently of shell suppression.
Installed 0.159.0-alpha.3 help was inspected without a model/account call.

Official Claude 2.1.288 package integrity and isolated version/help checks
confirm `--tools ""`, strict MCP configuration and safe mode. However,
[server-managed settings](https://code.claude.com/docs/en/server-managed-settings)
can arrive after startup using the existing OAuth token and apply hooks or
environment settings to noninteractive runs. Safe mode preserves managed policy;
[per-session hook disabling](https://code.claude.com/docs/en/hooks#disable-or-remove-hooks)
cannot disable managed hooks. The credential snapshot binds credential bytes,
not current effective policy; checking an empty local directory is insufficient.
Bare mode drops OAuth custody and is not an alternative. No pre-launch proof of
inert effective policy exists in the current adapter. Both native adapters
therefore refuse before executable resolution, credential/environment construction
or spawn. No provider substitution or user/host settings change is made.
Ordinary non-review provider calls retain their policy.

The Codex `native_install_mounts` callback is separate metadata plumbing requested
by the integration lead. Both metadata and execution use this callback to the
existing installation-path resolver; the shared jail validates those paths and
mounts them read-only. It grants no tools, writable mounts or account access.
Metadata commit `976884c5bb00da5e3a600415053ce7ada5c4acbf` supplies its consumer.
Extract the registration and execution callback reuse, including runtime mirrors,
as an independent prerequisite, or fold them into metadata before merging it.
Then integrate metadata and this repair, preserving the already-present callback
and resolving `base.py` serially so both contracts survive.
No changes to metadata protocols, process ownership, jail policy, authority
storage or schema are part of this repair.

Proof uses real review/admission/router and HTTP encoding with fake terminal
models/proxies only. Native tests capture the env/argv/spawn boundaries and
require zero calls, including attempted ambient config/tool/session injection.
No live retry, real issue submission or deployment is authorized by these tests.

# Code-only effect review: restrictive execution contract

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

Native text-only reviews are currently unsupported. Local official
`codex exec --help` from `codex-cli 0.159.0-alpha.3` was inspected on 2026-10-03
without a prompt/model/account invocation. It documents feature toggles, sandbox
modes and config isolation, but no verified complete tool-free contract. Claude
was not installed locally. Neither absence nor individual shell-disable flags
proves full tool suppression. Both adapters therefore refuse before executable
resolution, credential/environment construction or spawn. No existing user or
host settings are changed. Ordinary non-review provider calls retain their policy.

The Codex `native_install_mounts = staticmethod(_codex_sandbox_mounts)` registration
is separate metadata plumbing requested by the integration lead. It reuses the
existing installation-path resolver; the shared jail validates those paths and
mounts them read-only. It grants no tools, writable mounts or account access.
Metadata commit `976884c5bb00da5e3a600415053ce7ada5c4acbf` supplies its consumer.
Integrate the metadata lane first, then this repair, resolving `base.py` serially
so its enumeration callback and the independent text-only contract both survive.
No changes to metadata protocols, process ownership, jail policy, authority
storage or schema are part of this repair.

Proof uses real review/admission/router and HTTP encoding with fake terminal
models/proxies only. Native tests capture the env/argv/spawn boundaries and
require zero calls, including attempted ambient config/tool/session injection.
No live retry, real issue submission or deployment is authorized by these tests.

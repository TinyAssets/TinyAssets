# L2 cross-family review

Claude via `.agents/skills/peer-agents/SKILL.md`, one read-only round, 2026-10-05.
Draft PR: https://github.com/TinyAssets/TinyAssets/pull/4502.
Reviewer verdict: **ADAPT**. The reviewer ran the new quota scope file: 4 passed.

## Findings and disposition

1. **AGREE** — The no-owner host-refusal test must inspect the empty scope, not
   a named owner's scope. Restored its original availability assertion and added
   an assertion that the entire cooldown map stays empty.
2. **AGREE** — `get_status` still read the empty scope and would show zeros for
   real owner cooldowns. It now reads the authenticated named principal's scope.
   A regression tests both the owner's positive cooldown and zero when only
   another owner has cooled the same provider.
3. **AGREE** — Guidance to connect an existing destination just to add model use
   could request a duplicate key deposit. Removed that guidance. Keep the
   accounted prompt-node route and the actual fieldless `bind_model_access`
   owner ask, preserving other accepted providers.

Claude confirmed that all routed and deferred cooldown writes carry the same
trusted owner identity as the reads, diagnostics stay isolated, the empty scope
cannot be reached by production dispatch, and the typed inference refusal
preserves both accounting and secret-free IPC. It also confirmed the URL safety
checks remain in place. No second review round was requested or performed.

The original generic production refusal does not identify its private cause.
The missing-reference path is reproduced; the founder's live replay and deployed
SHA proof remain pending. This draft has no final trusted merge-review receipt.

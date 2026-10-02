---
severity: P3
title: The provider-routing spec still ends every chain at a host model that can never serve a turn
filed: '2026-10-01'
summary: 'The gemini, groq and grok host_process executors were deleted (#4309). ollama remains as the one host_process executor. owner_binding refuses it before launch, as it refuses every host_process executor (Hard Rule 15), yet openspec/specs/provider-routing requires every fallback chain to end at ollama-local. Open question: drop ollama-local from the chat chains and keep Ollama only for fantasy_daemon embeddings, if those are still used.'
---

# The provider-routing spec still ends every chain at a host model

**Filed:** 2026-10-01. Narrowed 2026-10-02, after #4309 deleted gemini, groq and grok.
**Verified:** 2026-10-02, origin/main, by reading the code.
**Severity:** P3. This is a stale design statement, not an outage.

## What is true

- `tinyassets/providers/ollama_provider.py` declares `credential_source = "host_process"`. `owner_binding.require_owner_bound_dispatch` refuses every such executor before launch, so `ollama-local` can never serve a chat turn.
- `openspec/specs/provider-routing/spec.md`, in the requirement "Every role chain terminates at the local model", still says every fallback chain ends at `ollama-local`, "so a call keeps producing output with zero cloud providers reachable". Under "no host writer ever" that terminus is a refusal, not output. `router.FALLBACK_CHAINS` still lists it.
- `fantasy_daemon/__main__.py` uses `OllamaProvider` directly, outside the router, as the embedding function for its vector store. That is the one place it does real work.

## Open question

Should the chat chains drop `ollama-local`, with the spec requirement removed or rewritten, while Ollama stays only as fantasy_daemon's embedding backend? First establish whether that embedding path is still used. If it is not, `ollama_provider.py` is dead as well.

## Resolution

Decide the question above. Then either rewrite the spec requirement and `FALLBACK_CHAINS`, or delete `ollama_provider.py` with its references. Delete this file in that PR.

---
severity: P2
title: tiny's OpenRouter review call is refused with "inference usage authority refused"
filed: '2026-10-05'
summary: the GPT review tiny runs through its OpenRouter connection fails with ProviderAuthorityHeldError; the Codex review works
---

# OpenRouter review refused for inference authority

tiny reported on 2026-10-05 that its GPT review through the founder's OpenRouter
HTTP connection is refused with "inference usage authority refused". That
message is raised as `ProviderAuthorityHeldError` in the broker dispatch path
(`tinyassets/storage/outbound_connections.py` near the `ProviderAuthorityHeldError`
handler; `tinyassets/broker/client.py:51`). The Codex review path works.

Unknown: whether this is a missing per-connection inference grant the founder
can give from the approval sheet (then the agent should raise that ask, not just
report the refusal), or a platform authority bug. Next step: reproduce from
tiny's review workflow and check which authority check refuses.

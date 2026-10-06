---
severity: P1
title: Live capability check of the main agent against pi.dev, Muse and Claude Code
filed: '2026-10-05'
summary: 14 real user tasks run in the founder's desktop app; continuity and file loss, no notify primitive, Google sign-in rejected, no chat visuals or file delivery, no provider failover, false send failures
---

# Live capability check (2026-10-05, main account, desktop app)
Bar: pi.dev + Muse + Claude Code. Each item = a real user request, unprompted path, no coaching.
| # | Capability | User message | Result |
|---|---|---|---|
| 1 | Real code work: clone, run tests | clone our repo, run one test file, report | PARTIAL: git+network+pytest work in box; FULL CLONE BLOCKED by a 16 MiB-per-call write cap (structural cap, founder says only storage+concurrency); full conftest needs platform deps; agent did not know it could do this (used GitHub API for PRs) |
| 2 | Web research with sources | latest news on Meta Muse, with links | PASS (minor: links are news.google.com redirect URLs, not publisher URLs) |
| 3 | Code execution (python) | compute something non-trivial | PASS (mortgage table correct, xlsx with formulas) |
| 4 | Make a file to download | build a CSV/markdown file I can download | GAP: file made in exports/, but no way to hand it to the user; agent doesn't know if a download exists |
| 5 | Memory: remember + show + edit | remember a preference; show memory; change it | PASS in chat (saved + shown). UI view/edit of memory = known open requirement 3 |
| 6 | Soul/identity edit | rename yourself | NOT RUN (paused) |
| 7 | Schedule recurring work | every morning, do X | PASS (weekday 8am workflow, test run). GAP: delivery to the user unknown to the agent (no confirmed push/chat delivery) |
| 8 | Connect via MCP link | connect https://mcp.deepwiki.com/mcp and use it | PARTIAL: used a public no-auth MCP from its shell (clever); cannot save it as a connection; OAuth/authed MCP impossible (MCP lane) |
| 9 | Parallel sub-agents | research 3 things in parallel | PASS on result (~1 min, good comparison); actual parallelism not visible |
| 10 | Read a web page | open a URL and summarise | PASS (read three pricing pages; used a proxy when Buffer blocked it) |
| 11 | Calendar/email via connection | what's on my calendar tomorrow | FAIL (bug): connect ask for www.googleapis.com rejected - OAuth discovery only checks the destination host; the google directory row (issuer accounts.google.com) is never matched to googleapis.com; platform demanded a pasted key; agent fell back to iCal secret URL |
| 12 | Build + preview a UI | small page, show me a screenshot | NOT RUN: turn failed 1:00 PDT - 'claude -p reported a provider rate limit and did not recover'; no automatic failover to the user's ChatGPT subscription. Check paused to stop burning the founder's limits |
| 13 | Rich visuals in chat | chart / mermaid diagram rendered inline | NOT REACHED - exposed a P1: at 12:52 the agent said it never made the mortgage table (made 12:24, same thread) and exports/mortgage-* files are GONE from its workspace. Conversation continuity + workspace persistence lost (likely across the 07:31Z deploy) |
| 14 | Phone notification | send me a push now | GAP: no notify primitive; agent faked it with a pending request tab (req_b3bdd3a94e9e40d1a014a226); scheduled runs can't notify; awaiting founder receipt |

## Extra findings
- Chat links are NOT clickable (founder, 2026-10-05 00:25): markdown links in agent replies render as text.
- Founder 00:27: agent replies should use appealing visuals (Mermaid diagrams, charts) where they fit; the mortgage table was a missed chance. Chat must render Mermaid/charts and the agent should reach for them unprompted.
- Founder 00:32: the Social Media Manager agent could not send them a phone notification; main agent not tried recently.
- 07:33Z: message send failed with HTTP 520 during deploy 29fa5b4686 (started 07:31Z): deploy kills in-flight turns and sends; app offered 'Send it again'.
- Founder 07:34Z: the request-tab notification DID reach the phone; the founder replied from the phone but the reply did not reach the chat (deploy overlap possible; retest).
- Founder 07:35Z: 'that is a gap that our updates make things drop' -> deploys must never drop a turn, a send or a reply (zero-loss deploy: drain/resume in-flight turns, durable inbound replies).
- Founder 07:40Z: WIDER - 'insure deploys dont cause user issues is built into the platform': a platform-level zero-user-impact deploy guarantee (turns, sends, replies, page state, scheduled runs, background work), not a per-surface patch.
- 12:35 PDT: phone reply DID reach the agent (not dropped). The 8am note now notifies via the same request-tab workaround (req_fee55902c533435aaaa9f8f8): it works, but needs a real notify primitive.
- Founder 07:52Z: OpenRouter free is the wrong default when the user has $200 Claude + ChatGPT subscriptions; the agent should prefer the user's subscriptions (the GPT review via the ChatGPT/Codex subscription already works). The OpenRouter-refusal lane becomes: prefer the strongest connected source.
- #13 retry 12:54: GAP confirmed - no chart rendering in chat, agent drew an ASCII bar chart in a code block and offered to install a charting tool for an image. Fix lane: fix/chat-links-visuals-files.
- 1:00 PDT: provider rate limit on the founder's Claude subscription: the turn failed with 'try another connected source' when the user HAS a connected ChatGPT subscription -> automatic failover across the user's own sources is missing.
- Founder 1:01: both the founder's phone message and my message showed 'Send it again' failures, but the founder's was actually answered -> FALSE delivery-failure notice.
- Thread ORDER bug: a reply stamped 12:55 renders after a 1:00 notice.
- tiny filed #4486 (request-tab link 300-char cap) and worked around it with TinyURL; the 300-char tab-link cap is another leftover structural cap.

## Fix lanes

Running: turn continuity and workspace persistence; chat links, visuals and file delivery; per-call write cap (#4485); notify primitive and agent self-knowledge; zero-impact deploys (design); consent asks owner-only (#4483). Queued: Google sign-in for googleapis.com, model failover across the user's sources, chat delivery truth and order, request-tab link cap, cleared asks can come back.

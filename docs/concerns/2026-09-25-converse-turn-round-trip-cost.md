---
severity: P1
title: A served founder turn pays for 3+ whole round-trips
filed: '2026-09-25'
summary: 'measured: ~70 KB per round-trip, three times, for one trivial message; 63 KB of it is the tool block (61% one tool''s manual) and the learning call cannot leave the request without an authority change'
---

# A served founder turn pays for 3+ whole round-trips, and 63 KB of them repeats

**Filed:** 2026-09-25
**Verified:** 2026-09-25, local, `pytest tests/test_converse_turn_cost.py` on the
real converse path with a synthetic wire; byte counts from the real engine tool
surface (`engine_mcp_server.mcp.list_tools()`)
**Severity:** P1 — it is every reply on every account, on the founder's own surface

Found 2026-09-25 by measuring, not reading: `tests/test_converse_turn_cost.py`
drives the real `converse` path (persona assembly -> router -> the HTTP executor
-> the agent tool loop -> `extract_learning`) against a synthetic wire that
records every model round-trip.

Live symptom: on the free-only test account with a free source, each reply to a
trivial message took 1-2 minutes of the founder's wall clock (sent 6:12 ->
answered 6:14; 6:14 -> 6:15; 6:16 -> 6:18 PT).

## Measured, one turn, one tool step

| round-trip | what it is | request | of which system prompt | of which tool schemas |
|---|---|---|---|---|
| 1 | reply turn, first inference | 7,509 B | 5,511 | 1,553 |
| 2 | reply turn, continuation after the tool result | 7,863 B | 5,511 | 1,553 |
| 3 | `extract_learning` | 2,039 B | 1,580 | 0 |

Those tool-schema bytes are the RIG's stub schemas. Against the real engine
surface the block is far bigger: 63,383 B of tool definitions (56,328 chars of
description), **re-sent on every round of every served founder turn**.

| tool | definition bytes |
|---|---|
| `write_graph` | 40,719 |
| `read_graph` | 7,172 |
| `run_graph` | 4,143 |
| `connect_compute` | 3,595 |
| `source_channel` | 1,788 |
| `write_brain` | 1,407 |
| the other 8 | 4,559 |
| **total** | **63,383** |

`write_graph`'s description alone is 38,513 chars (623 lines, ~9.6k tokens) — 61%
of the block, and 68% of all description text on the surface. It is a genuine
manual (file inputs, code nodes, workspaces, HTTP connections, git checkouts),
not maintainer commentary, and `inspect.cleandoc` saves zero bytes: it is already
dedented, and the engine surface attaches no per-parameter descriptions, so there
is no duplication to strip.

So one trivial founder turn with one tool step sends roughly **70 KB per
round-trip, three times**. Round-trip COUNT multiplied by a ~63 KB fixed
preamble is the turn's cost, and on a slow source each round-trip is tens of
seconds. Nothing in the path sleeps, polls or retries: the writer and the
extractor both pass `retry_on_exhaustion=False`, the router's sync pool has 8
workers, and `engine_mcp_http.wait_for_engine_mcp_route` only polls when the
loopback route is not yet up.

## What shipped with this finding

The prompt stops inviting a round-trip it already answered: the grounding files
are quoted verbatim in the system prompt, and the prompt now declares them
current and complete for the turn, so recall ("do you remember my favourite
colour?") is answered from the prompt instead of paying a round-trip to fetch
the same text. It forbids no tool and keeps read-before-edit.

## Two costs this did NOT fix

### 1. `extract_learning` sits inside the founder's wait, and cannot leave the request

`converse` produces the reply, then spends a third round-trip on learning
extraction, then returns (`tinyassets/universe_intelligence.py`, `_learn_from_turn`
before `return reply`). The founder waits for platform bookkeeping whose output
cannot change their answer.

It cannot simply be deferred:

* **The reply is delivered by the HTTP response.** The app awaits
  `MCP.converse(...)` (`tinyassets/onboarding/app.html`); `record_exchange` is
  memory, not delivery. There is no post-response hook — the MCP tool wrapper
  returns the value straight to FastMCP.
* **The provider lease dies with the request.** `_register_structured_tool` calls
  `revoke_provider_request(capability)` in its `finally`
  (`tinyassets/universe_server.py`), and `validate_provider_request_carrier`
  refuses a carrier whose registry lease is no longer `claimed`
  (`tinyassets/auth/middleware.py`). A worker thread that outlives the request
  loses authority by design.
* **Overlapping it with the reply turn is not free either.** In-flight budget is
  a deliberate concurrency guard: `provider_assignment.py` sums unsettled
  reservations and refuses when the remaining output allowance drops below 1, so a
  concurrent secondary call can make the FOREGROUND call lose the race. A slow
  reply is better than a lost one.
* **The as-built spec requires the current order.**
  `openspec/specs/universe-personification-and-relay/spec.md`: "After the reply
  turn, `converse` SHALL run a separate provider call."

Fixing it properly is an authority change (a bounded deferred-work lease, or
reusing the existing background provider-work authority), which needs an OpenSpec
change and a cross-family review before code.

### 2. The 63 KB tool block needs a decision about where the manual lives

Every round re-sends it and no capability may be removed. The repo already has
the pattern that solves this — `universe_tools._HARNESS_HEAD` keeps a one-line
skill index in the prompt and has the agent `read` the full `SKILL.md` when a
request matches — so the shape would be a short description plus the manual
reachable on demand. That trades ~9.6k tokens on EVERY round for one extra
round-trip on turns that actually build a graph, and it changes the served tool
surface, so it is a founder/spec decision rather than a refactor.
`tests/test_converse_turn_cost.py::test_engine_tool_description_budget_does_not_grow`
ratchets the total meanwhile so it cannot grow unnoticed.

## Evidence to pull from production to close this

Local timings are synthetic; the wire latency here is a `sleep`. For the real
split, from the box, for the 2026-09-25 18:12-18:18 PT turns on
`u-01ky3zh1arr8qth8jee7zx63pq`:

1. **Round count per turn** — `agent_turn_rounds` in the agent-turn journal,
   `SELECT turn_id, COUNT(*) FROM agent_turn_rounds GROUP BY turn_id`, plus
   `agent_turn_tools.name` for which tools each round ran. This settles whether a
   trivial turn really spent round-trips re-reading brain files.
2. **Per-call latency** — the daemon logs the router's per-attempt
   `latency_ms` (`ProviderResponse.latency_ms`, set in
   `api_key_http_provider.py`, surfaced in the router's `provider_calls` record).
   Grep the turn window for the writer attempts and the `converse: learning ...`
   lines to see what fraction the extraction cost.
3. **Whether the extraction 429s on this account** — the `converse: learning
   skipped ... no capacity for a second call this turn` INFO line. If it fires,
   the extraction is cheap on THIS account and the round count is the whole story;
   if it does not, the extraction is a full round-trip of the founder's wait.

**Known gap:** `agent_turn_rounds` records no timestamps and no prompt-token
count, so (1) gives the count and (2) gives durations only from logs. Per-round
timing in the journal would make this measurable from data instead of grep — it
is a storage-shape change, so it needs its own proposal.

## Tried and REJECTED: eliding a `read_brain` body the prompt already quotes

The live proof on `ef63af30` (2026-09-26) showed the reply arriving in under a minute
instead of ~two, but turn `5c3daf87` still ran `rounds=3` with `tools=['read_brain']`:
the free model re-read `founder.md` although the prompt quotes it verbatim and says so.

PR #4006 made that re-read cheap instead of relying on obedience — `read_brain`
returned a pointer to the prompt heading, with the quoted version's `sha256`, whenever
the file was still byte-identical to what the prompt carried, and the full body the
moment it differed. It measured a **72% cut** in the result (2,513 → 712 bytes on a
~2 KB brain), repeated across every later round of the turn.

**Closed unmerged, on the risk, not the mechanism.** The saving is ~1.8 KB per read
against a ~34 KB block per round. What it risks is the BRAIN: `read_brain`'s contract
is "read it first so an edit builds on what's there", so a turn holding a pointer
instead of a body can write a shorter one and drop what was there — and continuous
self-learning is founder law. The decisive point is that the same model was shown live
to ignore a prompt statement about those very files, so "copy the text from the named
heading" is exactly the instruction it will not follow.

**The condition for revisiting it:** only together with a `write_brain` guard against
a destructive shrink — the natural shape is threading `soul_edit`'s existing
`expected_versions` compare-and-swap through `write_brain`, so an edit must name the
version it read and a body that drops content is refused rather than trusted. Without
that guard the elision is a data-loss path wearing an optimisation's clothes; with it,
the 72% is worth re-measuring. Branch `perf/read-brain-is-cheap-when-already-inlined`
is kept, and its tests already pin the digest comparison, the net-win rule and the
"a pointer is never reachable as a body" property.


### Windows validation handoff, 2026-10-04

PR #4469 CI repair validation reproduces the description ratchet on the untouched
72873ee582 baseline as well as the repaired tree: 32985 characters versus the
30100 limit (write_graph: 13547). Both test_onboarding_note's resident-budget
case and test_converse_turn_cost's direct budget case fail. Full modules in an
external detached baseline checkout: 20 passed, 2 failed, 2 skipped, Windows
Python 3.14. Linux oracle Python 3.11 passes the onboarding-note case in its
complete CI shard. No ratchet, tool description, skip or quarantine was changed.
This is handed back to the existing tool-budget investigation, not patched as
part of the sheet CI repair. Evidence is in the local wf-sheet-ci directory's
windows-description-baseline.xml and windows-description-recheck.xml reports.
The unchanged baseline also reproduces
`test_served_tool_guidance::test_moved_chapters_are_gone_from_the_per_round_description`:
write_graph is 13547 characters against its 12000 bound (full module: 18 passed,
1 failed). Its Linux counterpart passes. All three assertions remain intact.

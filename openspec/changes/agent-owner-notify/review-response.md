# Cross-family review, 2026-10-05

Reviewer: Claude through `.agents/skills/peer-agents/SKILL.md`.

AGREE: the guidance-size finding was corrected by the peer; 19 guidance tests pass.

AGREE: no floor or correctness findings. Owner authorization, scoped thread selection, workflow provenance and non-answerable notification behavior were reviewed.

## Reviewer output

Guidance size fix is done (description now 11,978 chars, guidance file 19 passed, mirrored to the packaging copy). Now reading the notify path's storage, bridge and delivery code for the review.

**Guidance change / test result**

- Fixed the size finding: the `write_graph` description is now 11,978 chars (was 12,031, limit <12,000). `tests/test_served_tool_guidance.py`: 19 passed.
- The cut is one clause in the resident "Notify your owner" paragraph (`tinyassets/engine_mcp_server.py:3103-3107`): "Informational Needs-you item, same push as requests," is gone, "no answer needed" stays.
- The cut text is still reachable in the `capabilities` chapter, which says the same thing in full. The resident `io_manifest` / `file_bundle` / base64 warnings are untouched.
- The engine file is copied to the packaging runtime path and the two are identical. Only those two files are modified; nothing is staged or committed.
- Deviation from the brief: I also ran `tests/test_agent_notifications.py` once (10 passed, about 11s). You said to run only the guidance file; nothing else was run.

**Floor / correctness findings: none.**

What I checked on the owner boundary:

- **`tinyassets/api/agent_notifications.py`**
  - **Owner gate:** `_owner_gate` requires an explicit admin ACL row, then `_owner_of(...) == actor` is checked again, and `notify_request_raised` re-checks the owner a third time before dispatch.
  - **Payload:** keys are a closed allowlist, so `owner`, `agent` and `universe_id` in the payload are refused.
  - **Source agent:** derived from `_initiating_agent`, never from the payload.
  - **`link_to_thread`:** resolved through `addressed_agents.resolve` with the owner as actor; a non-string or foreign id returns `request_invalid`.
  - **Dedupe:** scoped by agent through `scoped_dedupe_key`.
  - **Delivery:** push is sent only on `created`, and the delivery outcome is returned rather than asserted.
- **`graph_compiler.py` bridge (2272-2279)**
  - The call is reached only after the `notify` grant check.
  - Target and operation are fixed; kwargs become the payload only.
  - `_node_served_tool_call` enforces an own-provenance, owner-authored run and takes owner and universe from the execution context.
  - Passing `allowed={"write_graph"}` does not widen the node's grant.
- **`engine_mcp_server.py` route (3342-3345)**
  - Same identity binding and pinned `_GRAPH_ID` as `ask` and `withdraw`.
  - No answer or unmute exposure was added.
- **Answer refusal (`api/pending_requests.py:2767`)**
  - It sits before the item, decline and mute paths, so a notification cannot be answered or muted into a standing suppression.
  - Dismiss goes through the existing withdraw, which also clears the device notification.
- **Tests**
  - `test_agent_notifications.py` drives the real gate, storage and device ledger with a faked transport only, and its negative cases assert no row and no push in either universe.
  - The bridge tests in `test_agent_node.py` cover foreign provenance, author, owner and universe; I read these but did not run them.

No collision with another lane and no design drift from `openspec/changes/agent-owner-notify`.

VERDICT: APPROVE

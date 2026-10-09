# The agent learns that a request it sent was answered

## Why

Live 2026-10-06: the founder's agent filed a patch request (GitHub issue #4551,
delivery `54a947b9b92e44a8b515b35506cee6d0`) because it could not connect a
remote MCP server. The fix shipped (#4553) and the issue was closed with a note
naming the new route. Asked again, the agent said "the platform can't attach an
MCP server as a real connection yet", kept its shell workaround
(`bin/deepwiki.py` and a `deepwiki` skill in its own files) and said it was
waiting on its request. Nothing could tell it: a delivery receipt reported only
the receiver's run state, and no answer travelled back.

## What Changes

- **Storage (additive).** `graph_delivery_answers(delivery_id PK -> graph_deliveries,
  outcome 'resolved'|'declined', note, answered_at, noticed_at)` beside the other
  delivery tables, created by the same idempotent schema pass; account deletion
  erases it with its parent receipt.
- **Answer (public MCP, existing target).** `write_graph target=receiver
  operation=answer payload_json={delivery_id, outcome, note}`. Only the delivery's
  receiving owner and command center may answer; anyone else gets the existing
  non-disclosing `receiver_or_link_not_found`. A new answer replaces the old one
  and is told again.
- **Read (public MCP, existing route).** Every receipt carries `outcome`
  (`pending` until answered) and, once answered, `answer {answered_at, note}`
  with the note inside the untrusted envelope. `read_graph target=deliveries`
  lists the caller's own sent deliveries, newest first. `write_graph
  target=patch_request` names this read in its result.
- **Notice (turn context).** The sender's next main-thread turn gets one
  `platform` history notice per newly answered delivery: what was answered, to
  re-check any workaround or belief in its own files that depended on it, and the
  enveloped note. Marked told in the same transaction that reads it. The resident
  prompt is unchanged.

The intake's owner records the answer when they close the loop (for the
founder's intake, when the GitHub issue closes) — through the app agent or the
connector, like any other receiver action. No GitHub-specific code.
